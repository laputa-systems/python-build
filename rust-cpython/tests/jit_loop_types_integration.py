import json
import os
import subprocess
import sys

PROGRAM = r'''
import array
import _opcode
import _testcapi
import gc
import json
import os
import signal
import sys
import threading

WARM = 7000
CALLBACK_ITERATIONS = 10000000
OPTIMIZE = os.environ.get("PYTHON_UOPS_OPTIMIZE") != "0"
RESULTS = {}
TRACES = {}

SOURCE = """def loop(n, step, initial, start):
    value = initial
    for i in range(start, start + n):
        value = value + step
    return value
"""

def build(source=SOURCE):
    namespace = {}
    exec(source, namespace)
    return namespace["loop"]

def trace(function):
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            entries = list(_opcode.get_executor(function.__code__, offset))
        except (ValueError, RuntimeError):
            continue
        if any(entry[0] == "_JUMP_TO_TOP" for entry in entries):
            return entries
    return []

sparse_values = array.array("d", [0.25]) * WARM
sparse_columns = array.array("i", [0]) * WARM
sparse_x = array.array("d", [2.0])
sparse = build("""def loop(n, val, col, x, initial):
    value = initial
    for k in range(n):
        value += val[k] * x[col[k]]
    return value
""")
RESULTS["sparse"] = sparse(WARM, sparse_values, sparse_columns, sparse_x, 0.0)
TRACES["sparse"] = trace(sparse)

floating = build()
RESULTS["float"] = floating(WARM, 0.25, 0.0, 0)
TRACES["float"] = trace(floating)
RESULTS["noncompact index"] = floating(WARM, 0.25, 0.0, 1 << 40)
TRACES["noncompact index"] = trace(floating)

integer = build()
RESULTS["int"] = integer(WARM, 1, 0, 0)
TRACES["int"] = trace(integer)
RESULTS["integer growth"] = integer(20, 1 << 28, 1 << 29, 0)
RESULTS["bool refusal"] = floating(20, True, 0.0, 0)

calls = []
class Step(float):
    def __radd__(self, other):
        calls.append(sys._getframe(1).f_code.co_name)
        return float(self) + other

RESULTS["subclass refusal"] = floating(20, Step(1.0), 0.0, 0)
RESULTS["subclass calls"] = calls

conditional = build("""def loop(n, step, initialize):
    value = 0.0
    for i in range(n):
        if initialize:
            temporary = 1.0
        value = value + step
    return value
""")
RESULTS["conditional"] = conditional(WARM, 0.25, True)
TRACES["conditional"] = trace(conditional)
RESULTS["null refusal"] = conditional(20, 0.25, False)

def wide(count):
    names = [f"a{i}" for i in range(count)]
    source = "def loop(n, step):\n    " + " = ".join(names) + " = 0.0\n    for i in range(n):\n"
    source += "".join(f"        {name} = {name} + step\n" for name in names)
    source += f"    return {names[0]}, {names[-1]}\n"
    return build(source)

if OPTIMIZE:
    for count in (24, 48, 144):
        fn = wide(count)
        RESULTS[f"wide {count}"] = fn(WARM, 0.25)
        TRACES[f"wide {count}"] = trace(fn)

def async_case(kind):
    fn = build()
    fn(WARM, 1.0, 0.0, 0)
    before = trace(fn)
    stop = threading.Event()
    state = {}
    main_thread = threading.get_ident()
    additions = []

    class Replacement(float):
        def __add__(self, other):
            additions.append([sys._getframe(1).f_code.co_name, str(sys.exception())])
            return float(self) + other

    def replace(frame):
        state["index"] = frame.f_locals["i"]
        state["before"] = frame.f_locals["value"]
        state["context"] = str(sys.exception())
        frame.f_locals["value"] = Replacement(12345.0)

    class Collected:
        def __init__(self, frame):
            self.frame = frame
            self.cycle = self
        def __del__(self):
            replace(self.frame)

    def collected():
        frame = sys._getframe(1)
        while frame and frame.f_code is not fn.__code__:
            frame = frame.f_back
        if frame is None:
            state["error"] = "pending callback lost the loop frame"
            return
        cycle = Collected(frame)
        del cycle
        gc.collect()

    def handler(signum, frame):
        if frame.f_code is not fn.__code__:
            state["error"] = "signal lost the loop frame"
            return
        replace(frame)

    def worker():
        try:
            while not stop.is_set():
                frame = sys._current_frames().get(main_thread)
                if frame and frame.f_code is fn.__code__ and "i" in frame.f_locals:
                    if kind == "signal":
                        os.kill(os.getpid(), signal.SIGUSR1)
                    elif kind == "gc":
                        assert _testcapi._pending_threadfunc(collected, ensure_added=True) == 1
                    else:
                        replace(frame)
                    return
                stop.wait(0.001)
        except BaseException as error:
            state["error"] = repr(error)

    previous_handler = signal.signal(signal.SIGUSR1, handler)
    thread = threading.Thread(target=worker)
    try:
        try:
            raise ValueError("outer")
        except ValueError:
            thread.start()
            result = fn(CALLBACK_ITERATIONS, 1.0, 0.0, 0)
    finally:
        stop.set()
        thread.join(10)
        signal.signal(signal.SIGUSR1, previous_handler)
    assert not thread.is_alive() and "error" not in state, state
    assert "index" in state, state
    completed = state["index"] + 1
    expected = 12345.0 + CALLBACK_ITERATIONS - completed
    RESULTS[kind] = [result == expected, state["before"] == float(completed),
        additions, state["context"]]
    TRACES[kind] = before

if OPTIMIZE:
    for kind in ("signal", "gc", "thread"):
        async_case(kind)

def async_sparse_case(kind):
    values = array.array("d", [0.25]) * CALLBACK_ITERATIONS
    columns = array.array("i", [0]) * CALLBACK_ITERATIONS
    x = array.array("d", [2.0])
    sparse(WARM, values, columns, x, 0.0)
    before = trace(sparse)
    stop = threading.Event()
    state = {}
    main_thread = threading.get_ident()
    additions = []

    class Replacement(float):
        def __add__(self, other):
            additions.append([sys._getframe(1).f_code.co_name, str(sys.exception())])
            return float(self) + other

    def replace(frame):
        state["index"] = frame.f_locals["k"]
        state["before"] = frame.f_locals["value"]
        state["context"] = str(sys.exception())
        frame.f_locals["value"] = Replacement(12345.0)

    class Collected:
        def __init__(self, frame):
            self.frame = frame
            self.cycle = self
        def __del__(self):
            replace(self.frame)

    def collected():
        frame = sys._getframe(1)
        while frame and frame.f_code is not sparse.__code__:
            frame = frame.f_back
        if frame is None:
            state["error"] = "pending callback lost the sparse loop frame"
            return
        cycle = Collected(frame)
        del cycle
        gc.collect()

    def handler(signum, frame):
        if frame.f_code is not sparse.__code__:
            state["error"] = "signal lost the sparse loop frame"
            return
        replace(frame)

    def worker():
        try:
            while not stop.is_set():
                frame = sys._current_frames().get(main_thread)
                if frame and frame.f_code is sparse.__code__ and "k" in frame.f_locals:
                    if kind == "signal":
                        os.kill(os.getpid(), signal.SIGUSR1)
                    elif kind == "gc":
                        assert _testcapi._pending_threadfunc(collected, ensure_added=True) == 1
                    else:
                        replace(frame)
                    return
                stop.wait(0.001)
        except BaseException as error:
            state["error"] = repr(error)

    previous_handler = signal.signal(signal.SIGUSR1, handler)
    thread = threading.Thread(target=worker)
    try:
        try:
            raise ValueError("outer")
        except ValueError:
            thread.start()
            result = sparse(CALLBACK_ITERATIONS, values, columns, x, 0.0)
    finally:
        stop.set()
        thread.join(10)
        signal.signal(signal.SIGUSR1, previous_handler)
    assert not thread.is_alive() and "error" not in state, state
    assert "index" in state, state
    completed = state["index"] + 1
    expected = 12345.0 + (CALLBACK_ITERATIONS - completed) * 0.5
    RESULTS[f"sparse {kind}"] = [result == expected,
        state["before"] == float(completed) * 0.5, additions, state["context"]]
    TRACES[f"sparse {kind}"] = before

if OPTIMIZE:
    for kind in ("signal", "gc", "thread"):
        async_sparse_case(kind)

observed = []
def profiler(frame, event, arg):
    if event == "return" and frame.f_code is floating.__code__:
        observed.append([frame.f_locals["value"], arg])

sys.setprofile(profiler)
floating(20, 1.0, 0.0, 0)
sys.setprofile(None)
RESULTS["profile"] = observed.copy()
observed.clear()

def tracer(frame, event, arg):
    profiler(frame, event, arg)
    return tracer
sys.settrace(tracer)
floating(20, 1.0, 0.0, 0)
sys.settrace(None)
RESULTS["trace"] = observed.copy()

sparse_observed = []
def sparse_profiler(frame, event, arg):
    if event == "return" and frame.f_code is sparse.__code__:
        sparse_observed.append([frame.f_locals["value"], arg])

sys.setprofile(sparse_profiler)
sparse(20, sparse_values, sparse_columns, sparse_x, 0.0)
sys.setprofile(None)
RESULTS["sparse profile"] = sparse_observed.copy()
sparse_observed.clear()

def sparse_tracer(frame, event, arg):
    sparse_profiler(frame, event, arg)
    return sparse_tracer

sys.settrace(sparse_tracer)
sparse(20, sparse_values, sparse_columns, sparse_x, 0.0)
sys.settrace(None)
RESULTS["sparse trace"] = sparse_observed.copy()

speculative_division = build("""def loop(args):
    a, b, n = args
    total = 0.0
    for _ in range(n):
        total += a / b
    return total
""")
RESULTS["speculative division"] = speculative_division((10.0, 3.0, WARM))
TRACES["speculative division"] = trace(speculative_division)
print(json.dumps({"results": RESULTS, "traces": TRACES}, sort_keys=True))
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=90, check=False)
    assert completed.returncode == 0, (jit, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    assert candidate["results"] == reference["results"], (candidate["results"], reference["results"])
    expected = {"float": 1750.0, "noncompact index": 1750.0, "int": 7000,
        "integer growth": (1 << 29) + 20 * (1 << 28), "bool refusal": 20.0,
        "subclass refusal": 20.0, "subclass calls": ["loop"] * 20,
        "conditional": 1750.0, "null refusal": 5.0, "sparse": 3500.0,
        "profile": [[20.0, 20.0]], "trace": [[20.0, 20.0]]}
    expected["sparse profile"] = [[10.0, 10.0]]
    expected["sparse trace"] = [[10.0, 10.0]]
    for key, value in expected.items():
        assert reference["results"][key] == value, (key, reference["results"][key])
        assert noopt["results"][key] == value, (key, noopt["results"][key])
    for kind in ("signal", "gc", "thread"):
        assert reference["results"][kind] == [True, True, [["loop", "outer"]],
            "None" if kind == "thread" else "outer"], (kind, reference["results"][kind])
        assert reference["results"][f"sparse {kind}"] == [True, True, [["loop", "outer"]],
            "None" if kind == "thread" else "outer"], (f"sparse {kind}", reference["results"][f"sparse {kind}"])
    guarded = ("float", "noncompact index", "int", "conditional", "signal", "gc", "thread")
    for key in guarded:
        entries = candidate["traces"][key]
        names = [entry[0] for entry in entries]
        assert "_GUARD_LOCAL_INT" in names, (key, names)
        warm = names.index("_MAKE_WARM")
        assert all(index < warm for index, name in enumerate(names)
                   if name in ("_GUARD_LOCAL_INT", "_GUARD_LOCAL_FLOAT")), (key, entries)
        assert "_CHECK_PERIODIC" not in names and "_TIER2_RESUME_CHECK" in names, (key, names)
        jump = next(entry for entry in entries if entry[0] == "_JUMP_TO_TOP")
        assert jump[2] == warm, (key, jump, warm)
    sparse_names = [entry[0] for entry in candidate["traces"]["sparse"]]
    sparse_warm = sparse_names.index("_MAKE_WARM")
    assert any(name == "_GUARD_LOCAL_FLOAT" and index < sparse_warm
               for index, name in enumerate(sparse_names)), sparse_names
    assert "_CHECK_PERIODIC" not in sparse_names
    assert "_TIER2_RESUME_CHECK" in sparse_names
    division_names = [entry[0] for entry in candidate["traces"]["speculative division"]]
    assert "_GUARD_LOCAL_FLOAT" in division_names, division_names
    assert candidate["results"]["speculative division"] == reference["results"]["speculative division"]
    assert noopt["results"]["speculative division"] == reference["results"]["speculative division"]
    for key in ("float", "noncompact index", "conditional"):
        names = {entry[0] for entry in candidate["traces"][key]}
        assert not names.intersection({"_GUARD_TOS_FLOAT", "_GUARD_NOS_FLOAT"}), (key, names)
    names = {entry[0] for entry in candidate["traces"]["int"]}
    assert "_GUARD_TOS_INT" in names and "_GUARD_NOS_INT" in names, names
    for key in ("wide 24", "wide 48", "wide 144"):
        assert candidate["results"][key] == [1750.0, 1750.0]
    assert any(sum(entry[0].startswith("_GUARD_LOCAL") for entry in candidate["traces"][key]) >= 20
               for key in ("wide 24", "wide 48")), candidate["traces"].keys()
    assert not any(entry[0].startswith("_GUARD_LOCAL") for entry in candidate["traces"]["wide 144"])
    assert not any(entry[0].startswith("_GUARD_LOCAL")
                   for entries in noopt["traces"].values() for entry in entries)
    print("OK    loop types preserve induction, overflow, callback mutation, refusal progress and capacity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
