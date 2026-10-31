"""Integration tests for command execution and Linux process-tree cleanup."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from benchmarks.harness import process as process_module
from benchmarks.harness.memory import CgroupV2MemoryScope
from benchmarks.harness.process import ProcessSampler, _run_unmonitored, run_command


def _process_state(pid: int) -> str | None:
    try:
        stat_line = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    close = stat_line.rfind(")")
    if close < 0:
        return "?"
    fields = stat_line[close + 1 :].split()
    return fields[0] if fields else "?"


def _wait_until_not_live(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _process_state(pid)
        if state is None or state in {"Z", "X", "x"}:
            return True
        time.sleep(0.01)
    return False


def _wait_until_not_running(pid: int, timeout: float = 2.0) -> bool:
    ps = shutil.which("ps")
    if ps is None:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        if result.returncode != 0 or not state or state.startswith("Z"):
            return True
        time.sleep(0.01)
    return False


class UnmonitoredProcessTests(unittest.TestCase):
    def test_timing_command_collects_output_without_memory_sampling(self):
        result = _run_unmonitored(
            [sys.executable, "-c", "print('timing-only')"],
            env=None,
            cwd=None,
            timeout=3,
            terminate_grace_seconds=0.1,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"timing-only\n")
        self.assertIsNone(result.memory)
        self.assertTrue(result.cleanup_complete)
        self.assertGreater(result.duration_seconds, 0)

    def test_cpu_usage_includes_waited_grandchild(self):
        code = (
            "import subprocess,sys; "
            "subprocess.run([sys.executable, '-c', 'sum(range(12000000))'], check=True)"
        )
        result = run_command(
            [sys.executable, "-c", code], timeout=5, sample_interval_seconds=None
        )
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(result.cpu_user_seconds)
        assert result.cpu_user_seconds is not None
        self.assertGreater(result.cpu_user_seconds, 0.05)
        self.assertGreaterEqual(result.cpu_system_seconds or 0, 0)

    def test_timing_command_cleans_descendant_after_parent_exits(self):
        parent_code = (
            "import subprocess,sys; "
            "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(child.pid, flush=True)"
        )
        result = _run_unmonitored(
            [sys.executable, "-c", parent_code],
            env=None,
            cwd=None,
            timeout=3,
            terminate_grace_seconds=0.1,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        child_pid = int(result.stdout.decode("ascii").strip())
        self.assertTrue(_wait_until_not_running(child_pid), f"child {child_pid} survived cleanup")
        self.assertTrue(result.cleanup_complete)
        self.assertEqual(result.remaining_pids, ())


@unittest.skipUnless(sys.platform == "darwin", "native macOS RSS sampler")
class MacProcessSamplerTests(unittest.TestCase):
    def test_short_root_peak_survives_missing_ps_samples(self):
        code = (
            "data=bytearray(32*1024*1024); "
            "data[::4096]=b'x'*len(data[::4096])"
        )
        with patch("benchmarks.harness.process._mac_process_table", return_value={}):
            result = run_command(
                [sys.executable, "-c", code], timeout=5,
                sample_interval_seconds=0.02,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        assert result.memory is not None
        self.assertEqual(result.memory.samples, [])
        self.assertGreater(result.memory.root_kernel_peak_rss_bytes or 0, 32 * 1024 * 1024)
        self.assertGreater(
            result.memory.root_kernel_peak_phys_footprint_bytes or 0,
            16 * 1024 * 1024,
        )
        self.assertEqual(result.memory.peak_rss_bytes,
                         result.memory.root_kernel_peak_rss_bytes)

    def test_process_table_failure_cannot_report_clean_memory_run(self):
        with patch("benchmarks.harness.process._mac_process_table",                    side_effect=RuntimeError("ps unavailable")), self.assertRaisesRegex(RuntimeError, "ps unavailable"):
            run_command(
                [sys.executable, "-c", "pass"], timeout=3,
                sample_interval_seconds=0.02,
            )

    def test_external_tree_rss_counts_child_and_marks_unavailable_metrics(self):
        code = (
            "import subprocess,sys; "
            "subprocess.run([sys.executable, '-c', "
            "'import time; x=bytearray(32*1024*1024); time.sleep(0.3)'], check=True)"
        )
        result = run_command(
            [sys.executable, "-c", code], timeout=5,
            sample_interval_seconds=0.02,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        assert result.memory is not None
        self.assertGreater(result.memory.peak_rss_bytes, 20 * 1024 * 1024)
        self.assertGreaterEqual(result.memory.peak_process_count, 2)
        self.assertIsNone(result.memory.peak_pss_bytes)
        self.assertIsNone(result.memory.peak_private_bytes)
        self.assertGreater(result.cpu_user_seconds or 0, 0)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc sampler")
class ProcessSamplerTests(unittest.TestCase):
    def test_memory_peak_observes_touched_child_allocation(self):
        code = (
            "import time; data=bytearray(64*1024*1024); "
            "[data.__setitem__(i, 1) for i in range(0, len(data), 4096)]; "
            "time.sleep(0.3)"
        )
        result = run_command(
            [sys.executable, "-c", code],
            timeout=5,
            sample_interval_seconds=0.01,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertFalse(result.timed_out)
        self.assertIsNotNone(result.memory)
        assert result.memory is not None
        self.assertGreater(result.memory.peak_rss_bytes, 32 * 1024 * 1024)
        self.assertGreater(result.memory.peak_pss_bytes, 32 * 1024 * 1024)
        self.assertGreaterEqual(len(result.memory.samples), 2)

    def test_descendant_memory_is_counted_and_timeout_cleans_process_group(self):
        parent_code = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(child.pid, flush=True); time.sleep(30)"
        )
        result = run_command(
            [sys.executable, "-c", parent_code],
            timeout=0.3,
            sample_interval_seconds=0.01,
            terminate_grace_seconds=0.1,
        )

        self.assertTrue(result.timed_out)
        self.assertTrue(result.cleanup_complete)
        self.assertEqual(result.remaining_pids, ())
        self.assertIsNotNone(result.memory)
        assert result.memory is not None
        self.assertGreaterEqual(result.memory.peak_process_count, 2)
        child_pid = int(result.stdout.decode("ascii").strip())
        self.assertTrue(_wait_until_not_live(child_pid), f"child {child_pid} survived cleanup")

    def test_affinity_and_environment_are_applied_before_target_runs(self):
        allowed = sorted(os.sched_getaffinity(0))
        chosen_cpu = allowed[0]
        result = run_command(
            [
                sys.executable,
                "-c",
                "import os; print(min(os.sched_getaffinity(0))); print(os.environ['BENCH_MARK'])",
            ],
            env={"PATH": os.environ.get("PATH", ""), "BENCH_MARK": "ready"},
            affinity=[chosen_cpu],
            sample_interval_seconds=None,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(result.cleanup_complete)
        self.assertEqual(result.stdout.decode().splitlines(), [str(chosen_cpu), "ready"])
        self.assertIsNone(result.memory)
        self.assertTrue(result.as_dict()["duration_seconds"] > 0)

    def test_affinity_launcher_does_not_inflate_target_memory(self):
        sleep = shutil.which("sleep")
        if sleep is None:
            self.skipTest("sleep command unavailable")
        result = run_command(
            [sleep, "0.12"],
            affinity=[min(os.sched_getaffinity(0))],
            timeout=2,
            sample_interval_seconds=0.01,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(result.memory)
        assert result.memory is not None
        self.assertLess(result.memory.peak_pss_bytes, 2 * 1024 * 1024)

    def test_timing_pass_skips_cgroup_setup(self):
        with patch("benchmarks.harness.process.CgroupV2MemoryScope.detect_and_create") as detect:
            result = run_command(
                [sys.executable, "-c", "print('timing')"],
                sample_interval_seconds=None,
            )

        self.assertEqual(result.stdout, b"timing\n")
        self.assertIsNone(result.memory)
        detect.assert_not_called()

    def test_unreadable_rollup_is_retried_once(self):
        real = process_module._read_rollup
        calls = []
        def first_denied(proc_root, info):
            if not calls:
                calls.append(info.pid)
                self.assertTrue(process_module._process_is_live(info))
                raise PermissionError(13, "denied")
            return real(proc_root, info)
        with patch("benchmarks.harness.process._read_rollup", side_effect=first_denied):
            result = run_command(
                [sys.executable, "-c", "import time; time.sleep(0.2)"],
                timeout=3,
                sample_interval_seconds=0.01,
            )
        assert result.memory is not None
        self.assertEqual(result.memory.sampling_errors, [])
        self.assertTrue(result.memory.samples)

    def test_rollup_read_that_stays_unavailable_fails_closed(self):
        with patch("benchmarks.harness.process._read_rollup",
                   side_effect=PermissionError(13, "denied")):
            result = run_command(
                [sys.executable, "-c", "import time; time.sleep(0.2)"],
                timeout=3,
                sample_interval_seconds=0.01,
            )
        assert result.memory is not None
        self.assertTrue(any("smaps_rollup unavailable" in error
                            for error in result.memory.sampling_errors))

    def test_vanished_rollup_is_treated_as_gone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pid = 4242
            (root / str(pid)).mkdir()
            (root / str(pid) / "stat").write_text(
                f"{pid} (python) R 1 {pid} 0 0 0 0 0 0 0 0 0 0 0 0 20 0 1 0 12345",
                encoding="ascii",
            )
            info = process_module._read_proc_info(root / str(pid))
            self.assertIsNotNone(info)
            self.assertIsNone(process_module._read_rollup(root, info))

    def test_unavailable_cgroup_falls_back_to_procfs_metrics(self):
        with patch(
            "benchmarks.harness.process.CgroupV2MemoryScope.detect_and_create",
            return_value=None,
        ):
            result = run_command(
                [sys.executable, "-c", "import time; time.sleep(0.1)"],
                timeout=3,
                sample_interval_seconds=0.01,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(result.memory)
        assert result.memory is not None
        self.assertGreater(result.memory.peak_pss_bytes, 0)
        self.assertIsNone(result.memory.cgroup_current_bytes)
        self.assertIsNone(result.memory.cgroup_peak_bytes)
        self.assertIsNone(result.memory.cgroup_events)

    def test_cgroup_attach_denial_retries_without_cgroup(self):
        scope = Mock(spec=CgroupV2MemoryScope)
        scope.attach_pid.side_effect = PermissionError("delegation revoked")
        scope.cleanup_error = None
        with patch(
            "benchmarks.harness.process.CgroupV2MemoryScope.detect_and_create",
            return_value=scope,
        ):
            result = run_command(
                [sys.executable, "-c", "print('ran once')"],
                timeout=3,
                sample_interval_seconds=0.01,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"ran once\n")
        self.assertIsNotNone(result.memory)
        assert result.memory is not None
        self.assertIsNone(result.memory.cgroup_peak_bytes)
        self.assertIn("cgroup attach unavailable", result.memory.cgroup_error or "")
        scope.close.assert_called_once()

    def test_cgroup_scope_is_attached_before_target_exec_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope_path = Path(tmp) / "cgroup"
            scope_path.mkdir()
            (scope_path / "cgroup.procs").write_text("", encoding="ascii")
            (scope_path / "memory.current").write_text("1024\n", encoding="ascii")
            (scope_path / "memory.peak").write_text("4096\n", encoding="ascii")
            (scope_path / "memory.events").write_text("low 0\nmax 0\noom 0\n", encoding="ascii")
            scope = CgroupV2MemoryScope(scope_path)
            with (
                patch(
                    "benchmarks.harness.process.CgroupV2MemoryScope.detect_and_create",
                    return_value=scope,
                ),
                patch("benchmarks.harness.memory._remove_cgroup_directory"),
            ):
                sampler = ProcessSampler(
                    [sys.executable, "-c", "import time; time.sleep(0.1)"],
                    timeout=3,
                    sample_interval_seconds=0.01,
                ).start()
                target_pid = sampler.pid
                result = sampler.wait()

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                (scope_path / "cgroup.procs").read_text(encoding="ascii"),
                f"{target_pid}\n",
            )
            self.assertIsNotNone(result.memory)
            assert result.memory is not None
            self.assertEqual(result.memory.cgroup_current_bytes, 1024)
            self.assertEqual(result.memory.cgroup_peak_bytes, 4096)
            self.assertEqual(result.memory.cgroup_events, {"low": 0, "max": 0, "oom": 0})

    def test_normal_exit_kills_a_leftover_child(self):
        parent_code = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(child.pid, flush=True)"
        )
        result = run_command(
            [sys.executable, "-c", parent_code],
            timeout=5,
            sample_interval_seconds=0.01,
            terminate_grace_seconds=0.1,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        child_pid = int(result.stdout.decode("ascii").strip())
        self.assertTrue(_wait_until_not_live(child_pid), f"child {child_pid} survived cleanup")

    def test_caller_can_mark_postload_memory_phase(self):
        sampler = ProcessSampler(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            timeout=3,
            sample_interval_seconds=0.01,
        ).start()
        time.sleep(0.05)
        phase = sampler.mark_phase("postload")
        result = sampler.wait()

        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.cleanup_complete)
        self.assertIsNotNone(phase)
        self.assertIsNotNone(result.memory)
        assert result.memory is not None
        self.assertIsNotNone(result.memory.postload_pss_bytes)
        self.assertIn("postload", result.memory.phase_samples)


if __name__ == "__main__":
    unittest.main()
