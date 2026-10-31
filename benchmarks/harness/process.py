"""Run benchmark commands and observe process-tree resources externally.

Linux memory uses procfs. macOS memory uses libproc resident-size snapshots.
Linux timing CPU uses cgroup v2 when delegation permits. Otherwise CPU time
is kernel wait4 usage for the workload root. On macOS, that usage
includes descendants reaped through the workload process tree; detached or
unreaped descendants remain outside it. Linux workloads may report direct
reaped-child CPU separately.
"""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Self

from .macos_resource import (
    MacProcessInfo,
    MacProcessMemory,
    read_process_memory,
    read_process_table,
)
from .memory import (
    CgroupV2CpuScope,
    CgroupV2CpuSnapshot,
    CgroupV2MemoryScope,
    CgroupV2MemorySnapshot,
    MemorySample,
    ProcessMemoryMetrics,
    SmapsRollup,
    parse_smaps_rollup,
    sum_rollups,
)

MAC_REAPED_CPU_COVERAGE = (
    "wait4 root plus reaped descendants; detached or unreaped children excluded"
)
CGROUP_TREE_CPU_COVERAGE = "cgroup v2 workload process tree, including descendants"
LINUX_ROOT_CPU_COVERAGE = "incomplete: wait4 root only; descendants excluded"


@dataclass(frozen=True)
class ProcessResult:
    """Command output, status, elapsed time, and optional process-tree data."""

    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_seconds: float
    memory: ProcessMemoryMetrics | None
    cleanup_complete: bool
    remaining_pids: tuple[int, ...]
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    cpu_coverage: str = "unsupported"

    def as_dict(self) -> dict[str, object]:
        """Return JSON-ready status and memory data (output is UTF-8 decoded)."""

        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout.decode("utf-8", errors="replace"),
            "stderr": self.stderr.decode("utf-8", errors="replace"),
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
            "memory": None if self.memory is None else self.memory.as_dict(),
            "cleanup_complete": self.cleanup_complete,
            "remaining_pids": list(self.remaining_pids),
            "cpu_user_seconds": self.cpu_user_seconds,
            "cpu_system_seconds": self.cpu_system_seconds,
            "cpu_coverage": self.cpu_coverage,
        }


@dataclass(frozen=True)
class _WaitResult:
    timed_out: bool
    usage: resource.struct_rusage | None
    mac_memory: MacProcessMemory | None = None
    mac_memory_error: str | None = None


def _wait4(
    process: subprocess.Popen[bytes], timeout: float | None, *,
    capture_macos_memory: bool = False,
) -> _WaitResult:
    """Reap one workload root and return kernel-accounted CPU usage.

    The kernel's treatment of descendants reaped by that root differs across
    supported hosts; callers label the recorded coverage accordingly.
    """
    if not hasattr(os, "wait4"):
        process.wait(timeout=timeout)
        return _WaitResult(False, None)
    deadline = None if timeout is None else time.monotonic() + timeout
    read_zombie = capture_macos_memory and sys.platform == "darwin" and hasattr(os, "waitid")
    while True:
        if read_zombie:
            exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if exited is not None:
                try:
                    mac_memory = read_process_memory(process.pid)
                    error = None
                except (OSError, RuntimeError) as failure:
                    mac_memory = None
                    error = f"root macOS resource counters unavailable: {failure}"
                _pid, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
                return _WaitResult(False, usage, mac_memory, error)
        else:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                return _WaitResult(False, usage)
        if deadline is not None and time.monotonic() >= deadline:
            return _WaitResult(True, None)
        time.sleep(0.005)


def _mac_process_table(root_pid: int, group: int) -> dict[int, MacProcessInfo]:
    return read_process_table(root_pid, group)


def _mac_tree_members(
    table: Mapping[int, MacProcessInfo], root_pid: int, group: int,
) -> dict[int, MacProcessInfo]:
    children: dict[int, list[int]] = {}
    for pid, info in table.items():
        children.setdefault(info.parent_pid, []).append(pid)
    selected = {pid for pid, info in table.items() if info.process_group == group}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            if child not in selected:
                selected.add(child)
                pending.append(child)
    return {pid: table[pid] for pid in selected if pid in table}


def _mac_tree_footprint(
    members: Mapping[int, MacProcessInfo],
    root_pid: int, group: int,
) -> int | None:
    """Sum current footprint ledgers only for a stable observed tree.

    Two rusage reads identify PID reuse, and a second process table checks
    membership and birth times after the reads. This remains a sequential
    sample, not an atomic kernel snapshot; a child that exits during
    collection invalidates the footprint sample instead of silently making
    the tree look smaller.
    """

    try:
        first = {pid: read_process_memory(pid) for pid in members}
        after = _mac_tree_members(_mac_process_table(root_pid, group), root_pid, group)
        if set(after) != set(members):
            return None
        if any(after[pid].parent_pid != members[pid].parent_pid
               or after[pid].process_group != members[pid].process_group
               or after[pid].start_time != members[pid].start_time
               for pid in members):
            return None
        second = {pid: read_process_memory(pid) for pid in members}
    except (OSError, RuntimeError):
        return None
    if any(first[pid].start_abstime != second[pid].start_abstime for pid in members):
        return None
    if any(second[pid].exit_abstime for pid in members):
        return None
    return sum(second[pid].phys_footprint_bytes for pid in members)


@dataclass(frozen=True)
class _ProcInfo:
    pid: int
    parent_pid: int
    process_group: int
    state: str
    start_ticks: int


@dataclass
class _Collected:
    samples: list[MemorySample] = field(default_factory=list)
    phases: dict[str, MemorySample] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    seen: dict[int, int] = field(default_factory=dict)


def _read_proc_info(path: Path) -> _ProcInfo | None:
    """Read stable fields from `/proc/<pid>/stat`; names may contain `)` ."""

    try:
        raw = (path / "stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    try:
        pid = int(raw[: raw.find(" ")])
        fields = raw[close + 1 :].split()
        # Fields start at stat field 3 (state). PPID, process group and start
        # time are fields 4, 5 and 22 respectively.
        return _ProcInfo(
            pid=pid,
            state=fields[0],
            parent_pid=int(fields[1]),
            process_group=int(fields[2]),
            start_ticks=int(fields[19]),
        )
    except (IndexError, ValueError):
        return None


def _proc_table(proc_root: Path) -> dict[int, _ProcInfo]:
    infos: dict[int, _ProcInfo] = {}
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as error:
        raise RuntimeError(f"cannot read procfs at {proc_root}: {error}") from error
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        info = _read_proc_info(entry)
        if info is not None:
            infos[info.pid] = info
    return infos


def _tree_members(
    infos: Mapping[int, _ProcInfo], root_pid: int, process_group: int
) -> dict[int, _ProcInfo]:
    """Find root descendants plus every member of its isolated process group."""

    children: dict[int, list[int]] = {}
    for info in infos.values():
        children.setdefault(info.parent_pid, []).append(info.pid)
    members = {pid: info for pid, info in infos.items() if info.process_group == process_group}
    pending = [root_pid]
    visited = {root_pid}
    while pending:
        parent_pid = pending.pop()
        info = infos.get(parent_pid)
        if info is not None:
            members[parent_pid] = info
        for child_pid in children.get(parent_pid, ()):
            if child_pid not in visited:
                visited.add(child_pid)
                pending.append(child_pid)
    return members


def _read_rollup(proc_root: Path, info: _ProcInfo) -> SmapsRollup | None:
    path = proc_root / str(info.pid) / "smaps_rollup"
    try:
        text = path.read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None
    # Detect PID reuse between stat discovery and the smaps read.
    after = _read_proc_info(proc_root / str(info.pid))
    if after is None or after.start_ticks != info.start_ticks:
        return None
    return parse_smaps_rollup(text)


def _process_is_live(info: _ProcInfo) -> bool:
    return info.state not in {"Z", "X", "x"}


class ProcessSampler:
    """Start a command in its own process group and sample its Linux tree.

    `mark_phase()` captures a named boundary snapshot, for example
    `steady_before_load`, `postload`, or `post_gc`. Call `wait()` after the
    workload; the sampler terminates any process-group descendants the command
    left behind. The context manager also cleans up if the caller raises.
    """

    def __init__(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
        timeout: float | None = None,
        affinity: Sequence[int] | None = None,
        sample_interval_seconds: float | None = 0.015,
        terminate_grace_seconds: float = 0.25,
        proc_root: str | os.PathLike[str] = "/proc",
    ) -> None:
        self.command = _normalize_command(command)
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        if sample_interval_seconds is not None and sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be positive or None")
        if terminate_grace_seconds < 0:
            raise ValueError("terminate_grace_seconds must be non-negative")
        self.timeout = timeout
        self.affinity = _normalize_affinity(affinity)
        self.sample_interval_seconds = sample_interval_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self.proc_root = Path(proc_root)
        self.cwd = None if cwd is None else os.fspath(cwd)
        self.env = None if env is None else dict(env)
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_file: IO[bytes] | None = None
        self._stderr_file: IO[bytes] | None = None
        self._started_at: float | None = None
        self._process_group: int | None = None
        self._stop_sampler = threading.Event()
        self._sampler_thread: threading.Thread | None = None
        self._collected = _Collected()
        self._cgroup_scope: CgroupV2MemoryScope | CgroupV2CpuScope | None = None
        self._cgroup_error: str | None = None
        self._last_cgroup_snapshot: CgroupV2MemorySnapshot | None = None
        self._final_cgroup_snapshot: CgroupV2MemorySnapshot | None = None
        self._final_cpu_snapshot: CgroupV2CpuSnapshot | None = None
        self._remaining_pids: tuple[int, ...] = ()
        self._lock = threading.Lock()
        self._finished_result: ProcessResult | None = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._finished_result is None and self._process is not None:
            self._terminate_process_group()
            self._stop_and_join_sampler()
            self._close_cgroup_scope()
            self._close_output_files()

    def start(self) -> ProcessSampler:
        """Start the command and optional external sampling loop."""

        if self._process is not None:
            raise RuntimeError("process sampler has already been started")
        if sys.platform != "darwin" and not self.proc_root.is_dir():
            raise RuntimeError(f"procfs is not available at {self.proc_root}")

        self._stdout_file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        self._stderr_file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        argv = list(self.command)
        if self.sample_interval_seconds is not None and sys.platform != "darwin":
            self._cgroup_scope = CgroupV2MemoryScope.detect_and_create(
                proc_root=self.proc_root
            )
        elif self.sample_interval_seconds is None and sys.platform == "linux":
            self._cgroup_scope = CgroupV2CpuScope.detect_and_create(
                proc_root=self.proc_root
            )
        set_affinity = self._make_preexec(self._cgroup_scope)
        self._started_at = time.monotonic()
        try:
            self._process = self._launch(argv, set_affinity)
        except subprocess.SubprocessError as error:
            if self._cgroup_scope is None:
                self._close_output_files()
                raise
            self._cgroup_error = f"cgroup attach unavailable: {error}"
            self._close_cgroup_scope()
            assert self._stdout_file is not None and self._stderr_file is not None
            self._stdout_file.seek(0)
            self._stdout_file.truncate()
            self._stderr_file.seek(0)
            self._stderr_file.truncate()
            try:
                self._process = self._launch(argv, self._make_preexec(None))
            except BaseException:
                self._close_output_files()
                raise
        except BaseException:
            self._close_cgroup_scope()
            self._close_output_files()
            raise
        self._process_group = self._process.pid
        if self.sample_interval_seconds is not None:
            # Capture immediately so short but resident commands have a chance
            # to be observed before their first polling interval elapses.
            self._capture_sample()
            self._sampler_thread = threading.Thread(
                target=self._sample_loop,
                name=f"process-memory-{self._process.pid}",
                daemon=True,
            )
            self._sampler_thread.start()
        return self

    def _make_preexec(
        self, cgroup_scope: CgroupV2MemoryScope | CgroupV2CpuScope | None
    ) -> Callable[[], None] | None:
        if self.affinity is None and cgroup_scope is None:
            return None
        if self.affinity is not None and not hasattr(os, "sched_setaffinity"):
            raise RuntimeError("CPU affinity is supported only on Linux")

        def prepare_child() -> None:
            if self.affinity is not None:
                os.sched_setaffinity(0, self.affinity)
            if cgroup_scope is not None:
                cgroup_scope.attach_pid(os.getpid())

        return prepare_child

    def _launch(
        self, argv: list[str], preexec_fn: Callable[[], None] | None
    ) -> subprocess.Popen[bytes]:
        assert self._stdout_file is not None and self._stderr_file is not None
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=self._stdout_file,
            stderr=self._stderr_file,
            cwd=self.cwd,
            env=self.env,
            preexec_fn=preexec_fn,  # noqa: PLW1509
            start_new_session=True,
            close_fds=True,
        )

    def _close_cgroup_scope(self) -> None:
        if self._cgroup_scope is None:
            return
        self._cgroup_scope.close()
        if self._cgroup_scope.cleanup_error is not None:
            self._cgroup_error = self._cgroup_scope.cleanup_error
        self._cgroup_scope = None

    @property
    def pid(self) -> int:
        if self._process is None:
            raise RuntimeError("process sampler has not been started")
        return self._process.pid

    def mark_phase(self, name: str) -> MemorySample | None:
        """Record the latest tree state under a caller-defined phase name."""

        if self._process is None:
            raise RuntimeError("process sampler has not been started")
        if not name or not name.strip():
            raise ValueError("phase name must not be empty")
        if self.sample_interval_seconds is None:
            return None
        sample = self._capture_sample()
        if sample is not None:
            with self._lock:
                self._collected.phases[name] = sample
        return sample

    def wait(self) -> ProcessResult:
        """Wait for completion or timeout, clean remaining descendants, return."""

        if self._process is None or self._started_at is None:
            raise RuntimeError("process sampler has not been started")
        if self._finished_result is not None:
            return self._finished_result

        wait_result = _WaitResult(False, None)
        boundary_remaining: tuple[int, ...] = ()
        try:
            wait_result = _wait4(
                self._process, self.timeout,
                capture_macos_memory=self.sample_interval_seconds is not None,
            )
            # Preserve the workload boundary before stopping the sampler or
            # cleaning leaked descendants; cleanup time is not command time.
            if self.sample_interval_seconds is not None:
                self._capture_sample()
            boundary_remaining = self._live_process_ids()
            duration = time.monotonic() - self._started_at
        finally:
            self._stop_and_join_sampler()
            try:
                self._terminate_process_group()
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                # An unavailable process table cannot prove cleanup. If the
                # root is still live, terminate its group before surfacing
                # the inspection failure; never report an empty tree.
                if self._process.poll() is None and self._process_group is not None:
                    self._signal_process_group(signal.SIGKILL)
                    self._process.wait()
                self._close_output_files()
                raise
            finally:
                if isinstance(self._cgroup_scope, CgroupV2CpuScope):
                    try:
                        self._final_cpu_snapshot = self._cgroup_scope.read_snapshot()
                    except (OSError, ValueError):
                        self._final_cpu_snapshot = None
                else:
                    self._final_cgroup_snapshot = self._read_cgroup_snapshot()
                self._close_cgroup_scope()

        assert self._stdout_file is not None and self._stderr_file is not None
        self._stdout_file.seek(0)
        self._stderr_file.seek(0)
        stdout = self._stdout_file.read()
        stderr = self._stderr_file.read()
        self._close_output_files()
        memory = None
        if self.sample_interval_seconds is not None:
            with self._lock:
                memory = ProcessMemoryMetrics.from_samples(self._collected.samples)
                memory.phase_samples.update(self._collected.phases)
                memory.sampling_errors.extend(self._collected.errors)
            cgroup_snapshot = self._final_cgroup_snapshot or self._last_cgroup_snapshot
            if cgroup_snapshot is not None:
                memory.cgroup_current_bytes = cgroup_snapshot.current_bytes
                memory.cgroup_peak_bytes = max(
                    memory.cgroup_peak_bytes or 0, cgroup_snapshot.peak_bytes
                )
                memory.cgroup_events = dict(cgroup_snapshot.events)
            memory.cgroup_error = self._cgroup_error
            if sys.platform == "darwin":
                if wait_result.usage is not None:
                    memory.root_kernel_peak_rss_bytes = wait_result.usage.ru_maxrss
                    memory.peak_rss_bytes = max(memory.peak_rss_bytes, wait_result.usage.ru_maxrss)
                if wait_result.mac_memory is not None:
                    memory.root_kernel_peak_phys_footprint_bytes = (
                        wait_result.mac_memory.lifetime_max_phys_footprint_bytes
                    )
                if wait_result.mac_memory_error is not None:
                    memory.sampling_errors.append(wait_result.mac_memory_error)
        cpu_user = None if wait_result.usage is None else wait_result.usage.ru_utime
        cpu_system = None if wait_result.usage is None else wait_result.usage.ru_stime
        cpu_coverage = (
            "unsupported" if wait_result.usage is None else
            "incomplete: workload left descendants at completion" if boundary_remaining else
            MAC_REAPED_CPU_COVERAGE if sys.platform == "darwin" else
            LINUX_ROOT_CPU_COVERAGE
        )
        if (self._final_cpu_snapshot is not None and wait_result.usage is not None
                and not boundary_remaining and not self._remaining_pids
                and self._cgroup_error is None):
            cpu_user = self._final_cpu_snapshot.user_usec / 1_000_000
            cpu_system = self._final_cpu_snapshot.system_usec / 1_000_000
            cpu_coverage = CGROUP_TREE_CPU_COVERAGE
        result = ProcessResult(
            command=self.command,
            returncode=self._process.returncode if self._process.returncode is not None else 0,
            stdout=stdout,
            stderr=stderr,
            timed_out=wait_result.timed_out,
            duration_seconds=duration,
            memory=memory,
            cleanup_complete=not self._remaining_pids,
            remaining_pids=self._remaining_pids,
            cpu_user_seconds=cpu_user,
            cpu_system_seconds=cpu_system,
            cpu_coverage=cpu_coverage,
        )
        self._finished_result = result
        return result

    def _sample_loop(self) -> None:
        assert self.sample_interval_seconds is not None
        while not self._stop_sampler.is_set():
            self._capture_sample()
            self._stop_sampler.wait(self.sample_interval_seconds)

    def _capture_sample(self) -> MemorySample | None:
        if self._started_at is None or self._process is None or self._process_group is None:
            return None
        if sys.platform == "darwin":
            try:
                members = _mac_tree_members(
                    _mac_process_table(self._process.pid, self._process_group),
                    self._process.pid, self._process_group
                )
            except (OSError, RuntimeError) as error:
                self._record_error(str(error))
                return None
            if not members:
                return None
            with self._lock:
                self._collected.seen.update({pid: 0 for pid in members})
            footprint = _mac_tree_footprint(
                members, self._process.pid, self._process_group
            )
            sample = MemorySample(
                elapsed_seconds=time.monotonic() - self._started_at,
                rss_bytes=sum(info.resident_bytes for info in members.values()),
                pss_bytes=None,
                private_bytes=None,
                swap_bytes=None,
                process_count=len(members),
                pids=tuple(sorted(members)),
                phys_footprint_bytes=footprint,
            )
            with self._lock:
                self._collected.samples.append(sample)
            return sample
        try:
            infos = _proc_table(self.proc_root)
        except RuntimeError as error:
            self._record_error(str(error))
            return None
        members = _tree_members(infos, self._process.pid, self._process_group)
        rollups: dict[int, SmapsRollup] = {}
        for pid, info in members.items():
            with self._lock:
                self._collected.seen[pid] = info.start_ticks
            try:
                rollup = _read_rollup(self.proc_root, info)
            except (OSError, ValueError):
                rollup = self._retry_unreadable_rollup(info)
            if rollup is not None:
                rollups[pid] = rollup
        if not rollups:
            return None
        sample = sum_rollups(rollups, time.monotonic() - self._started_at)
        cgroup_snapshot = self._read_cgroup_snapshot()
        if cgroup_snapshot is not None:
            sample = replace(
                sample,
                cgroup_current_bytes=cgroup_snapshot.current_bytes,
                cgroup_peak_bytes=cgroup_snapshot.peak_bytes,
            )
        with self._lock:
            self._collected.samples.append(sample)
        return sample

    def _retry_unreadable_rollup(self, info: _ProcInfo) -> SmapsRollup | None:
        if not _process_is_live(info):
            return None
        current = _read_proc_info(self.proc_root / str(info.pid))
        if (
            current is None
            or current.start_ticks != info.start_ticks
            or not _process_is_live(current)
        ):
            return None
        try:
            return _read_rollup(self.proc_root, current)
        except (OSError, ValueError):
            latest = _read_proc_info(self.proc_root / str(info.pid))
            if (
                latest is not None
                and latest.start_ticks == info.start_ticks
                and _process_is_live(latest)
            ):
                self._record_error(f"pid {info.pid}: smaps_rollup unavailable")
            return None

    def _read_cgroup_snapshot(self) -> CgroupV2MemorySnapshot | None:
        if not isinstance(self._cgroup_scope, CgroupV2MemoryScope):
            return None
        try:
            snapshot = self._cgroup_scope.read_snapshot()
        except (OSError, ValueError) as error:
            if self._cgroup_error is None:
                self._cgroup_error = f"cgroup memory counters unavailable: {error}"
            return None
        self._last_cgroup_snapshot = snapshot
        return snapshot

    def _record_error(self, message: str) -> None:
        with self._lock:
            if message not in self._collected.errors:
                self._collected.errors.append(message)

    def _stop_and_join_sampler(self) -> None:
        self._stop_sampler.set()
        if self._sampler_thread is not None:
            self._sampler_thread.join()
            self._sampler_thread = None

    def _terminate_process_group(self) -> None:
        if self._process is None or self._process_group is None:
            return
        if self._has_live_processes():
            self._signal_process_group(signal.SIGTERM)
        self._signal_seen_processes(signal.SIGTERM)
        deadline = time.monotonic() + self.terminate_grace_seconds
        while time.monotonic() < deadline and self._has_live_processes():
            if self._process.poll() is None:
                self._process.poll()
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        if self._has_live_processes():
            self._signal_process_group(signal.SIGKILL)
            # Kill any previously discovered descendants that escaped their
            # parent's session/process group, guarding against PID reuse.
            self._signal_seen_processes(signal.SIGKILL)
            kill_deadline = time.monotonic() + max(1.0, self.terminate_grace_seconds)
            while time.monotonic() < kill_deadline and self._has_live_processes():
                time.sleep(0.01)
        if self._process.poll() is None:
            try:
                self._process.wait(timeout=max(0.1, self.terminate_grace_seconds))
            except subprocess.TimeoutExpired:
                self._signal_process_group(signal.SIGKILL)
                self._process.wait()
        self._remaining_pids = self._live_process_ids()

    def _signal_process_group(self, sig: signal.Signals) -> None:
        assert self._process_group is not None
        if sys.platform == "darwin":
            try:
                os.killpg(self._process_group, sig)
            except ProcessLookupError:
                pass
            return
        if self._process is not None and self._process.poll() is None:
            try:
                os.killpg(self._process_group, sig)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                self._record_error(
                    f"cannot signal process group {self._process_group}: {error}"
                )
            return
        # Once the root has exited, signal live group members by verified PID
        # and start time. This avoids a late killpg racing with reuse of the
        # former process-group ID.
        try:
            infos = _proc_table(self.proc_root)
        except RuntimeError:
            return
        for info in infos.values():
            if info.process_group != self._process_group or not _process_is_live(info):
                continue
            current = _read_proc_info(self.proc_root / str(info.pid))
            if current is None or current.start_ticks != info.start_ticks:
                continue
            try:
                os.kill(info.pid, sig)
            except (ProcessLookupError, PermissionError):
                continue

    def _signal_seen_processes(self, sig: signal.Signals) -> None:
        if sys.platform == "darwin":
            return
        with self._lock:
            seen = tuple(self._collected.seen.items())
        for pid, start_ticks in seen:
            info = _read_proc_info(self.proc_root / str(pid))
            if info is None or info.start_ticks != start_ticks or not _process_is_live(info):
                continue
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                continue

    def _has_live_processes(self) -> bool:
        return bool(self._live_process_ids())

    def _live_process_ids(self) -> tuple[int, ...]:
        if sys.platform == "darwin":
            assert self._process_group is not None
            table = _mac_process_table(self.pid, self._process_group)
            return tuple(sorted(_mac_tree_members(table, self.pid, self._process_group)))
        live: set[int] = set()
        try:
            infos = _proc_table(self.proc_root)
        except RuntimeError:
            infos = {}
        if self._process_group is not None:
            live.update(
                info.pid
                for info in infos.values()
                if info.process_group == self._process_group and _process_is_live(info)
            )
        with self._lock:
            seen = tuple(self._collected.seen.items())
        for pid, start_ticks in seen:
            info = _read_proc_info(self.proc_root / str(pid))
            if info is not None and info.start_ticks == start_ticks and _process_is_live(info):
                live.add(pid)
        return tuple(sorted(live))

    def _close_output_files(self) -> None:
        for stream_name in ("_stdout_file", "_stderr_file"):
            stream = getattr(self, stream_name)
            if stream is not None:
                stream.close()
                setattr(self, stream_name, None)


def _normalize_command(command: Sequence[str | os.PathLike[str]]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be a sequence of arguments, not a string")
    normalized = tuple(os.fspath(argument) for argument in command)
    if not normalized:
        raise ValueError("command must contain at least one argument")
    if any(not isinstance(argument, str) or "\0" in argument for argument in normalized):
        raise ValueError("command arguments must be NUL-free strings")
    return normalized


def _normalize_affinity(affinity: Sequence[int] | None) -> tuple[int, ...] | None:
    if affinity is None:
        return None
    raw_cpus = tuple(affinity)
    if not raw_cpus or any(type(cpu) is not int or cpu < 0 for cpu in raw_cpus):
        raise ValueError("affinity must contain one or more non-negative CPU ids")
    cpus = tuple(sorted(set(raw_cpus)))
    if not hasattr(os, "sched_getaffinity"):
        raise RuntimeError("CPU affinity is supported only on Linux")
    allowed = os.sched_getaffinity(0)
    unavailable = sorted(set(cpus) - allowed)
    if unavailable:
        raise ValueError(f"requested CPUs are outside the current affinity: {unavailable}")
    return cpus


def run_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
    affinity: Sequence[int] | None = None,
    sample_interval_seconds: float | None = 0.015,
    terminate_grace_seconds: float = 0.25,
) -> ProcessResult:
    """Run any command in a fresh process group and collect its output.

    Supply `sample_interval_seconds=None` for timing passes. Memory passes
    should use an interval near 0.01–0.02 seconds. `env` follows subprocess
    semantics and replaces the inherited environment when provided. Affinity
    is applied in the forked child before target code runs.
    """

    if sys.platform == "darwin" and sample_interval_seconds is None:
        if affinity is not None:
            raise RuntimeError("CPU affinity is not supported by the macOS timing runner")
        return _run_unmonitored(
            command,
            env=env,
            cwd=cwd,
            timeout=timeout,
            terminate_grace_seconds=terminate_grace_seconds,
        )

    sampler = ProcessSampler(
        command,
        env=env,
        cwd=cwd,
        timeout=timeout,
        affinity=affinity,
        sample_interval_seconds=sample_interval_seconds,
        terminate_grace_seconds=terminate_grace_seconds,
    ).start()
    return sampler.wait()


def _run_unmonitored(
    command: Sequence[str | os.PathLike[str]],
    *,
    env: Mapping[str, str] | None,
    cwd: str | os.PathLike[str] | None,
    timeout: float | None,
    terminate_grace_seconds: float,
) -> ProcessResult:
    """Run a timing-only macOS command and clean its process group."""
    normalized = _normalize_command(command)
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be non-negative or None")
    if terminate_grace_seconds < 0:
        raise ValueError("terminate_grace_seconds must be non-negative")
    ps = shutil.which("ps")
    if ps is None:
        raise RuntimeError("ps is required to verify macOS benchmark process cleanup")

    with (
        tempfile.TemporaryFile(mode="w+b") as stdout_file,
        tempfile.TemporaryFile(mode="w+b") as stderr_file,
    ):
        started = time.monotonic()
        process = subprocess.Popen(
            normalized,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            cwd=None if cwd is None else os.fspath(cwd),
            env=None if env is None else dict(env),
            start_new_session=True,
            close_fds=True,
        )
        wait_result = _wait4(process, timeout)
        duration = time.monotonic() - started

        def group_pids() -> tuple[int, ...]:
            result = subprocess.run(
                [ps, "-A", "-o", "pid=,pgid=,stat="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"cannot inspect process groups with ps: {result.stderr.strip()}")
            pids = []
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) < 3:
                    continue
                try:
                    pid, process_group = int(fields[0]), int(fields[1])
                except ValueError:
                    continue
                if process_group == process.pid and not fields[2].startswith("Z"):
                    pids.append(pid)
            return tuple(sorted(pids))

        def signal_group(sig: signal.Signals) -> None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                raise RuntimeError(f"cannot signal benchmark process group {process.pid}: {error}") from error

        remaining = group_pids()
        boundary_remaining = remaining
        if remaining:
            signal_group(signal.SIGTERM)
            deadline = time.monotonic() + terminate_grace_seconds
            while remaining and time.monotonic() < deadline:
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
                remaining = group_pids()
            if remaining:
                signal_group(signal.SIGKILL)
                deadline = time.monotonic() + max(1.0, terminate_grace_seconds)
                while remaining and time.monotonic() < deadline:
                    time.sleep(0.01)
                    remaining = group_pids()
        if process.poll() is None:
            signal_group(signal.SIGKILL)
            process.wait()

        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()
    return ProcessResult(
        command=normalized,
        returncode=process.returncode if process.returncode is not None else 0,
        stdout=stdout,
        stderr=stderr,
        timed_out=wait_result.timed_out,
        duration_seconds=duration,
        memory=None,
        cleanup_complete=not remaining,
        remaining_pids=remaining,
        cpu_user_seconds=None if wait_result.usage is None else wait_result.usage.ru_utime,
        cpu_system_seconds=None if wait_result.usage is None else wait_result.usage.ru_stime,
        cpu_coverage=("unsupported" if wait_result.usage is None else
                      "incomplete: workload left descendants at completion" if boundary_remaining else
                      MAC_REAPED_CPU_COVERAGE if sys.platform == "darwin" else
                      "wait4 root only; descendants excluded"),
    )
