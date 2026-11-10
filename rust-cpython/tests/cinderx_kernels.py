"""Run the JIT kernel harness under CinderX with its JIT off and on.

Usage: python3 rust-cpython/tests/cinderx_kernels.py --python PATH
       [--kernel NAME ...] [--expect FILE] [--json FILE] [--timeout SECONDS]

Each kernel runs in a fresh subprocess of the given interpreter.  The JIT-off
child receives ``-X cinderx-jit-disable``; the JIT-on child receives
``-X cinderx-jit-all -X cinderx-jit-background-compile=0`` so every function it
calls is compiled synchronously before it runs.  Both are the options accepted
by CinderX's ``-X cinderx-jit-help`` and exercised by its command-line tests.

The child imports ``benchmarks.workloads.jit_kernels`` and reports its digest
plus the JIT state it observed.  The driver fails when a child fails, when a
mode does not report the expected JIT state, when a kernel's two digests
differ, or when a digest differs from ``--expect`` (a JSON object mapping
kernel names to harness-recorded digests).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT_SECONDS = 1800

MODE_FLAGS: dict[str, tuple[str, ...]] = {
    "off": ("-X", "cinderx-jit-disable"),
    "on": ("-X", "cinderx-jit-all", "-X", "cinderx-jit-background-compile=0"),
}

CHILD = """\
import json
import platform
import sys

import cinderx
import cinderx.jit
from benchmarks.workloads import jit_kernels

name = sys.argv[1]
enabled = cinderx.jit.is_enabled()
result = jit_kernels.run(name)
if enabled:
    cinderx.jit.wait_for_background_compiles()
print(json.dumps({
    "name": name,
    "digest": result["digest"],
    "operation_count": result["operation_count"],
    "jit_enabled": enabled,
    "jit_enabled_after": cinderx.jit.is_enabled(),
    "compile_after_n_calls": cinderx.jit.get_compile_after_n_calls(),
    "compiled_functions": len(cinderx.jit.get_compiled_functions()) if enabled else 0,
    "python": platform.python_version(),
}, sort_keys=True), flush=True)
"""


class KernelError(RuntimeError):
    """A kernel subprocess did not produce a usable result."""


def kernel_names() -> list[str]:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from benchmarks.workloads.registry import JIT_HELDOUT, JIT_SET
    finally:
        sys.path.remove(str(REPO_ROOT))
    return [*JIT_SET, *JIT_HELDOUT]


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


def run_kernel(python: Path, name: str, mode: str, timeout: int) -> dict[str, object]:
    command = [str(python), *MODE_FLAGS[mode], "-c", CHILD, name]
    try:
        process = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=kernel_environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise KernelError(f"{name} [{mode}] timed out after {timeout}s") from error
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip().splitlines()
        tail = detail[-1] if detail else "no output"
        raise KernelError(f"{name} [{mode}] exited {process.returncode}: {tail}")
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise KernelError(f"{name} [{mode}] produced no output")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise KernelError(f"{name} [{mode}] produced invalid JSON: {lines[-1]!r}") from error
    if result.get("name") != name:
        raise KernelError(f"{name} [{mode}] reported kernel {result.get('name')!r}")
    if not isinstance(result.get("digest"), str) or not result["digest"]:
        raise KernelError(f"{name} [{mode}] reported no digest")
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
    args = parser.parse_args(argv)

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
            print(f"{name}: {mode}", file=sys.stderr, flush=True)
            try:
                result = run_kernel(python, name, mode, args.timeout)
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
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
