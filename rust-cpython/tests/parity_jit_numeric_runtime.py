import ctypes
import shlex
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path


def numeric_loop(iterations):
    value = 0.125
    integer = 1_000_000
    values = []
    for index in range(iterations):
        value = (value + 0.25) * 1.0
        integer = (integer + 1_000_001) - 1_000_000
        values.append(value)
    return values, integer


def main():
    scratch = Path(__file__).resolve().parents[2] / ".work" / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    iterations = 50_000
    for _ in range(3):
        values, integer = numeric_loop(iterations)
        assert len(values) == iterations and values[-1] == 0.125 + 0.25 * iterations
        assert integer == 1_000_000 + iterations
    del values, integer
    if sys._jit.is_enabled():
        import _opcode

        executors = []
        for offset in range(0, len(numeric_loop.__code__.co_code), 2):
            try:
                executors.append(_opcode.get_executor(numeric_loop.__code__, offset))
            except ValueError:
                continue
        assert any(executor.get_jit_code() for executor in executors), "the loop was not JIT compiled"
    with tempfile.TemporaryDirectory(prefix="jit-numeric-reftracer-", dir=scratch) as temporary:
        library = Path(temporary) / "numeric_reftracer.so"
        command = [*shlex.split(sysconfig.get_config_var("LDSHARED")),
                   *shlex.split(sysconfig.get_config_var("CONFIGURE_CPPFLAGS")),
                   "-I", sysconfig.get_path("include"),
                   str(Path(__file__).with_name("jit_numeric_reftracer.c")), "-o", str(library)]
        subprocess.run(command, check=True)
        tracer = ctypes.PyDLL(str(library))
        tracer.begin_numeric_reference_trace.argtypes = []
        tracer.begin_numeric_reference_trace.restype = ctypes.c_int
        tracer.end_numeric_reference_trace.argtypes = []
        tracer.end_numeric_reference_trace.restype = ctypes.c_int
        tracer.numeric_reference_counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
        tracer.numeric_reference_counts.restype = None
        counts = (ctypes.c_uint64 * 4)()
        assert tracer.begin_numeric_reference_trace() == 0
        try:
            result = numeric_loop(iterations)
        finally:
            assert tracer.end_numeric_reference_trace() == 0
        tracer.numeric_reference_counts(counts)
        values, integer = result
        assert len(values) == iterations
        assert all(value == 0.125 + 0.25 * (index + 1) for index, value in enumerate(values))
        assert integer == 1_000_000 + iterations, integer
        created_floats, destroyed_floats, created_ints, destroyed_ints = counts
        assert created_floats - destroyed_floats == iterations, tuple(counts)
        assert created_ints > 0 and created_ints - destroyed_ints == 1, tuple(counts)
        print("numeric values and reference-tracer counts match:", tuple(counts))


if __name__ == "__main__":
    main()
