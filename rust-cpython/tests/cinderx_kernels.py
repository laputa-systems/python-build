"""Run the JIT kernel harness under CinderX with its JIT off, auto, and all.

Usage:
    python3 rust-cpython/tests/cinderx_kernels.py --python PATH
        [--kernel NAME ...] [--expect FILE] [--json FILE] [--timeout SECONDS]

    python3 rust-cpython/tests/cinderx_kernels.py --python PATH --timed
        [--runs N] [--set all|training|heldout] [--variant NAME=FLAG ...]
        [--kernel NAME ...] [--expect FILE] [--json FILE] [--timeout SECONDS]

    python3 rust-cpython/tests/cinderx_kernels.py --python PATH --steady
        [--runs N] [--steady-passes N] [--set all|training|heldout]
        [--variant NAME=FLAG ...] [--kernel NAME ...]
        [--expect FILE] [--json FILE] [--timeout SECONDS]

Each kernel runs in a fresh subprocess of the given interpreter.  The JIT-off
child receives ``-X cinderx-jit-disable``; the compile-all child receives
``-X cinderx-jit-all -X cinderx-jit-background-compile=0`` so every function it
calls is compiled synchronously before it runs.  The auto child enables
``cinderx.jit.auto()`` with background compilation off, which is CinderX's
documented default policy and keeps compilation synchronous.

The untimed run compares off against compile-all and checks digests.  The timed
run measures off, auto, and every compile-all variant in interleaved
subprocesses, rotating the mode order across runs, while holding perf.py's
exclusive host lease.  It records the kernel's timed-region CPU from
``jit_kernels.run``, wall time, the child's peak RSS, and the compiled function
count and code bytes.  ``--variant NAME=cinderx-jit-flag=value`` adds a
compile-all mode with one extra ``-X`` switch for ablation measurements.
``--expect`` is a JSON object mapping kernel names to harness-recorded digests.

The steady run keeps to CinderX's profile-guided path instead of compiling
before any profile exists.  Its JIT child enables auto mode (compile after 1000
calls or 100,000 interpreted bytecodes) with ``-X
cinderx-jit-recompile-threshold=2``, so hot functions compile only after the
interpreter has specialized their bytecode and the JIT's inline-cache profile
can drive a recompile.  ``jit_kernels._load`` is memoized in the child so every
warmup and timed pass executes the same code objects; without that the harness
re-execs the benchmark each pass and nothing ever reaches steady state.  The
child warms up until the compiled-function count, summed code size, and
compilation time stop changing, then times ``--steady-passes`` further kernel
passes (at least 10).  Warmup CPU is recorded separately; timed kernel CPU
excludes it.  ``--variant`` adds a steady mode with one extra ``-X`` switch and
ratios are taken against steady.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LANE = REPO_ROOT / "rust-cpython"
DEFAULT_TIMEOUT_SECONDS = 1800
MAX_KERNEL_ATTEMPTS = 3

MODE_FLAGS: dict[str, tuple[str, ...]] = {
    "off": ("-X", "cinderx-jit-disable"),
    "on": ("-X", "cinderx-jit-all", "-X", "cinderx-jit-background-compile=0"),
}

TIMED_MODES: tuple[str, ...] = ("off", "auto", "all")
TIMED_FLAGS: dict[str, tuple[str, ...]] = {
    "off": MODE_FLAGS["off"],
    "auto": ("-X", "cinderx-jit-background-compile=0"),
    "all": MODE_FLAGS["on"],
}

STEADY_MODE = "steady"
STEADY_MODES: tuple[str, ...] = ("off", STEADY_MODE)
STEADY_RECOMPILE_THRESHOLD = 2
STEADY_MIN_PASSES = 10
STEADY_DEFAULT_PASSES = 10
STEADY_MIN_WARMUP = 3
STEADY_STABLE_PASSES = 2
STEADY_MAX_WARMUP = 20
STEADY_FLAGS: dict[str, tuple[str, ...]] = {
    "off": MODE_FLAGS["off"],
    STEADY_MODE: (
        "-X", "cinderx-jit-auto",
        "-X", "cinderx-jit-background-compile=0",
        "-X", f"cinderx-jit-recompile-threshold={STEADY_RECOMPILE_THRESHOLD}",
    ),
}

CHILD = """\
import json
import platform
import resource
import sys

import cinderx
import cinderx.jit

name = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else "off"
if mode == "auto":
    cinderx.jit.auto()
    cinderx.jit.background_compile(False)

from benchmarks.workloads import jit_kernels

enabled = cinderx.jit.is_enabled()
compile_after_n_calls = cinderx.jit.get_compile_after_n_calls()
background_compile = cinderx.jit.get_background_compile()
result = jit_kernels.run(name)
if enabled:
    cinderx.jit.wait_for_background_compiles()
functions = cinderx.jit.get_compiled_functions() if enabled else []
compiled_bytes = sum(cinderx.jit.get_compiled_size(func) for func in functions)
compilation_milliseconds = cinderx.jit.get_compilation_time() if enabled else 0
usage = resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({
    "name": name,
    "mode": mode,
    "digest": result["digest"],
    "operation_count": result["operation_count"],
    "cpu_user_seconds": result["cpu_user_seconds"],
    "cpu_system_seconds": result["cpu_system_seconds"],
    "kernel_cpu_seconds": result["cpu_user_seconds"] + result["cpu_system_seconds"],
    "wall_seconds": result["elapsed_seconds"],
    "ru_maxrss": int(usage.ru_maxrss),
    "peak_rss_bytes": int(usage.ru_maxrss),
    "compiled_functions": len(functions),
    "compiled_code_bytes": int(compiled_bytes),
    "compilation_milliseconds": int(compilation_milliseconds),
    "jit_enabled": enabled,
    "jit_enabled_after": cinderx.jit.is_enabled(),
    "compile_after_n_calls": compile_after_n_calls,
    "background_compile": background_compile,
    "python": platform.python_version(),
}, sort_keys=True), flush=True)
"""

STEADY_CHILD = """\
import json
import platform
import resource
import sys

import cinderx
import cinderx.jit

name = sys.argv[1]
mode = sys.argv[2]
passes = int(sys.argv[3])
min_warmup = int(sys.argv[4])
stable_passes = int(sys.argv[5])
max_warmup = int(sys.argv[6])

from benchmarks.workloads import jit_kernels

original_load = jit_kernels._load
modules = {}


def cached_load(module):
    if module not in modules:
        modules[module] = original_load(module)
    return modules[module]


jit_kernels._load = cached_load

enabled = cinderx.jit.is_enabled()


def compiled_state():
    if not enabled:
        return (0, 0, 0)
    cinderx.jit.wait_for_background_compiles()
    functions = cinderx.jit.get_compiled_functions()
    return (
        len(functions),
        sum(cinderx.jit.get_compiled_size(func) for func in functions),
        int(cinderx.jit.get_compilation_time()),
    )


warmup_cpu = 0.0
warmup_passes = 0
first_digest = None
operation_count = None
stable = 0
previous = None
while warmup_passes < max_warmup:
    result = jit_kernels.run(name)
    warmup_cpu += result["cpu_user_seconds"] + result["cpu_system_seconds"]
    warmup_passes += 1
    if first_digest is None:
        first_digest = result["digest"]
        operation_count = result["operation_count"]
    current = compiled_state()
    stable = stable + 1 if current == previous else 1
    previous = current
    if warmup_passes >= min_warmup and stable >= stable_passes:
        break

timed_user = 0.0
timed_system = 0.0
timed_wall = 0.0
timed_digest = None
for _ in range(passes):
    result = jit_kernels.run(name)
    timed_user += result["cpu_user_seconds"]
    timed_system += result["cpu_system_seconds"]
    timed_wall += result["elapsed_seconds"]
    timed_digest = result["digest"]

functions = cinderx.jit.get_compiled_functions() if enabled else []
compiled_bytes = sum(cinderx.jit.get_compiled_size(func) for func in functions)
compilation_milliseconds = cinderx.jit.get_compilation_time() if enabled else 0
usage = resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({
    "name": name,
    "mode": mode,
    "digest": first_digest,
    "timed_digest": timed_digest,
    "operation_count": operation_count,
    "cpu_user_seconds": timed_user,
    "cpu_system_seconds": timed_system,
    "kernel_cpu_seconds": timed_user + timed_system,
    "wall_seconds": timed_wall,
    "warmup_cpu_seconds": warmup_cpu,
    "warmup_passes": warmup_passes,
    "timed_passes": passes,
    "ru_maxrss": int(usage.ru_maxrss),
    "peak_rss_bytes": int(usage.ru_maxrss),
    "compiled_functions": len(functions),
    "compiled_code_bytes": int(compiled_bytes),
    "compilation_milliseconds": int(compilation_milliseconds),
    "jit_enabled": enabled,
    "jit_enabled_after": cinderx.jit.is_enabled(),
    "compile_after_n_calls": cinderx.jit.get_compile_after_n_calls(),
    "background_compile": cinderx.jit.get_background_compile(),
    "python": platform.python_version(),
}, sort_keys=True), flush=True)
"""


class KernelError(RuntimeError):
    """A kernel subprocess did not produce a usable result."""


def kernel_sets() -> dict[str, str]:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from benchmarks.workloads.registry import JIT_HELDOUT, JIT_SET
    finally:
        sys.path.remove(str(REPO_ROOT))
    return {
        **{name: "training" for name in JIT_SET},
        **{name: "heldout" for name in JIT_HELDOUT},
    }


def kernel_names() -> list[str]:
    names = kernel_sets()
    return [name for name in names]


def kernel_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("CINDERX_", "PYTHONJIT"))
        and key not in {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"}
    }
    environment["PYTHONPATH"] = str(REPO_ROOT)
    environment["PYTHONHASHSEED"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def run_kernel(python: Path, name: str, label: str, flags: tuple[str, ...],
               timeout: int, script: str = CHILD,
               arguments: tuple[str, ...] = ()) -> dict[str, object]:
    command = [str(python), *flags, "-c", script, name, label, *arguments]
    try:
        process = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=kernel_environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise KernelError(f"{name} [{label}] timed out after {timeout}s") from error
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip().splitlines()
        tail = detail[-1] if detail else "no output"
        raise KernelError(f"{name} [{label}] exited {process.returncode}: {tail}")
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise KernelError(f"{name} [{label}] produced no output")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise KernelError(f"{name} [{label}] produced invalid JSON: {lines[-1]!r}") from error
    if result.get("name") != name:
        raise KernelError(f"{name} [{label}] reported kernel {result.get('name')!r}")
    if not isinstance(result.get("digest"), str) or not result["digest"]:
        raise KernelError(f"{name} [{label}] reported no digest")
    return result


def load_expectations(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("kernels"), dict):
        payload = payload["kernels"]
    if not isinstance(payload, dict):
        raise KernelError(f"{path} is not a kernel-to-digest mapping")
    expectations: dict[str, str] = {}
    for name, value in payload.items():
        digest = value.get("digest") if isinstance(value, dict) else value
        if isinstance(digest, str) and digest:
            expectations[name] = digest
    return expectations


def expected_jit_enabled(label: str) -> bool:
    return label != "off"


def timed_measurement(result: dict[str, object]) -> dict[str, object]:
    return {
        "kernel_cpu_seconds": float(result["kernel_cpu_seconds"]),
        "cpu_user_seconds": float(result["cpu_user_seconds"]),
        "cpu_system_seconds": float(result["cpu_system_seconds"]),
        "wall_seconds": float(result["wall_seconds"]),
        "ru_maxrss": int(result["ru_maxrss"]),
        "peak_rss_bytes": int(result["peak_rss_bytes"]),
        "compiled_functions": int(result["compiled_functions"]),
        "compiled_code_bytes": int(result["compiled_code_bytes"]),
        "compilation_milliseconds": int(result["compilation_milliseconds"]),
        "operation_count": result["operation_count"],
        "digest": result["digest"],
        "jit_enabled": result["jit_enabled"],
        "compile_after_n_calls": result["compile_after_n_calls"],
        "background_compile": result["background_compile"],
    }


def steady_measurement(result: dict[str, object]) -> dict[str, object]:
    return {
        "kernel_cpu_seconds": float(result["kernel_cpu_seconds"]),
        "cpu_user_seconds": float(result["cpu_user_seconds"]),
        "cpu_system_seconds": float(result["cpu_system_seconds"]),
        "wall_seconds": float(result["wall_seconds"]),
        "warmup_cpu_seconds": float(result["warmup_cpu_seconds"]),
        "warmup_passes": int(result["warmup_passes"]),
        "timed_passes": int(result["timed_passes"]),
        "timed_digest": result["timed_digest"],
        "ru_maxrss": int(result["ru_maxrss"]),
        "peak_rss_bytes": int(result["peak_rss_bytes"]),
        "compiled_functions": int(result["compiled_functions"]),
        "compiled_code_bytes": int(result["compiled_code_bytes"]),
        "compilation_milliseconds": int(result["compilation_milliseconds"]),
        "operation_count": result["operation_count"],
        "digest": result["digest"],
        "jit_enabled": result["jit_enabled"],
        "compile_after_n_calls": result["compile_after_n_calls"],
        "background_compile": result["background_compile"],
    }


def steady_run(python: Path, kernels: list[str], sets: dict[str, str], runs: int,
               modes: list[str], flags: dict[str, tuple[str, ...]], timeout: int,
               expectations: dict[str, str], passes: int, min_warmup: int,
               stable_passes: int) -> dict[str, dict[str, object]]:
    arguments = (str(passes), str(min_warmup), str(stable_passes),
                 str(STEADY_MAX_WARMUP))
    rows: dict[str, dict[str, object]] = {}
    for kernel_index, name in enumerate(kernels):
        records: list[dict[str, object]] = []
        first_digests: dict[str, str] = {}
        for run_index in range(runs):
            offset = (run_index + kernel_index) % len(modes)
            order = modes[offset:] + modes[:offset]
            measurements: dict[str, object] = {}
            for label in order:
                print(f"{name}: steady run {run_index + 1}/{runs} [{label}]",
                      file=sys.stderr, flush=True)
                attempts: list[dict[str, object]] = []
                result = None
                for attempt in range(1, MAX_KERNEL_ATTEMPTS + 1):
                    try:
                        result = run_kernel(python, name, label, flags[label],
                                            timeout, script=STEADY_CHILD,
                                            arguments=arguments)
                        break
                    except KernelError as error:
                        attempts.append({"attempt": attempt, "error": str(error)})
                        if attempt == MAX_KERNEL_ATTEMPTS:
                            raise
                        print(f"{name} [{label}] steady attempt {attempt} failed: "
                              f"{error}; retrying", file=sys.stderr, flush=True)
                assert result is not None
                if result.get("jit_enabled") is not expected_jit_enabled(label):
                    raise KernelError(
                        f"{name} [{label}] reported jit_enabled={result.get('jit_enabled')}")
                if result.get("jit_enabled_after") is not expected_jit_enabled(label):
                    raise KernelError(
                        f"{name} [{label}] ended with "
                        f"jit_enabled={result.get('jit_enabled_after')}")
                digest = str(result["digest"])
                first_digests.setdefault(label, digest)
                if first_digests[label] != digest:
                    raise KernelError(
                        f"{name} [{label}] first-pass digest changed across runs")
                measurements[label] = steady_measurement(result)
                if attempts:
                    measurements[label]["failed_attempts"] = attempts
            records.append({"run": run_index, "order": order,
                            "measurements": measurements})
        unique_digests = set(first_digests.values())
        if len(unique_digests) != 1:
            raise KernelError(f"{name} first-pass digests differ across modes: "
                              f"{first_digests}")
        digest = unique_digests.pop()
        expected = expectations.get(name)
        if expected is not None and digest != expected:
            raise KernelError(f"{name} digest differs from harness: {digest} != {expected}")

        ratio_vs_off: dict[str, list[float]] = {}
        for label in modes:
            if label == "off":
                continue
            ratio_vs_off[label] = [
                record["measurements"][label]["kernel_cpu_seconds"]
                / record["measurements"]["off"]["kernel_cpu_seconds"]
                for record in records
            ]
        ratio_vs_steady: dict[str, list[float]] = {}
        for label in modes:
            if label in ("off", STEADY_MODE):
                continue
            ratio_vs_steady[label] = [
                record["measurements"][label]["kernel_cpu_seconds"]
                / record["measurements"][STEADY_MODE]["kernel_cpu_seconds"]
                for record in records
            ]
        rows[name] = {
            "set": sets[name],
            "runs": records,
            "digest": digest,
            "expected_digest": expected,
            "digest_match": expected is None or digest == expected,
            "ratios_vs_off": ratio_vs_off,
            "median_ratio_vs_off": {
                label: median_or_none(values) for label, values in ratio_vs_off.items()
            },
            "ratios_vs_steady": ratio_vs_steady,
            "median_ratio_vs_steady": {
                label: median_or_none(values) for label, values in ratio_vs_steady.items()
            },
            "median_warmup_cpu_seconds": {
                label: median_or_none([
                    record["measurements"][label]["warmup_cpu_seconds"]
                    for record in records
                ])
                for label in modes
            },
            "warmup_passes": {
                label: sorted({
                    record["measurements"][label]["warmup_passes"]
                    for record in records
                })
                for label in modes
            },
            "timed_digests": {
                label: sorted({
                    str(record["measurements"][label]["timed_digest"])
                    for record in records
                })
                for label in modes
            },
        }
    return rows


def gate_ready(post: bool) -> dict[str, object]:
    tool = shutil.which("perf-gate-ready")
    if tool is None:
        raise KernelError("perf-gate-ready is not on PATH")
    result = subprocess.run([tool, *(["-1"] if post else [])],
                            capture_output=True, text=True, check=False)
    return {"exit": result.returncode,
            "detail": (result.stderr or result.stdout).strip()[-2000:]}


def load_host_lease():
    sys.path.insert(0, str(LANE))
    try:
        spec = importlib.util.spec_from_file_location("cinderx_kernels_perf",
                                                      LANE / "perf.py")
        if spec is None or spec.loader is None:
            raise KernelError("cannot load rust-cpython/perf.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LANE))
    return module.host_lease


def median_or_none(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def geometric_or_none(values: list[float]) -> float | None:
    return float(statistics.geometric_mean(values)) if values else None


def timed_run(python: Path, kernels: list[str], sets: dict[str, str], runs: int,
              modes: list[str], flags: dict[str, tuple[str, ...]], timeout: int,
              expectations: dict[str, str]) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for kernel_index, name in enumerate(kernels):
        records: list[dict[str, object]] = []
        digests: dict[str, str] = {}
        for run_index in range(runs):
            offset = (run_index + kernel_index) % len(modes)
            order = modes[offset:] + modes[:offset]
            measurements: dict[str, object] = {}
            for label in order:
                print(f"{name}: run {run_index + 1}/{runs} [{label}]",
                      file=sys.stderr, flush=True)
                attempts: list[dict[str, object]] = []
                result = None
                for attempt in range(1, MAX_KERNEL_ATTEMPTS + 1):
                    try:
                        result = run_kernel(python, name, label, flags[label], timeout)
                        break
                    except KernelError as error:
                        attempts.append({"attempt": attempt, "error": str(error)})
                        if attempt == MAX_KERNEL_ATTEMPTS:
                            raise
                        print(f"{name} [{label}] attempt {attempt} failed: {error}; "
                              f"retrying", file=sys.stderr, flush=True)
                assert result is not None
                if result.get("jit_enabled") is not expected_jit_enabled(label):
                    raise KernelError(
                        f"{name} [{label}] reported jit_enabled={result.get('jit_enabled')}")
                if result.get("jit_enabled_after") is not expected_jit_enabled(label):
                    raise KernelError(
                        f"{name} [{label}] ended with "
                        f"jit_enabled={result.get('jit_enabled_after')}")
                digest = str(result["digest"])
                digests.setdefault(label, digest)
                if digests[label] != digest:
                    raise KernelError(f"{name} [{label}] digest changed across runs")
                measurements[label] = timed_measurement(result)
                if attempts:
                    measurements[label]["failed_attempts"] = attempts
            records.append({"run": run_index, "order": order,
                            "measurements": measurements})
        unique_digests = set(digests.values())
        if len(unique_digests) != 1:
            raise KernelError(f"{name} digests differ across modes: {digests}")
        digest = unique_digests.pop()
        expected = expectations.get(name)
        if expected is not None and digest != expected:
            raise KernelError(f"{name} digest differs from harness: {digest} != {expected}")

        ratio_vs_off: dict[str, list[float]] = {}
        for label in modes:
            if label == "off":
                continue
            ratios = []
            for record in records:
                measurements = record["measurements"]
                base = measurements["off"]["kernel_cpu_seconds"]
                value = measurements[label]["kernel_cpu_seconds"]
                ratios.append(value / base)
            ratio_vs_off[label] = ratios
        ratio_vs_all: dict[str, list[float]] = {}
        for label in modes:
            if label in ("off", "all"):
                continue
            if label == "auto":
                continue
            ratios = []
            for record in records:
                measurements = record["measurements"]
                base = measurements["all"]["kernel_cpu_seconds"]
                value = measurements[label]["kernel_cpu_seconds"]
                ratios.append(value / base)
            ratio_vs_all[label] = ratios
        rows[name] = {
            "set": sets[name],
            "runs": records,
            "digest": digest,
            "expected_digest": expected,
            "digest_match": expected is None or digest == expected,
            "ratios_vs_off": ratio_vs_off,
            "median_ratio_vs_off": {
                label: median_or_none(ratios) for label, ratios in ratio_vs_off.items()
            },
            "ratios_vs_all": ratio_vs_all,
            "median_ratio_vs_all": {
                label: median_or_none(ratios) for label, ratios in ratio_vs_all.items()
            },
        }
    return rows


def timed_geomeans(rows: dict[str, dict[str, object]], modes: list[str],
                   variants: list[str], baseline: str = "all") -> dict[str, object]:
    baseline_key = f"median_ratio_vs_{baseline}"
    result: dict[str, object] = {"vs_off": {}, f"vs_{baseline}": {}}
    for set_name in ("training", "heldout"):
        names = [name for name, row in rows.items() if row["set"] == set_name]
        if not names:
            continue
        vs_off = {}
        for label in modes:
            if label == "off":
                continue
            values = [rows[name]["median_ratio_vs_off"][label] for name in names]
            vs_off[label] = geometric_or_none(values)
        result["vs_off"][set_name] = vs_off
        if variants:
            vs_baseline = {}
            for label in variants:
                values = [rows[name][baseline_key][label] for name in names]
                vs_baseline[label] = geometric_or_none(values)
            result[f"vs_{baseline}"][set_name] = vs_baseline
    return result


def write_summary(summary: dict[str, object], path: Path | None) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))


def digest_main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    python = args.python.resolve()
    if not python.is_file():
        parser.error(f"interpreter does not exist: {python}")
    selected = args.kernels or kernel_names()
    unknown = sorted(set(selected) - set(kernel_names()))
    if unknown:
        parser.error("unknown kernels: " + ", ".join(unknown))
    expectations = load_expectations(args.expect) if args.expect else {}

    failures: list[str] = []
    rows: dict[str, dict[str, object]] = {}
    compiled_total = 0
    for name in selected:
        row: dict[str, object] = {}
        for mode in ("off", "on"):
            label = "all" if mode == "on" else "off"
            print(f"{name}: {mode}", file=sys.stderr, flush=True)
            try:
                result = run_kernel(python, name, label, MODE_FLAGS[mode], args.timeout)
            except KernelError as error:
                failures.append(str(error))
                row[mode] = {"error": str(error)}
                continue
            row[mode] = result
            row[f"{mode}_flags"] = list(MODE_FLAGS[mode])
            expected_enabled = mode == "on"
            if result.get("jit_enabled") is not expected_enabled:
                failures.append(
                    f"{name} [{mode}] reported jit_enabled={result.get('jit_enabled')}"
                )
            if result.get("jit_enabled_after") is not expected_enabled:
                failures.append(
                    f"{name} [{mode}] ended with "
                    f"jit_enabled={result.get('jit_enabled_after')}"
                )
            if expected_enabled:
                compiled_total += int(result.get("compiled_functions") or 0)
        off = row.get("off")
        on = row.get("on")
        if isinstance(off, dict) and isinstance(on, dict) and "digest" in off and "digest" in on:
            match = off["digest"] == on["digest"]
            row["digest_match"] = match
            if not match:
                failures.append(
                    f"{name} digest differs: off={off['digest']} on={on['digest']}"
                )
            if name in expectations:
                expected = expectations[name]
                row["expected_digest"] = expected
                expected_match = off["digest"] == expected and on["digest"] == expected
                row["expected_match"] = expected_match
                if not expected_match:
                    failures.append(
                        f"{name} digest differs from harness: off={off['digest']} "
                        f"on={on['digest']} harness={expected}"
                    )
        rows[name] = row

    if compiled_total == 0:
        failures.append("JIT-on runs compiled no functions")

    summary = {
        "python": str(python),
        "modes": {mode: list(flags) for mode, flags in MODE_FLAGS.items()},
        "kernels": rows,
        "compiled_functions_total": compiled_total,
        "failures": failures,
    }
    write_summary(summary, args.json_output)
    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    return 1 if failures else 0


def timed_main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    python = args.python.resolve()
    if not python.is_file():
        parser.error(f"interpreter does not exist: {python}")
    if args.runs < 1:
        parser.error("--runs must be positive")

    sets = kernel_sets()
    selected = args.kernels or list(sets)
    unknown = sorted(set(selected) - set(sets))
    if unknown:
        parser.error("unknown kernels: " + ", ".join(unknown))
    if args.set != "all":
        selected = [name for name in selected if sets[name] == args.set]
    if not selected:
        parser.error("no kernels selected")
    expectations = load_expectations(args.expect) if args.expect else {}

    variants: list[str] = []
    flags: dict[str, tuple[str, ...]] = dict(TIMED_FLAGS)
    for spec in args.variant:
        name, separator, flag = spec.partition("=")
        if not separator or not name or not flag:
            parser.error(f"--variant needs NAME=FLAG, got {spec!r}")
        if name in flags:
            parser.error(f"--variant name {name!r} is reserved")
        variants.append(name)
        flags[name] = (*TIMED_FLAGS["all"], "-X", flag)
    modes = list(TIMED_MODES) + variants

    summary: dict[str, object] = {
        "mode": "timed",
        "python": str(python),
        "runs": args.runs,
        "kernels_selected": selected,
        "modes": {label: list(value) for label, value in flags.items()},
        "variants": variants,
        "geomean": {},
        "kernels": {},
        "failures": [],
    }
    failures: list[str] = summary["failures"]

    before = gate_ready(post=False)
    summary["gate_ready_before"] = before
    if before["exit"] != 0:
        failures.append("perf gate deferred: host busy before the timed run")
        write_summary(summary, args.json_output)
        return 1

    lease = load_host_lease()
    try:
        with (
            contextlib.redirect_stdout(sys.stderr),
            lease("measure", f"cinderx kernels {args.runs} runs"),
        ):
            rows = timed_run(python, selected, sets, args.runs, modes, flags,
                             args.timeout, expectations)
    except KernelError as error:
        failures.append(str(error))
        rows = {}
    after = gate_ready(post=True)
    summary["gate_ready_after"] = after
    if after["exit"] != 0:
        failures.append("perf gate deferred: host busy after the timed run")

    if rows:
        summary["kernels"] = rows
        summary["failed_attempts"] = sum(
            len(measurement.get("failed_attempts", []))
            for row in rows.values()
            for record in row["runs"]
            for measurement in record["measurements"].values()
        )
        summary["geomean"] = timed_geomeans(rows, modes, variants)
    write_summary(summary, args.json_output)
    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    return 1 if failures else 0


def steady_main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    python = args.python.resolve()
    if not python.is_file():
        parser.error(f"interpreter does not exist: {python}")
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.steady_passes < STEADY_MIN_PASSES:
        parser.error(f"--steady-passes must be at least {STEADY_MIN_PASSES}")
    if args.steady_min_warmup < 1 or args.steady_stable_passes < 1:
        parser.error("--steady-min-warmup and --steady-stable-passes must be positive")

    sets = kernel_sets()
    selected = args.kernels or list(sets)
    unknown = sorted(set(selected) - set(sets))
    if unknown:
        parser.error("unknown kernels: " + ", ".join(unknown))
    if args.set != "all":
        selected = [name for name in selected if sets[name] == args.set]
    if not selected:
        parser.error("no kernels selected")
    expectations = load_expectations(args.expect) if args.expect else {}

    variants: list[str] = []
    flags: dict[str, tuple[str, ...]] = dict(STEADY_FLAGS)
    for spec in args.variant:
        name, separator, flag = spec.partition("=")
        if not separator or not name or not flag:
            parser.error(f"--variant needs NAME=FLAG, got {spec!r}")
        if name in flags:
            parser.error(f"--variant name {name!r} is reserved")
        variants.append(name)
        flags[name] = (*STEADY_FLAGS[STEADY_MODE], "-X", flag)
    modes = list(STEADY_MODES) + variants

    summary: dict[str, object] = {
        "mode": STEADY_MODE,
        "python": str(python),
        "runs": args.runs,
        "steady_passes": args.steady_passes,
        "steady_min_warmup": args.steady_min_warmup,
        "steady_stable_passes": args.steady_stable_passes,
        "steady_recompile_threshold": STEADY_RECOMPILE_THRESHOLD,
        "kernels_selected": selected,
        "modes": {label: list(value) for label, value in flags.items()},
        "variants": variants,
        "geomean": {},
        "kernels": {},
        "failures": [],
    }
    failures: list[str] = summary["failures"]

    before = gate_ready(post=False)
    summary["gate_ready_before"] = before
    if before["exit"] != 0:
        failures.append("perf gate deferred: host busy before the steady run")
        write_summary(summary, args.json_output)
        return 1

    lease = load_host_lease()
    try:
        with (
            contextlib.redirect_stdout(sys.stderr),
            lease("measure", f"cinderx steady kernels {args.runs} runs"),
        ):
            rows = steady_run(python, selected, sets, args.runs, modes, flags,
                              args.timeout, expectations, args.steady_passes,
                              args.steady_min_warmup,
                              args.steady_stable_passes)
    except KernelError as error:
        failures.append(str(error))
        rows = {}
    after = gate_ready(post=True)
    summary["gate_ready_after"] = after
    if after["exit"] != 0:
        failures.append("perf gate deferred: host busy after the steady run")

    if rows:
        summary["kernels"] = rows
        summary["failed_attempts"] = sum(
            len(measurement.get("failed_attempts", []))
            for row in rows.values()
            for record in row["runs"]
            for measurement in record["measurements"].values()
        )
        summary["warmup_capped"] = [
            {"kernel": name, "mode": label}
            for name, row in rows.items()
            for label, counts in row["warmup_passes"].items()
            if counts and max(counts) >= STEADY_MAX_WARMUP
        ]
        summary["timed_digest_variation"] = [
            {"kernel": name, "mode": label, "digests": digests}
            for name, row in rows.items()
            for label, digests in row["timed_digests"].items()
            if len(digests) > 1
        ]
        summary["geomean"] = timed_geomeans(rows, modes, variants,
                                            baseline=STEADY_MODE)
    write_summary(summary, args.json_output)
    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path,
                        help="interpreter with CinderX installed")
    parser.add_argument("--kernel", action="append", dest="kernels",
                        help="kernel to run; repeatable, defaults to all 24")
    parser.add_argument("--expect", type=Path,
                        help="JSON mapping kernel names to harness-recorded digests")
    parser.add_argument("--json", type=Path, dest="json_output",
                        help="write the full result table here")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                        help="per-subprocess timeout in seconds")
    parser.add_argument("--timed", action="store_true",
                        help="run the interleaved off/auto/all measurement")
    parser.add_argument("--steady", action="store_true",
                        help="run the interleaved profile-guided steady measurement")
    parser.add_argument("--runs", type=int, default=5,
                        help="timed runs per kernel, interleaved across modes")
    parser.add_argument("--steady-passes", type=int, default=STEADY_DEFAULT_PASSES,
                        help="timed kernel passes after warmup (at least 10)")
    parser.add_argument("--steady-min-warmup", type=int, default=STEADY_MIN_WARMUP,
                        help="minimum warmup kernel passes before steady timing")
    parser.add_argument("--steady-stable-passes", type=int,
                        default=STEADY_STABLE_PASSES,
                        help="consecutive warmup passes with unchanged JIT state")
    parser.add_argument("--set", choices=("all", "training", "heldout"), default="all",
                        help="kernel set for a timed or steady run")
    parser.add_argument("--variant", action="append", default=[], metavar="NAME=FLAG",
                        help="extra mode with one -X switch, for ablation")
    args = parser.parse_args(argv)

    if args.steady and args.timed:
        parser.error("--steady and --timed are mutually exclusive")
    if args.steady:
        return steady_main(args, parser)
    if args.timed:
        return timed_main(args, parser)
    if args.variant or args.set != "all":
        parser.error("--variant and --set require --timed or --steady")
    return digest_main(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
