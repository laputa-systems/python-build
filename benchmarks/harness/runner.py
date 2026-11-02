"""Paired, isolated passes over repository-owned application workloads.

Timing uses an uninstrumented child. Memory and allocation collection run
the same semantic operation in fresh children; their elapsed times are never
used as timing observations.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from benchmarks.harness.environment import interpreter_environment
from benchmarks.harness.process import (
    CGROUP_TREE_CPU_COVERAGE,
    KERNEL_REGION_CPU_COVERAGE,
    LINUX_ROOT_CPU_COVERAGE,
    MAC_REAPED_CPU_COVERAGE,
    run_command,
)
from benchmarks.workloads.registry import Workload

PROFILE_ROUNDS = {
    "quick": (2, 2),
    "standard": (5, 3),
    "rigorous": (10, 5),
}


def workload_command(python: Path, workload: Workload, iterations: int, *, memory: bool = False) -> list[str]:
    if workload.name == "python_startup":
        if memory:
            # The near-empty interpreter would otherwise exit between 10 ms
            # smaps polls. Hold its initialized address space long enough to
            # observe PSS; only the separate timing pass measures startup.
            return [str(python), "-c", "import time; time.sleep(0.12)"]
        return [str(python), "-c", "pass"]
    return [
        str(python), "-m", f"benchmarks.workloads.{workload.module}",
        workload.name, "--iterations", str(iterations),
    ]


def workload_environment(
    site_packages: Path | None,
    *,
    wheelhouse: Path | None = None,
    pip_packages: tuple[str, ...] = (),
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    env = interpreter_environment()
    env.pop("BENCH_DJANGO_PREPARE_PATH", None)
    env.pop("BENCH_DJANGO_FIXTURE_PATH", None)
    paths = [str(root)]
    if site_packages is not None:
        paths.insert(0, str(site_packages))
    env.update({
        "PYTHONPATH": os.pathsep.join(paths),
        "PYTHONHASHSEED": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONMALLOC": "default",
    })
    if wheelhouse is not None:
        env["BENCH_WHEELHOUSE"] = str(wheelhouse)
    if pip_packages:
        env["BENCH_PIP_PACKAGES"] = " ".join(pip_packages)
    if extra:
        env.update(extra)
    return env


def _measurement_payload(stdout: str, workload: Workload, iterations: int) -> dict[str, Any]:
    if workload.name == "python_startup":
        return {"operation_count": 1, "digest": "empty-interpreter"}
    lines = stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"{workload.name}: missing correctness payload")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{workload.name}: invalid correctness payload: {lines[-1]!r}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("operation_count"), int):
        raise RuntimeError(f"{workload.name}: missing operation count")  # noqa: TRY004
    if payload["operation_count"] < iterations or not isinstance(payload.get("digest"), str):
        raise RuntimeError(f"{workload.name}: failed correctness payload")
    if not payload["digest"]:
        raise RuntimeError(f"{workload.name}: empty correctness digest")
    return payload


def _timing_elapsed(measured: Any, payload: dict[str, Any], workload: Workload) -> float:
    if workload.timing_boundary == "process":
        elapsed = measured.duration_seconds
    elif workload.timing_boundary == "internal":
        elapsed = payload.get("elapsed_seconds")
    else:
        raise ValueError(f"{workload.name}: unknown timing boundary {workload.timing_boundary!r}")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0:
        raise RuntimeError(f"{workload.name}: invalid elapsed time")
    return float(elapsed)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_django_first_request_fixture(
    python: Path, env: dict[str, str], output_dir: Path, timeout_seconds: float,
    affinity: set[int] | None,
) -> tuple[Path, str]:
    fixture = output_dir / "django-first-request.sqlite3"
    if fixture.exists():
        raise FileExistsError(f"Django fixture already exists: {fixture}")
    prepared = run_command(
        [str(python), "-m", "benchmarks.workloads.django", "--prepare-fixture", str(fixture)],
        env=env, cwd=Path(__file__).resolve().parents[2], timeout=timeout_seconds,
        affinity=affinity, sample_interval_seconds=None,
    )
    (output_dir / "fixture-preparation.json").write_text(
        json.dumps(prepared.as_dict(), indent=2) + "\n"
    )
    if not prepared.cleanup_complete or prepared.remaining_pids or prepared.returncode != 0 or prepared.timed_out:
        raise RuntimeError(f"Django fixture preparation failed: {prepared.stderr[-2000:]}")
    try:
        declared = json.loads(prepared.stdout.decode("utf-8"))["fixture_sha256"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("Django fixture preparation returned no digest") from exc
    actual = _file_sha256(fixture)
    if declared != actual:
        raise RuntimeError("Django fixture changed during preparation")
    fixture.chmod(0o444)
    return fixture, actual


def _memory_dict(value: Any) -> dict[str, Any]:
    if value is None:
        raise RuntimeError("memory sampler returned no memory metrics")
    if hasattr(value, "as_dict"):
        value = value.as_dict()
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if not isinstance(value, dict):
        raise TypeError("memory sampler returned unexpected type")
    return {
        "peak_pss": value.get("peak_pss_bytes"),
        "peak_rss": value.get("peak_rss_bytes"),
        "peak_rss_coverage": value.get("peak_rss_coverage"),
        "peak_phys_footprint": value.get("peak_phys_footprint_bytes"),
        "phys_footprint_coverage": value.get("phys_footprint_coverage"),
        "root_kernel_peak_rss": value.get("root_kernel_peak_rss_bytes"),
        "root_kernel_peak_phys_footprint": value.get("root_kernel_peak_phys_footprint_bytes"),
        "peak_private": value.get("peak_private_bytes"),
        "peak_swap": value.get("peak_swap_bytes"),
        "process_count": value.get("peak_process_count"),
        "steady_pss": value.get("steady_pss_bytes"),
        "steady_rss": value.get("steady_rss_bytes"),
        "postload_pss": value.get("postload_pss_bytes"),
        "retained_rss": value.get("steady_rss_bytes"),
        "retained_status": ("marked steady boundary" if value.get("steady_rss_bytes") is not None
                            else "unsupported: no steady boundary marked"),
        "first": value.get("first"),
        "last": value.get("last"),
        "phase_samples": value.get("phase_samples", {}),
        "cgroup_current": value.get("cgroup_current_bytes"),
        "cgroup_peak": value.get("cgroup_peak_bytes"),
        "cgroup_events": value.get("cgroup_events"),
        "cgroup_error": value.get("cgroup_error"),
        "samples": value.get("samples", []),
        "sampling_errors": value.get("sampling_errors", []),
    }


def _ensure_clean(result: Any, workload: Workload, side: str, pass_name: str) -> None:
    if not result.cleanup_complete or result.remaining_pids:
        raise RuntimeError(
            f"{workload.name} {pass_name} {side} left processes behind: {result.remaining_pids}"
        )


def _workload_cpu(measured: Any, payload: dict[str, Any], workload: Workload) -> dict[str, Any]:
    if workload.cpu_boundary == "process":
        return _cpu_dict(measured, payload, workload.name)
    if workload.cpu_boundary != "internal":
        raise ValueError(f"{workload.name}: unknown CPU boundary {workload.cpu_boundary!r}")
    region = {}
    for key in ("cpu_user_seconds", "cpu_system_seconds"):
        value = payload.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise RuntimeError(f"{workload.name}: missing or invalid kernel-region {key}")
        region[key] = float(value)
    user, system = region["cpu_user_seconds"], region["cpu_system_seconds"]
    operation_count = payload["operation_count"]
    process = _cpu_dict(measured, payload, workload.name)
    return {
        "user_seconds": user,
        "system_seconds": system,
        "total_seconds": user + system,
        "user_seconds_per_operation": user / operation_count,
        "system_seconds_per_operation": system / operation_count,
        "total_seconds_per_operation": (user + system) / operation_count,
        "operation_count": operation_count,
        "coverage": KERNEL_REGION_CPU_COVERAGE,
        "process_total_seconds": process["total_seconds"],
        "process_coverage": process["coverage"],
    }


def _cpu_dict(measured: Any, payload: dict[str, Any], workload_name: str) -> dict[str, Any]:
    """Account for reported child CPU only when kernel totals exclude it."""
    operation_count = payload["operation_count"]
    user = getattr(measured, "cpu_user_seconds", None)
    system = getattr(measured, "cpu_system_seconds", None)
    coverage = getattr(measured, "cpu_coverage", "unsupported")
    root_user = None if coverage == CGROUP_TREE_CPU_COVERAGE else user
    root_system = None if coverage == CGROUP_TREE_CPU_COVERAGE else system
    child_cpu = None
    if workload_name == "zipimport_cold":
        child_cpu = payload.get("reaped_child_cpu")
        if not isinstance(child_cpu, dict) or child_cpu.get("process_count") != operation_count \
                or type(child_cpu.get("process_count")) is not int:
            raise RuntimeError("zipimport_cold: missing or inconsistent direct child CPU ledger")
        for key in ("user_seconds", "system_seconds"):
            value = child_cpu.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise RuntimeError(f"zipimport_cold: invalid direct child {key}")
        if user is not None and system is not None and coverage in (
            MAC_REAPED_CPU_COVERAGE, CGROUP_TREE_CPU_COVERAGE
        ):
            # Both kernel totals already include these children. Keep the
            # workload ledger as a cross-check without adding it twice.
            pass
        elif user is not None and system is not None and coverage == LINUX_ROOT_CPU_COVERAGE:
            user += child_cpu["user_seconds"]
            system += child_cpu["system_seconds"]
            coverage = ("wait4 root plus workload-reported RUSAGE_CHILDREN direct reaped children; "
                        "grandchildren and unreaped descendants excluded")
        else:
            coverage = "incomplete: direct child CPU reported but root CPU unavailable"
    elif "reaped_child_cpu" in payload:
        raise RuntimeError(f"{workload_name}: unexpected direct child CPU ledger")
    return {
        "user_seconds": user,
        "system_seconds": system,
        "total_seconds": None if user is None or system is None else user + system,
        "user_seconds_per_operation": None if user is None else user / operation_count,
        "system_seconds_per_operation": None if system is None else system / operation_count,
        "total_seconds_per_operation": None if user is None or system is None else (user + system) / operation_count,
        "operation_count": operation_count,
        "coverage": coverage,
        "root_user_seconds": root_user,
        "root_system_seconds": root_system,
        "reaped_child_cpu": child_cpu,
    }


def run_workload(
    workload: Workload,
    baseline: Path,
    candidate: Path,
    *,
    profile: str,
    site_packages: Path | None,
    output_dir: Path,
    wheelhouse: Path | None = None,
    pip_packages: tuple[str, ...] = (),
    allocation_site: Path | None = None,
    affinity: set[int] | None = None,
    timeout_seconds: float = 300,
    memory_interval_seconds: float = 0.01,
    perf_stat: bool = False,
    measure_memory: bool = True,
    memory_only: bool = False,
    baseline_python_home: Path | None = None,
    candidate_python_home: Path | None = None,
) -> dict[str, Any]:
    if profile not in PROFILE_ROUNDS:
        raise ValueError(f"unknown profile: {profile}")
    if memory_only and (not measure_memory or perf_stat or allocation_site is not None):
        raise ValueError("memory-only requires memory sampling without CPU or allocation diagnostics")
    homes = {"baseline": baseline_python_home, "candidate": candidate_python_home}
    if any(home is not None for home in homes.values()):
        if not memory_only or any(home is None for home in homes.values()):
            raise ValueError("matched Python homes require both sides and memory-only sampling")
        names = [str(home) for home in homes.values()]
        if not all(home.is_absolute() for home in homes.values()):
            raise ValueError("matched Python homes must be absolute")
        if not all(name.isascii() for name in names) or len(names[0]) != len(names[1]):
            raise ValueError("matched Python homes require equal-length ASCII paths")
        for home, python in zip(homes.values(), (baseline, candidate)):
            if not home.is_dir() or home.resolve() != python.parent.parent.resolve():
                raise ValueError("Python home must alias the selected interpreter installation")
    output_dir.mkdir(parents=True, exist_ok=True)
    timing_rounds, memory_rounds = PROFILE_ROUNDS[profile]
    if memory_only:
        timing_rounds = 0
    if not measure_memory:
        memory_rounds = 0
    if workload.noise_class == "noisy" and measure_memory:
        # Scheduling and child overlap make process-tree peaks less stable in
        # ASGI, pip, and multiprocessing workloads. Five separate memory
        # processes give their medians enough observations to resist one
        # transient peak without relaxing the baseline-derived gate.
        memory_rounds = max(memory_rounds, 5)
    sides = {"baseline": baseline, "candidate": candidate}
    env = workload_environment(site_packages, wheelhouse=wheelhouse, pip_packages=pip_packages)
    environments = {side: {**env, **({"PYTHONHOME": str(home)} if home is not None else {})}
                    for side, home in homes.items()}
    django_fixture: Path | None = None
    django_fixture_sha256: str | None = None
    if workload.name == "django_wsgi_first_request":
        django_fixture, django_fixture_sha256 = _prepare_django_first_request_fixture(
            baseline, environments["baseline"], output_dir, timeout_seconds, affinity,
        )
        env["BENCH_DJANGO_FIXTURE_PATH"] = str(django_fixture)
        for environment in environments.values():
            environment["BENCH_DJANGO_FIXTURE_PATH"] = str(django_fixture)
    result: dict[str, Any] = {
        "identity": {
            "name": workload.name,
            "category": workload.category,
            "operation": workload.operation,
            "operation_count": workload.iterations,
            "noise_class": workload.noise_class,
            "packages": list(workload.packages),
            "timing_boundary": workload.timing_boundary,
            "cpu_boundary": workload.cpu_boundary,
        },
        "baseline": {
            "timing": {"samples": [], "cpu_rounds": []},
            "memory": {
                "rounds": [],
                **({"status": "not_measured", "reason": "timing-only host mode"}
                   if not measure_memory else {}),
            },
        },
        "candidate": {
            "timing": {"samples": [], "cpu_rounds": []},
            "memory": {
                "rounds": [],
                **({"status": "not_measured", "reason": "timing-only host mode"}
                   if not measure_memory else {}),
            },
        },
    }
    if memory_only:
        result["measurement_mode"] = "memory-only"
        for side in sides:
            result[side]["timing"] = {"status": "not_measured", "reason": "memory-only", "samples": []}
    for side, home in homes.items():
        if home is not None:
            result[side]["python_home"] = str(home)
    digests: set[str] = set()
    operation_counts: set[int] = set()
    implementations: dict[str, str] = {}
    # Each side gets one uninstrumented preparation run before RSS sampling.
    # Third-party bytecode is prepared by the caller and immutable workload
    # fixtures are prepared above. Timed children provide no reusable state.
    for side, python in sides.items():
        warmup = run_command(workload_command(python, workload, workload.iterations),
                             env=environments[side], cwd=Path(__file__).resolve().parents[2],
                             timeout=timeout_seconds, affinity=affinity,
                             sample_interval_seconds=None)
        _ensure_clean(warmup, workload, side, "warmup")
        if warmup.returncode != 0 or warmup.timed_out:
            raise RuntimeError(f"{workload.name} warmup {side} failed: {warmup.stderr[-2000:]}")
        warmup_payload = _measurement_payload(warmup.stdout.decode("utf-8", errors="replace"), workload,
                                              workload.iterations)
        digests.add(warmup_payload["digest"])
        operation_counts.add(warmup_payload["operation_count"])
        backend = warmup_payload.get("backend")
        if isinstance(backend, str) and backend:
            implementations[side] = backend
    # Alternating BC/CB order prevents one side from always running cold.
    order = [side for index in range(timing_rounds) for side in
             (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline"))]
    for index, side in enumerate(order):
        command = workload_command(sides[side], workload, workload.iterations)
        measured = run_command(command, env=environments[side], cwd=Path(__file__).resolve().parents[2],
                               timeout=timeout_seconds, affinity=affinity,
                               sample_interval_seconds=None)
        _ensure_clean(measured, workload, side, "timing")
        if measured.returncode != 0 or measured.timed_out:
            raise RuntimeError(f"{workload.name} timing {side} failed: {measured.stderr[-2000:]}")
        payload = _measurement_payload(measured.stdout.decode("utf-8", errors="replace"), workload,
                                       workload.iterations)
        digests.add(payload["digest"])
        operation_counts.add(payload["operation_count"])
        elapsed = _timing_elapsed(measured, payload, workload)
        result[side]["timing"]["samples"].append(elapsed)
        cpu = _workload_cpu(measured, payload, workload)
        result[side]["timing"]["cpu_rounds"].append(cpu)
        (output_dir / f"timing-{index:02d}-{side}.json").write_text(
            json.dumps({"payload": payload, "elapsed_seconds_external": measured.duration_seconds,
                        "cpu": cpu}, indent=2) + "\n"
        )
    for index in range(memory_rounds):
        for side in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
            command = workload_command(sides[side], workload, workload.iterations, memory=True)
            measured = run_command(command, env=environments[side], cwd=Path(__file__).resolve().parents[2],
                                   timeout=timeout_seconds, affinity=affinity,
                                   sample_interval_seconds=memory_interval_seconds)
            _ensure_clean(measured, workload, side, "memory")
            if measured.returncode != 0 or measured.timed_out:
                raise RuntimeError(f"{workload.name} memory {side} failed: {measured.stderr[-2000:]}")
            payload = _measurement_payload(measured.stdout.decode("utf-8", errors="replace"), workload,
                                           workload.iterations)
            digests.add(payload["digest"])
            operation_counts.add(payload["operation_count"])
            memory = _memory_dict(measured.memory)
            if not memory_only:
                cpu = _workload_cpu(measured, payload, workload)
                memory["cpu"] = cpu
            result[side]["memory"]["rounds"].append(memory)
            (output_dir / f"memory-{index:02d}-{side}.json").write_text(
                json.dumps({"payload": payload, "memory": memory}, indent=2) + "\n"
            )
    if len(digests) != 1:
        raise RuntimeError(f"{workload.name}: correctness digest differs across runs: {digests}")
    if len(operation_counts) != 1:
        raise RuntimeError(f"{workload.name}: operation count differs across runs: {operation_counts}")
    if django_fixture is not None:
        if _file_sha256(django_fixture) != django_fixture_sha256:
            raise RuntimeError("Django fixture changed during measured runs")
        result["identity"]["fixture_sha256"] = django_fixture_sha256
        result["identity"]["fixture_open_boundary"] = "inside process timing"
    result["identity"]["digest"] = next(iter(digests))
    result["identity"]["operation_count"] = next(iter(operation_counts))
    if implementations:
        result["identity"]["implementation_by_side"] = implementations
    if allocation_site is not None:
        from benchmarks.harness.allocations import run_allocation_pass

        allocation_rounds = 3 if profile == "rigorous" else 2 if profile == "standard" else 1
        for side, python in sides.items():
            command = workload_command(python, workload, workload.allocation_iterations)
            allocation_operations = (
                result["identity"]["operation_count"] * workload.allocation_iterations
                // workload.iterations
            )
            allocation_samples = []
            for index in range(allocation_rounds):
                allocation_samples.append(run_allocation_pass(
                    command,
                    memray_pythonpath=[allocation_site, *([site_packages] if site_packages else [])],
                    output_json=output_dir / f"allocations-{side}-{index:02d}.json",
                    operations=allocation_operations,
                    env=environments[side],
                    follow_fork=workload.name == "multiprocess_pool",
                    timeout_seconds=timeout_seconds,
                ))
            result[side]["allocations"] = {"rounds": allocation_samples}
    if perf_stat:
        from benchmarks.harness.perf import run_perf_stat

        for side, python in sides.items():
            result[side]["perf"] = run_perf_stat(
                workload_command(python, workload, workload.iterations),
                enabled=True,
                operations=result["identity"]["operation_count"],
                env=environments[side],
                output_json=output_dir / f"perf-{side}.json",
            )
    (output_dir / "raw.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result
