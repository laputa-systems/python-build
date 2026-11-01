"""JIT-sensitive kernels loaded from the pinned pyperformance wheel.

Each workload runs one or more operations of a pyperformance benchmark and
reports the operation count plus a digest of its deterministic output. The
kernel source is never vendored here: it is read from the digest-pinned wheel
under `benchmarks/vendor/`, so a JIT build and its no-JIT sibling run the same
code.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.workloads.registry import BY_NAME

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
PYPERFORMANCE_WHEEL = VENDOR / "pyperformance-1.14.0-py3-none-any.whl"
PYPERF_WHEEL = VENDOR / "pyperf-2.10.0-py3-none-any.whl"
SHA256SUMS = VENDOR / "SHA256SUMS"


class WorkloadError(RuntimeError):
    """A pinned kernel input or output did not match its recorded content."""


_verified_wheels: set[Path] = set()


def verify_wheel(wheel: Path, sums: Path = SHA256SUMS) -> str:
    """Return the wheel digest, or raise when it differs from the pinned sum."""
    expected = None
    for line in sums.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == wheel.name:
            expected = fields[0]
            break
    if expected is None:
        raise WorkloadError(f"{wheel.name} is not listed in {sums}")
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual != expected:
        raise WorkloadError(
            f"{wheel.name} digest mismatch: {actual} != {expected}; "
            "the vendored wheel changed"
        )
    return actual


def _verify_wheels() -> None:
    if PYPERFORMANCE_WHEEL in _verified_wheels and PYPERF_WHEEL in _verified_wheels:
        return
    verify_wheel(PYPERFORMANCE_WHEEL)
    verify_wheel(PYPERF_WHEEL)
    _verified_wheels.update((PYPERFORMANCE_WHEEL, PYPERF_WHEEL))


def _load(module: str) -> dict[str, Any]:
    """Execute one benchmark from the wheel under a private module namespace."""
    _verify_wheels()
    entry = f"pyperformance/data-files/benchmarks/bm_{module}/run_benchmark.py"
    with zipfile.ZipFile(PYPERFORMANCE_WHEEL) as archive:
        source = archive.read(entry)
    namespace: dict[str, Any] = {
        "__name__": f"_jit_kernels_{module}",
        "__file__": f"{PYPERFORMANCE_WHEEL}/{entry}",
    }
    sys.path.insert(0, str(PYPERF_WHEEL))
    try:
        exec(compile(source, entry, "exec"), namespace)
    finally:
        sys.path.remove(str(PYPERF_WHEEL))
    return namespace


def _digest(parts: list[Any]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, bytes):
            data = part
            tag = b"b"
        else:
            data = repr(part).encode("utf-8")
            tag = b"r"
        digest.update(tag)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _result(iterations: int, units_per_iteration: int, digest: str,
            elapsed: float) -> dict[str, int | float | str]:
    return {
        "operation_count": iterations * units_per_iteration,
        "digest": digest,
        "elapsed_seconds": elapsed,
    }


@dataclass(frozen=True)
class _Kernel:
    module: str
    operation: str
    measure: Callable[[int], tuple[int, str, float]]


def _nbody(iterations: int) -> tuple[int, str, float]:
    module = _load("nbody")
    module["offset_momentum"](module["BODIES"]["sun"])
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        module["report_energy"]()
        module["advance"](0.01, 20_000)
        parts.append(module["report_energy"]())
    elapsed = time.perf_counter() - started
    return iterations * 20_000, _digest(parts), elapsed


def _spectral_norm(iterations: int) -> tuple[int, str, float]:
    module = _load("spectral_norm")
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        u = [1] * module["DEFAULT_N"]
        for _ in range(10):
            v = module["eval_AtA_times_u"](u)
            u = module["eval_AtA_times_u"](v)
        vBv = vv = 0
        for ue, ve in zip(u, v):
            vBv += ue * ve
            vv += ve * ve
        parts.append((vBv, vv))
    elapsed = time.perf_counter() - started
    return iterations, _digest(parts), elapsed


def _float(iterations: int) -> tuple[int, str, float]:
    module = _load("float")
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        points = [module["Point"](index) for index in range(module["POINTS"])]
        parts.append(module["maximize"](points))
    elapsed = time.perf_counter() - started
    return iterations * module["POINTS"], _digest(parts), elapsed


def _richards(iterations: int) -> tuple[int, str, float]:
    module = _load("richards")
    richards = module["Richards"]()
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        completed = richards.run(1)
        if not completed:
            raise WorkloadError("richards reported unexpected task counts")
        parts.append((completed, module["taskWorkArea"].holdCount,
                      module["taskWorkArea"].qpktCount))
    elapsed = time.perf_counter() - started
    return iterations, _digest(parts), elapsed


def _raytrace(iterations: int) -> tuple[int, str, float]:
    module = _load("raytrace")
    with tempfile.TemporaryDirectory(prefix="jit-raytrace-") as temporary:
        frame = Path(temporary) / "frame.ppm"
        elapsed = module["bench_raytrace"](iterations, 100, 100, str(frame))
        rendered = frame.read_bytes()
    if not rendered.startswith(b"P6 100 100 255\n"):
        raise WorkloadError("raytrace did not render a 100x100 PPM image")
    return iterations * 10_000, _digest([rendered]), elapsed


def _fannkuch(iterations: int) -> tuple[int, str, float]:
    module = _load("fannkuch")
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        parts.append(module["fannkuch"](9))
    elapsed = time.perf_counter() - started
    return iterations, _digest(parts), elapsed


def _deltablue(iterations: int) -> tuple[int, str, float]:
    module = _load("deltablue")
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        output = io.StringIO()
        previous = sys.stdout
        sys.stdout = output
        try:
            module["delta_blue"](100)
        finally:
            sys.stdout = previous
        if output.getvalue():
            raise WorkloadError(f"deltablue reported a failed check: {output.getvalue()!r}")
        parts.append("ok")
    elapsed = time.perf_counter() - started
    return iterations, _digest(parts), elapsed


class _CaptureRunner:
    metadata: dict[str, Any] = {}

    def __init__(self) -> None:
        self.call: tuple[str, Callable[..., None], tuple[Any, ...]] | None = None

    def bench_func(self, name: str, function: Callable[..., None], *args: Any) -> None:
        self.call = (name, function, args)


def _chaos(iterations: int) -> tuple[int, str, float]:
    module = _load("chaos")
    runner = _CaptureRunner()
    module["main"](runner, argparse.Namespace(
        thickness=module["DEFAULT_THICKNESS"], width=module["DEFAULT_WIDTH"],
        height=module["DEFAULT_HEIGHT"], iterations=module["DEFAULT_ITERATIONS"],
        filename=None, rng_seed=module["DEFAULT_RNG_SEED"],
    ))
    if runner.call is None:
        raise WorkloadError("chaos benchmark did not register a kernel")
    _, function, arguments = runner.call
    frames = []
    with tempfile.TemporaryDirectory(prefix="jit-chaos-") as temporary:
        started = time.perf_counter()
        for index in range(iterations):
            frame = Path(temporary) / f"frame-{index}.ppm"
            function(*arguments[:3], str(frame), arguments[4])
            frames.append(frame.read_bytes())
        elapsed = time.perf_counter() - started
    if any(not frame.startswith(b"P6\n") for frame in frames):
        raise WorkloadError("chaos did not render PPM images")
    return iterations * module["DEFAULT_ITERATIONS"], _digest(frames), elapsed


def _go(iterations: int) -> tuple[int, str, float]:
    module = _load("go")
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        parts.append(module["versus_cpu"]())
    elapsed = time.perf_counter() - started
    return iterations * module["GAMES"], _digest(parts), elapsed


def _nqueens(iterations: int) -> tuple[int, str, float]:
    module = _load("nqueens")
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        parts.append(list(module["n_queens"](8)))
    elapsed = time.perf_counter() - started
    return iterations, _digest(parts), elapsed


def _hexiom(iterations: int) -> tuple[int, str, float]:
    module = _load("hexiom")
    board, solution = module["LEVELS"][30]
    expected = solution.rstrip()
    parts = []
    started = time.perf_counter()
    for _ in range(iterations):
        output = io.StringIO()
        module["solve_file"](board.strip(), module["Done"].FIRST_STRATEGY,
                             module["DESCENDING"], output)
        rendered = "\n".join(line.rstrip() for line in output.getvalue().splitlines())
        if rendered != expected:
            raise WorkloadError(f"hexiom solved a different board: {rendered!r}")
        parts.append(rendered)
    elapsed = time.perf_counter() - started
    return iterations, _digest(parts), elapsed


def _scimark_sor(iterations: int) -> tuple[int, str, float]:
    module = _load("scimark")
    grid = module["Random"](7).RandomMatrix(module["Array2D"](100, 100))
    started = time.perf_counter()
    module["SOR_execute"](1.25, grid, iterations, module["Array2D"])
    elapsed = time.perf_counter() - started
    return iterations, _digest([grid.data.tobytes()]), elapsed


def _scimark_sparse_mat_mult(iterations: int) -> tuple[int, str, float]:
    from array import array

    module = _load("scimark")
    size, nonzeros = 1000, 50_000
    per_row = nonzeros // size
    values = array("d", [0.0]) * nonzeros
    for index in range(nonzeros):
        values[index] = 0.25 + (index % 17) * 0.5
    columns = array("i", [0]) * nonzeros
    rows = array("i", [0]) * (size + 1)
    for row in range(size):
        start = rows[row]
        step = max(1, row // per_row)
        rows[row + 1] = start + per_row
        for offset in range(per_row):
            columns[start + offset] = (offset * step) % size
    vector = module["Random"](7).RandomVector(size)
    product = array("d", [0.0]) * size
    started = time.perf_counter()
    module["SparseCompRow_matmult"](size, product, values, rows, columns, vector, iterations)
    elapsed = time.perf_counter() - started
    return iterations * size, _digest([product.tobytes()]), elapsed


def _scimark_fft(iterations: int) -> tuple[int, str, float]:
    module = _load("scimark")
    points = 2 * 1024
    initial = module["Random"](7).RandomVector(points)
    data = module["copy_vector"](initial)
    started = time.perf_counter()
    for _ in range(iterations):
        module["FFT_transform"](points, data)
        module["FFT_inverse"](points, data)
    elapsed = time.perf_counter() - started
    return iterations, _digest([data.tobytes()]), elapsed


def _scimark_lu(iterations: int) -> tuple[int, str, float]:
    from array import array

    module = _load("scimark")
    source = module["Random"](7).RandomMatrix(module["ArrayList"](100, 100))
    lu = module["ArrayList"](100, 100)
    pivot = array("i", [0]) * 100
    started = time.perf_counter()
    for _ in range(iterations):
        module["LU"](lu, source, pivot)
    elapsed = time.perf_counter() - started
    return iterations, _digest([row.tobytes() for row in lu.data]), elapsed


def _scimark_monte_carlo(iterations: int) -> tuple[int, str, float]:
    module = _load("scimark")
    started = time.perf_counter()
    estimate = module["MonteCarlo"](iterations)
    elapsed = time.perf_counter() - started
    return iterations, _digest([estimate]), elapsed


_KERNELS: dict[str, _Kernel] = {
    "nbody": _Kernel("nbody", "20,000-step n-body advance", _nbody),
    "spectral_norm": _Kernel("spectral_norm", "spectral-norm solve", _spectral_norm),
    "float": _Kernel("float", "100,000-point normalize and maximize", _float),
    "richards": _Kernel("richards", "scheduler run", _richards),
    "raytrace": _Kernel("raytrace", "100x100 frame", _raytrace),
    "fannkuch": _Kernel("fannkuch", "9-element fannkuch solve", _fannkuch),
    "deltablue": _Kernel("deltablue", "constraint plan execution", _deltablue),
    "chaos": _Kernel("chaos", "5,000-point frame", _chaos),
    "go": _Kernel("go", "200-game search", _go),
    "nqueens": _Kernel("nqueens", "8-queens solve", _nqueens),
    "hexiom": _Kernel("hexiom", "level-30 solve", _hexiom),
    "scimark_sor": _Kernel("scimark", "successive over-relaxation sweep", _scimark_sor),
    "scimark_sparse_mat_mult": _Kernel("scimark", "sparse matrix row",
                                       _scimark_sparse_mat_mult),
    "scimark_fft": _Kernel("scimark", "forward and inverse transform", _scimark_fft),
    "scimark_lu": _Kernel("scimark", "LU factorization", _scimark_lu),
    "scimark_monte_carlo": _Kernel("scimark", "Monte Carlo sample", _scimark_monte_carlo),
}


def run(name: str, iterations: int | None = None) -> dict[str, int | float | str]:
    kernel = _KERNELS.get(name)
    spec = BY_NAME.get(name)
    if kernel is None or spec is None:
        raise WorkloadError(f"unknown JIT kernel: {name}")
    if iterations is None:
        iterations = spec.iterations
    if iterations < 1:
        raise WorkloadError("iterations must be positive")
    count, digest, elapsed = kernel.measure(iterations)
    units, remainder = divmod(count, iterations)
    if remainder:
        raise WorkloadError(f"{name} reported a partial operation across iterations")
    return _result(iterations, units, digest, elapsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=sorted(_KERNELS))
    parser.add_argument("--iterations", type=int)
    args = parser.parse_args(argv)
    try:
        result = run(args.scenario, args.iterations)
    except WorkloadError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
