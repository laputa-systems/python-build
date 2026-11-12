"""Fresh-process, packaging, serialization, and multiprocessing workloads.

The runner starts this module once per measurement. Workloads that represent
startup or imports start a fresh child interpreter for each operation; the
other workloads keep their semantic operation in this process. The final
stdout line is a machine-readable result consumed by the benchmark runner.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import multiprocessing
import os
import pickle
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class WorkloadError(RuntimeError):
    """A workload could not run or its result failed validation."""


_DJANGO_IMPORTS = (
    "django",
    "django.conf",
    "django.contrib.auth",
    "django.db.models",
    "django.template",
    "django.urls",
)
_APP_STACK_IMPORTS = (
    "django",
    "django.contrib.auth",
    "django.db.models",
    "django.template",
    "fastapi",
    "fastapi.routing",
    "pydantic",
    "sqlalchemy",
    "sqlalchemy.orm",
)
_PACKAGE_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[A-Za-z0-9][A-Za-z0-9_.+-]*)$"
)
_POOL_WORKERS = 2
_PICKLE_PROTOCOL = 5
_WORKER_ROUNDS = 300_000


def _digest(value: Any) -> str:
    """Hash a canonical JSON representation of a correctness result."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result(operation_count: int, digest: str, elapsed_seconds: float) -> dict[str, int | float | str]:
    if operation_count < 1:
        raise WorkloadError("operation count must be positive")
    if not digest:
        raise WorkloadError("workload produced an empty digest")
    return {
        "operation_count": operation_count,
        "digest": digest,
        "elapsed_seconds": elapsed_seconds,
    }


def _run_child(
    command: list[str], *, timeout: int = 300, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one isolated child and retain its output for correctness checks."""
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkloadError(f"could not complete child command {command[0]!r}: {error}") from error
    if completed.returncode:
        details = (completed.stderr or completed.stdout).strip()[-2000:]
        raise WorkloadError(
            f"child command exited {completed.returncode}: {details or command!r}"
        )
    return completed


def python_startup(iterations: int, *, no_site: bool = False) -> dict[str, int | float | str]:
    """Measure fresh interpreter startup, including normal site initialization."""
    command = [sys.executable]
    if no_site:
        command.append("-S")
    command.extend(("-c", "pass"))
    elapsed = 0.0
    for _ in range(iterations):
        started = time.perf_counter()
        _run_child(command)
        elapsed += time.perf_counter() - started
    mode = "no-site" if no_site else "site"
    return _result(iterations, _digest({"scenario": "python_startup", "mode": mode}), elapsed)


def _import_fresh_process(modules: tuple[str, ...], iterations: int, scenario: str) -> dict[str, int | float | str]:
    """Import a fixed module set once in each fresh process."""
    module_literal = repr(modules)
    script = (
        "import importlib\n"
        f"_modules = {module_literal}\n"
        "for _name in _modules:\n"
        "    importlib.import_module(_name)\n"
        "print('IMPORTS_OK:' + str(len(_modules)))\n"
    )
    command = [sys.executable, "-B", "-c", script]
    expected_line = f"IMPORTS_OK:{len(modules)}"
    elapsed = 0.0
    for _ in range(iterations):
        started = time.perf_counter()
        completed = _run_child(command)
        elapsed += time.perf_counter() - started
        if not completed.stdout.splitlines() or completed.stdout.splitlines()[-1] != expected_line:
            raise WorkloadError(f"{scenario} child did not confirm all requested imports")
    return _result(iterations, _digest({"scenario": scenario, "modules": modules}), elapsed)


def import_django(iterations: int) -> dict[str, int | float | str]:
    """Import Django's request, ORM, auth, template, and routing entry points."""
    return _import_fresh_process(_DJANGO_IMPORTS, iterations, "import_django")


def import_app_stack(iterations: int) -> dict[str, int | float | str]:
    """Import the representative Django, FastAPI/Pydantic, and SQLAlchemy stack."""
    return _import_fresh_process(_APP_STACK_IMPORTS, iterations, "import_app_stack")


_COLD_SOURCE_CHECK = (
    "import os, sys\n"
    "_specs = [m.__spec__ for m in list(sys.modules.values())\n"
    "          if getattr(m, '__spec__', None) is not None]\n"
    "_frozen = {s.name for s in _specs if s.origin == 'frozen'}\n"
    "_cached = [s.cached for s in _specs if s.cached]\n"
    "_prefix = (sys.pycache_prefix or '\\0') + os.sep\n"
    "if _frozen - {'_frozen_importlib', '_frozen_importlib_external', '_pybuiltins', 'zipimport'}:\n"
    "    raise SystemExit('frozen modules loaded: ' + ', '.join(sorted(_frozen)))\n"
    "if not _cached or any(not c.startswith(_prefix) or os.path.exists(c) for c in _cached):\n"
    "    raise SystemExit('a module was not compiled from source into an empty cache')\n"
    "print('COLD_SOURCES:' + str(len(_cached)))\n"
)


def _cold_process(script: str, iterations: int, scenario: str) -> float:
    """Run fresh children that compile every module they import, stdlib included, from source."""
    command = [sys.executable, "-B", "-X", "frozen_modules=off", "-c", script + _COLD_SOURCE_CHECK]
    elapsed = 0.0
    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="cold-pycache-") as prefix:
            env = {**os.environ, "PYTHONPYCACHEPREFIX": prefix}
            started = time.perf_counter()
            completed = _run_child(command, env=env)
            elapsed += time.perf_counter() - started
            if any(Path(prefix).iterdir()):
                raise WorkloadError(f"{scenario} child wrote to its bytecode cache")
        last = completed.stdout.splitlines()[-1] if completed.stdout.splitlines() else ""
        if not last.startswith("COLD_SOURCES:") or int(last.removeprefix("COLD_SOURCES:")) < 1:
            raise WorkloadError(f"{scenario} child did not confirm its source compilations")
    return elapsed


def cold_python_startup(iterations: int) -> dict[str, int | float | str]:
    """Start a fresh interpreter whose startup modules all compile from source."""
    elapsed = _cold_process("", iterations, "cold_python_startup")
    return _result(iterations, _digest({"scenario": "cold_python_startup"}), elapsed)


def _import_cold_process(modules: tuple[str, ...], iterations: int, scenario: str) -> dict[str, int | float | str]:
    """Import a fixed module set once in each fresh process, compiling every module from source."""
    script = (
        "import importlib\n"
        f"for _name in {modules!r}:\n"
        "    importlib.import_module(_name)\n"
    )
    elapsed = _cold_process(script, iterations, scenario)
    return _result(iterations, _digest({"scenario": scenario, "modules": modules}), elapsed)


def cold_import_django(iterations: int) -> dict[str, int | float | str]:
    """Import Django's entry points with every module compiled from source."""
    return _import_cold_process(_DJANGO_IMPORTS, iterations, "cold_import_django")


def cold_import_app_stack(iterations: int) -> dict[str, int | float | str]:
    """Import the Django, FastAPI/Pydantic, and SQLAlchemy stack with every module compiled from source."""
    return _import_cold_process(_APP_STACK_IMPORTS, iterations, "cold_import_app_stack")


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_packages() -> tuple[tuple[str, str, str], ...]:
    raw = os.environ.get("BENCH_PIP_PACKAGES", "")
    specs = shlex.split(raw)
    if not specs:
        raise WorkloadError(
            "BENCH_PIP_PACKAGES must contain the exact direct package pins for the locked wheelhouse"
        )
    pins: list[tuple[str, str, str]] = []
    for spec in specs:
        match = _PACKAGE_PIN.fullmatch(spec)
        if not match:
            raise WorkloadError(f"package must be pinned as name==version: {spec!r}")
        name = match.group("name")
        version = match.group("version")
        pins.append((_normalized_distribution_name(name), version, spec))
    names = [name for name, _, _ in pins]
    if len(names) != len(set(names)):
        raise WorkloadError("BENCH_PIP_PACKAGES contains duplicate normalized package names")
    return tuple(pins)


def _distribution_inventory(target: Path) -> tuple[tuple[str, str], ...]:
    """Read the installed distribution inventory from a fresh pip target."""
    distributions: list[tuple[str, str]] = []
    for distribution in importlib.metadata.distributions(path=[str(target)]):
        name = distribution.metadata.get("Name")
        if name is None:
            raise WorkloadError(f"installed distribution lacks a Name field under {target}")
        distributions.append((_normalized_distribution_name(name), distribution.version))
    inventory = tuple(sorted(distributions))
    if len({name for name, _ in inventory}) != len(inventory):
        raise WorkloadError("pip installed multiple versions of the same normalized distribution")
    return inventory


def pip_install_wheelhouse(iterations: int) -> dict[str, int | float | str]:
    """Install an exact pinned package set from the verified local wheelhouse."""
    wheelhouse_value = os.environ.get("BENCH_WHEELHOUSE")
    if not wheelhouse_value:
        raise WorkloadError("BENCH_WHEELHOUSE must point to the prepared offline wheelhouse")
    wheelhouse = Path(wheelhouse_value).resolve()
    if not wheelhouse.is_dir():
        raise WorkloadError(f"BENCH_WHEELHOUSE is not a directory: {wheelhouse}")
    pins = _pinned_packages()
    requirements = [spec for _, _, spec in pins]
    expected = {name: version for name, version, _ in pins}
    elapsed = 0.0
    expected_inventory: tuple[tuple[str, str], ...] | None = None

    for _ in range(iterations):
        with tempfile.TemporaryDirectory(prefix="python-build-pip-") as temporary:
            target = Path(temporary) / "target"
            command = [
                sys.executable,
                "-m",
                "pip",
                "--isolated",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--no-warn-script-location",
                "--no-cache-dir",
                "--no-index",
                "--only-binary=:all:",
                "--ignore-installed",
                "--find-links",
                str(wheelhouse),
                "--target",
                str(target),
                *requirements,
            ]
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise WorkloadError(f"offline pip install did not complete: {error}") from error
            elapsed += time.perf_counter() - started
            if completed.returncode:
                details = (completed.stderr or completed.stdout).strip()[-3000:]
                raise WorkloadError(
                    f"offline pip install exited {completed.returncode}: {details or command!r}"
                )

            inventory = _distribution_inventory(target)
            installed = dict(inventory)
            missing_or_mismatched = {
                name: (version, installed.get(name))
                for name, version in expected.items()
                if installed.get(name) != version
            }
            if missing_or_mismatched:
                raise WorkloadError(
                    f"offline pip install did not install its pinned direct requirements: "
                    f"{missing_or_mismatched}"
                )
            if expected_inventory is not None and inventory != expected_inventory:
                raise WorkloadError("repeated offline installations produced different inventories")
            expected_inventory = inventory

    assert expected_inventory is not None
    return _result(
        iterations,
        _digest({"scenario": "pip_install_wheelhouse", "inventory": expected_inventory}),
        elapsed,
    )


def _serialization_payload() -> dict[str, Any]:
    shared_metadata = {
        "locale": "en_US",
        "permissions": ["read", "write"],
        "flags": {"active": True, "verified": False},
    }
    records = [
        {
            "id": row,
            "name": f"member-{row:04d}",
            "score": (row * 37) % 101,
            "labels": [f"group-{row % 11}", f"region-{row % 7}"],
            "profile": {
                "city": ("Auckland", "Dublin", "Osaka", "Quito")[row % 4],
                "active": row % 3 != 0,
                "metadata": shared_metadata,
            },
        }
        for row in range(1_024)
    ]
    return {
        "schema": "python-build.serialization.v1",
        "records": records,
        "by_id": {record["id"]: record for record in records},
        "metadata": shared_metadata,
    }


def serialization_roundtrip(iterations: int) -> dict[str, int | float | str]:
    """Construct, pickle, and deserialize a deterministic shared object graph."""
    elapsed = 0.0
    last_payload: dict[str, Any] | None = None
    last_decoded: Any = None
    last_wire = b""
    started = time.perf_counter()
    for _ in range(iterations):
        payload = _serialization_payload()
        wire = pickle.dumps(payload, protocol=_PICKLE_PROTOCOL)
        decoded = pickle.loads(wire)
        last_payload = payload
        last_decoded = decoded
        last_wire = wire
    elapsed = time.perf_counter() - started
    if last_payload is None or last_decoded != last_payload:
        raise WorkloadError("pickle round-trip did not preserve the object graph")
    if last_decoded["records"][0]["profile"]["metadata"] is not last_decoded["metadata"]:
        raise WorkloadError("pickle round-trip did not preserve shared object references")
    return _result(
        iterations,
        hashlib.sha256(last_wire).hexdigest(),
        elapsed,
    )


def _pool_work(task: tuple[int, int]) -> tuple[int, int]:
    """CPU work unit sent to one of the two worker processes."""
    seed, rounds = task
    value = seed + 17
    for index in range(rounds):
        value = (value * 33 + index + seed) % 1_000_000_007
    return seed, value


def multiprocess_pool(iterations: int) -> dict[str, int | float | str]:
    """Start a two-process pool and distribute deterministic CPU work units."""
    try:
        context = multiprocessing.get_context("fork")
    except ValueError as error:
        raise WorkloadError("multiprocess_pool requires the Linux fork start method") from error
    tasks = [(index, _WORKER_ROUNDS) for index in range(iterations)]
    started = time.perf_counter()
    pool = context.Pool(processes=_POOL_WORKERS)
    try:
        results = pool.map(_pool_work, tasks, chunksize=1)
        pool.close()
    except BaseException:
        pool.terminate()
        raise
    finally:
        pool.join()
    elapsed = time.perf_counter() - started
    expected = [_pool_work(task) for task in tasks]
    if results != expected:
        raise WorkloadError("multiprocess workers returned an incorrect result")
    return _result(iterations, _digest({"tasks": results}), elapsed)


_SCENARIOS: dict[str, Callable[[int], dict[str, int | float | str]]] = {
    "python_startup": python_startup,
    "python_startup_no_site": lambda count: python_startup(count, no_site=True),
    "import_django": import_django,
    "import_app_stack": import_app_stack,
    "cold_python_startup": cold_python_startup,
    "cold_import_django": cold_import_django,
    "cold_import_app_stack": cold_import_app_stack,
    "pip_install_wheelhouse": pip_install_wheelhouse,
    "serialization_roundtrip": serialization_roundtrip,
    "multiprocess_pool": multiprocess_pool,
}


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=sorted(_SCENARIOS))
    parser.add_argument("--iterations", type=_positive_integer, default=1)
    args = parser.parse_args(argv)
    try:
        result = _SCENARIOS[args.scenario](args.iterations)
    except WorkloadError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
