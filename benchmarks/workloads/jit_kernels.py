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
import resource
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from benchmarks.workloads.registry import BY_NAME

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
PYPERFORMANCE_WHEEL = VENDOR / "pyperformance-1.14.0-py3-none-any.whl"
PYPERF_WHEEL = VENDOR / "pyperf-2.11.0-py3-none-any.whl"
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


def _wheel_data(module: str, name: str) -> bytes:
    _verify_wheels()
    with zipfile.ZipFile(PYPERFORMANCE_WHEEL) as archive:
        return archive.read(f"pyperformance/data-files/benchmarks/bm_{module}/data/{name}")


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
        exec(compile(source, entry, "exec"), namespace)  # noqa: S102
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


@dataclass(frozen=True)
class _Timing:
    wall_seconds: float
    cpu_user_seconds: float
    cpu_system_seconds: float


class _TimedRegion:
    def __init__(self) -> None:
        self._usage = resource.getrusage(resource.RUSAGE_SELF)
        self._started = time.perf_counter()

    def stop(self) -> _Timing:
        wall = time.perf_counter() - self._started
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return _Timing(wall, usage.ru_utime - self._usage.ru_utime,
                       usage.ru_stime - self._usage.ru_stime)


def _result(iterations: int, units_per_iteration: int, digest: str,
            timing: _Timing) -> dict[str, int | float | str]:
    return {
        "operation_count": iterations * units_per_iteration,
        "digest": digest,
        "elapsed_seconds": timing.wall_seconds,
        "cpu_user_seconds": timing.cpu_user_seconds,
        "cpu_system_seconds": timing.cpu_system_seconds,
    }


@dataclass(frozen=True)
class _Kernel:
    module: str
    operation: str
    measure: Callable[[int], tuple[int, str, _Timing]]


def _nbody(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("nbody")
    module["offset_momentum"](module["BODIES"]["sun"])
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        module["report_energy"]()
        module["advance"](0.01, 20_000)
        parts.append(module["report_energy"]())
    timing = region.stop()
    return iterations * 20_000, _digest(parts), timing


def _spectral_norm(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("spectral_norm")
    parts = []
    region = _TimedRegion()
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
    timing = region.stop()
    return iterations, _digest(parts), timing


def _float(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("float")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        points = [module["Point"](index) for index in range(module["POINTS"])]
        parts.append(module["maximize"](points))
    timing = region.stop()
    return iterations * module["POINTS"], _digest(parts), timing


def _richards(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("richards")
    richards = module["Richards"]()
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        completed = richards.run(1)
        if not completed:
            raise WorkloadError("richards reported unexpected task counts")
        parts.append((completed, module["taskWorkArea"].holdCount,
                      module["taskWorkArea"].qpktCount))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _raytrace(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("raytrace")
    with tempfile.TemporaryDirectory(prefix="jit-raytrace-") as temporary:
        frame = Path(temporary) / "frame.ppm"
        region = _TimedRegion()
        wall = module["bench_raytrace"](iterations, 100, 100, str(frame))
        timing = replace(region.stop(), wall_seconds=wall)
        rendered = frame.read_bytes()
    if not rendered.startswith(b"P6 100 100 255\n"):
        raise WorkloadError("raytrace did not render a 100x100 PPM image")
    return iterations * 10_000, _digest([rendered]), timing


def _fannkuch(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("fannkuch")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        parts.append(module["fannkuch"](9))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _deltablue(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("deltablue")
    parts = []
    region = _TimedRegion()
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
    timing = region.stop()
    return iterations, _digest(parts), timing


class _CaptureRunner:
    metadata: ClassVar[dict[str, Any]] = {}

    def __init__(self) -> None:
        self.call: tuple[str, Callable[..., None], tuple[Any, ...]] | None = None

    def bench_func(self, name: str, function: Callable[..., None], *args: Any) -> None:
        self.call = (name, function, args)


def _chaos(iterations: int) -> tuple[int, str, _Timing]:
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
        region = _TimedRegion()
        for index in range(iterations):
            frame = Path(temporary) / f"frame-{index}.ppm"
            function(*arguments[:3], str(frame), arguments[4])
            frames.append(frame.read_bytes())
        timing = region.stop()
    if any(not frame.startswith(b"P6\n") for frame in frames):
        raise WorkloadError("chaos did not render PPM images")
    return iterations * module["DEFAULT_ITERATIONS"], _digest(frames), timing


def _go(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("go")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        parts.append(module["versus_cpu"]())
    timing = region.stop()
    return iterations * module["GAMES"], _digest(parts), timing


def _nqueens(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("nqueens")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        parts.append(list(module["n_queens"](8)))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _hexiom(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("hexiom")
    board, solution = module["LEVELS"][30]
    expected = solution.rstrip()
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        output = io.StringIO()
        module["solve_file"](board.strip(), module["Done"].FIRST_STRATEGY,
                             module["DESCENDING"], output)
        rendered = "\n".join(line.rstrip() for line in output.getvalue().splitlines())
        if rendered != expected:
            raise WorkloadError(f"hexiom solved a different board: {rendered!r}")
        parts.append(rendered)
    timing = region.stop()
    return iterations, _digest(parts), timing


def _scimark_sor(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("scimark")
    grid = module["Random"](7).RandomMatrix(module["Array2D"](100, 100))
    region = _TimedRegion()
    module["SOR_execute"](1.25, grid, iterations, module["Array2D"])
    timing = region.stop()
    return iterations, _digest([grid.data.tobytes()]), timing


def _scimark_sparse_mat_mult(iterations: int) -> tuple[int, str, _Timing]:
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
    region = _TimedRegion()
    module["SparseCompRow_matmult"](size, product, values, rows, columns, vector, iterations)
    timing = region.stop()
    return iterations * size, _digest([product.tobytes()]), timing


def _scimark_fft(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("scimark")
    points = 2 * 1024
    initial = module["Random"](7).RandomVector(points)
    data = module["copy_vector"](initial)
    region = _TimedRegion()
    for _ in range(iterations):
        module["FFT_transform"](points, data)
        module["FFT_inverse"](points, data)
    timing = region.stop()
    return iterations, _digest([data.tobytes()]), timing


def _scimark_lu(iterations: int) -> tuple[int, str, _Timing]:
    from array import array

    module = _load("scimark")
    source = module["Random"](7).RandomMatrix(module["ArrayList"](100, 100))
    lu = module["ArrayList"](100, 100)
    pivot = array("i", [0]) * 100
    region = _TimedRegion()
    for _ in range(iterations):
        module["LU"](lu, source, pivot)
    timing = region.stop()
    return iterations, _digest([row.tobytes() for row in lu.data]), timing


def _scimark_monte_carlo(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("scimark")
    region = _TimedRegion()
    estimate = module["MonteCarlo"](iterations)
    timing = region.stop()
    return iterations, _digest([estimate]), timing


def _barnes_hut(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("barnes_hut")
    width, height = 1000, 800
    particles = module["create_deterministic_galaxy"](
        module["DEFAULT_PARTICLES"], width / 2, height / 2)
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        module["advance_system"](particles, module["DEFAULT_THETA"], module["TIME_STEP"],
                                 width, height)
        parts.append(module["calculate_system_energy"](particles))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _pidigits(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("pidigits")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        parts.append(module["calc_ndigits"](module["DEFAULT_DIGITS"]))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _pyflate(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("pyflate")
    archive = _wheel_data("pyflate", "interpreter.tar.bz2")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        field = module["RBitfield"](io.BytesIO(archive))
        if field.readbits(16) != 0x425A:
            raise WorkloadError("pyflate input is not a bzip2 stream")
        parts.append(module["bzip2_main"](field))
    timing = region.stop()
    if any(hashlib.md5(part).hexdigest() != "afa004a630fe072901b1d9628b960974" for part in parts):
        raise WorkloadError("pyflate decoded different bytes")
    return iterations, _digest(parts), timing


def _coroutines(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("coroutines")
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        coroutine = module["fibonacci"](25)
        try:
            while True:
                coroutine.send(None)
        except StopIteration as finished:
            parts.append(finished.value)
    timing = region.stop()
    return iterations, _digest(parts), timing


def _generators(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("generators")
    tree = module["tree"](range(100_000))
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        parts.append(sum(tree))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _comprehensions(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("comprehensions")
    widgets = module["make_some_widgets"]()
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        tray = module["WidgetTray"](1, widgets)
        parts.append([widget.widget_id for widget in tray.sorted_widgets])
    timing = region.stop()
    return iterations, _digest(parts), timing


def _richards_super(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("richards_super")
    richards = module["Richards"]()
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        if not richards.run(1):
            raise WorkloadError("richards_super reported unexpected task counts")
        parts.append((module["taskWorkArea"].holdCount, module["taskWorkArea"].qpktCount))
    timing = region.stop()
    return iterations, _digest(parts), timing


def _meteor_contest(iterations: int) -> tuple[int, str, _Timing]:
    module = _load("meteor_contest")
    board, cti, pieces = module["get_puzzle"](module["WIDTH"], module["HEIGHT"])
    footprints = module["get_footprints"](board, cti, pieces)
    neighborhoods = module["get_senh"](board, cti)
    parts = []
    region = _TimedRegion()
    for _ in range(iterations):
        solutions: list[str] = []
        module["solve"](module["SOLVE_ARG"], 0, frozenset(range(len(board))), [-1] * len(board),
                        list(range(len(pieces))), solutions, footprints, neighborhoods)
        parts.append(solutions)
    timing = region.stop()
    if any(solutions != module["SOLUTIONS"] for solutions in parts):
        raise WorkloadError("meteor_contest found different solutions")
    return iterations, _digest(parts), timing


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
    "barnes_hut": _Kernel("barnes_hut", "200-particle quadtree step", _barnes_hut),
    "pidigits": _Kernel("pidigits", "2,000-digit pi expansion", _pidigits),
    "pyflate": _Kernel("pyflate", "bzip2 archive decode", _pyflate),
    "coroutines": _Kernel("coroutines", "recursive fibonacci(25) coroutine", _coroutines),
    "generators": _Kernel("generators", "100,000-node tree traversal", _generators),
    "comprehensions": _Kernel("comprehensions", "widget tray build", _comprehensions),
    "richards_super": _Kernel("richards_super", "scheduler run", _richards_super),
    "meteor_contest": _Kernel("meteor_contest", "60-solution board solve", _meteor_contest),
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
    count, digest, timing = kernel.measure(iterations)
    units, remainder = divmod(count, iterations)
    if remainder:
        raise WorkloadError(f"{name} reported a partial operation across iterations")
    return _result(iterations, units, digest, timing)


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
