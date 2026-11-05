import ctypes
import gc
import shlex
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path


ITERATIONS = 64


def contiguous_loop(source, iterations):
    result = None
    for _ in range(iterations):
        result = source[1:-1]
    return result[0]


def stepped_loop(source, iterations):
    result = None
    for _ in range(iterations):
        result = source[::-1]
    return result[0]


def assign_loop(target, source, iterations):
    for _ in range(iterations):
        target[:] = source
    return len(target)


class SliceRecorder:
    def __init__(self, count):
        self.saved = [None] * count
        self.position = 0

    def __setitem__(self, key, value):
        self.saved[self.position] = key
        self.position += 1


def retain_slices_loop(recorder, iterations):
    for index in range(iterations):
        recorder[index:index + 1] = None
    return recorder.position


def discard_loop(holder):
    while holder:
        value = holder.pop()
        value = None


def indexed_loop(source, start, stop, iterations):
    result = None
    for _ in range(iterations):
        result = source[start:stop]
    return result


def clear_batches_loop(target, batches):
    for batch in batches:
        target[:] = batch
        batch.clear()
        target[:] = []


def assert_compiled(function):
    if not sys._jit.is_enabled():
        return
    import _opcode

    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except ValueError:
            continue
        if executor.get_jit_code():
            return
    raise AssertionError(f"{function.__name__} did not acquire native JIT code")


def warm_loops():
    source = list(range(37))
    contiguous_loop(source, 20_000)
    stepped_loop(source, 20_000)
    assign_loop([None] * len(source), source, 20_000)
    retain_slices_loop(SliceRecorder(20_000), 20_000)
    discard_loop([[] for _ in range(20_000)])
    discard_loop([slice(None) for _ in range(20_000)])
    indexed_loop(source, 0, None, 20_000)
    clear_batches_loop([], [[] for _ in range(20_000)])
    for function in (contiguous_loop, stepped_loop, assign_loop, retain_slices_loop,
                     discard_loop, indexed_loop, clear_batches_loop):
        assert_compiled(function)
    gc.collect()


def configure_fixture(library):
    fixture = ctypes.PyDLL(str(library))
    fixture.call_with_container_allocator.argtypes = [
        ctypes.py_object, ctypes.py_object, ctypes.c_int, ctypes.c_ssize_t,
        ctypes.c_int, ctypes.c_int,
    ]
    fixture.call_with_container_allocator.restype = ctypes.py_object
    fixture.container_allocator_counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    fixture.container_allocator_counts.restype = None
    fixture.check_container_deallocator.argtypes = [
        ctypes.py_object, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_int,
    ]
    fixture.check_container_deallocator.restype = ctypes.py_object
    return fixture


def allocator_cases(fixture):
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    source = list(range(37))
    cases = (
        (contiguous_loop, lambda: (source, ITERATIONS), 1, 35 * pointer_size, 0),
        (stepped_loop, lambda: (source, ITERATIONS), 1, 37 * pointer_size, 0),
        (assign_loop, lambda: ([None] * 37, source, ITERATIONS),
         1, 37 * pointer_size, 0),
        (contiguous_loop, lambda: (source, ITERATIONS), 2, sys.getsizeof([]), 1),
        (retain_slices_loop, lambda: (SliceRecorder(ITERATIONS), ITERATIONS),
         2, sys.getsizeof(slice(None)), 2),
    )
    counts = (ctypes.c_uint64 * 2)()
    for function, arguments, domain, size, drain in cases:
        fixture.call_with_container_allocator(function, arguments(), domain, size, 0, drain)
        fixture.container_allocator_counts(counts)
        assert counts[0] > 0 and counts[1] == 0, (function.__name__, tuple(counts))
        args = arguments()
        failure_at = 2 if function is contiguous_loop and domain == 2 else 4
        try:
            fixture.call_with_container_allocator(
                function, args, domain, size, failure_at, drain)
        except MemoryError:
            pass
        else:
            raise AssertionError(f"{function.__name__} did not propagate MemoryError")
        fixture.container_allocator_counts(counts)
        assert counts[0] >= failure_at and counts[1] == 1, (function.__name__, tuple(counts))
        assert source == list(range(37)), function.__name__
        if function is assign_loop:
            assert args[0] == source
        if function is retain_slices_loop:
            recorder = args[0]
            assert recorder.position == failure_at - 1
            assert all(isinstance(key, slice) for key in recorder.saved[:recorder.position])
            assert all(key is None for key in recorder.saved[recorder.position:])
        assert function(*arguments()) in (1, 36, 37, ITERATIONS)

    try:
        fixture.call_with_container_allocator(lambda: None, (), 1, 37 * pointer_size, 0, 0)
    except AssertionError as error:
        assert "armed container allocator hook was not called" in str(error)
    else:
        raise AssertionError("the fixture accepted an operation that bypassed its hook")


def reentrant_cases():
    source = list(range(37))
    calls = []

    class ClearingIndex:
        def __index__(self):
            calls.append(len(source))
            source.clear()
            return 0

    assert indexed_loop(source, ClearingIndex(), None, ITERATIONS) == []
    assert calls == [37] + [0] * (ITERATIONS - 1)

    events = []
    target = []

    class ReinsertOnRelease:
        def __init__(self, index):
            self.index = index

        def __del__(self):
            events.append((self.index, len(target)))
            target.append(self.index)

    batches = [[ReinsertOnRelease(index) for index in range(3)]
               for _ in range(ITERATIONS)]
    clear_batches_loop(target, batches)
    assert events == [(2, 0), (1, 1), (0, 2)] * ITERATIONS, events
    assert target == [2, 1, 0], target
    assert all(batch == [] for batch in batches)

    class ListSubclass(list):
        def __setitem__(self, key, value):
            events.append("subclass")

    subclass = ListSubclass([1, 2, 3])
    assign_loop(subclass, [], ITERATIONS)
    assert subclass == [1, 2, 3]
    assert events[-ITERATIONS:] == ["subclass"] * ITERATIONS


def main():
    warm_loops()
    scratch = Path(__file__).resolve().parents[2] / ".work" / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jit-container-allocator-", dir=scratch) as temporary:
        library = Path(temporary) / "container_allocator.so"
        command = [*shlex.split(sysconfig.get_config_var("LDSHARED")),
                   *shlex.split(sysconfig.get_config_var("CONFIGURE_CPPFLAGS")),
                   "-I", sysconfig.get_path("include"),
                   str(Path(__file__).with_name("jit_container_allocator.c")),
                   "-o", str(library)]
        subprocess.run(command, check=True)
        fixture = configure_fixture(library)
        allocator_cases(fixture)
        preheader = sys.getsizeof([]) - list.__basicsize__
        assert preheader == sys.getsizeof(slice(None)) - slice.__basicsize__
        for kind in range(2):
            for replace_during_callback in range(2):
                fixture.check_container_deallocator(
                    discard_loop, kind, preheader, replace_during_callback)
        reentrant_cases()
    print("container allocator hooks, failures, captured destructors and reentry match")


if __name__ == "__main__":
    main()
