import json
import os
import subprocess
import sys


PROGRAM = r'''
import _opcode
import _testcapi
import _testinternalcapi
import dis
import gc
import json
import sys
import weakref

WARM = 7000
# Opcode snapshots can schedule GC; ordinary iterations let the caller reenter JIT before a fault.
FAULT_ITERATIONS = WARM + 16
RESULTS = {}
TRACES = {}
ACTIVE = []
EVENTS = []

def trace(function):
    entries = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            entries.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            continue
    return entries

def build(label, expression):
    source = f"""def arithmetic(a, b):
    return {expression}

def loop(n, fn, source, active):
    value = None
    was_active = False
    entries = []
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = fn(a, b)
        if index == {WARM - 1}:
            entries = trace(loop)
    return value, was_active, entries
"""
    namespace = {"trace": trace}
    exec(compile(source, f"float_leaf_{label}.py", "exec"), namespace)
    return namespace["arithmetic"], namespace["loop"]

functions = {}
for label, expression in [("add", "a + b"), ("reverse add", "b + a"),
        ("subtract", "a - b"), ("reverse subtract", "b - a"),
        ("multiply", "a * b"), ("reverse multiply", "b * a"),
        ("divide", "a / b"), ("reverse divide", "b / a")]:
    fn, loop = build(label, expression)
    value, active, entries = loop(WARM, fn, [(1.25, 2.5)] * WARM, sys._jit.is_active)
    RESULTS[label] = value.hex()
    TRACES[label] = entries
    functions[label] = fn, loop

specials = {}
for label, (fn, loop) in functions.items():
    values = []
    for x, y in [(0.0, -0.0), (-0.0, 2.0), (float("inf"), -float("inf")),
                 (float("nan"), 1.0), (-2.5, -1.25)]:
        divisor = x if label == "reverse divide" else y
        if "divide" in label and divisor == 0.0:
            continue
        value, _, _ = loop(20, fn, [(x, y)] * 20, sys._jit.is_active)
        values.append(value.hex())
    specials[label] = values
RESULTS["specials"] = specials

def fault(fn, loop, source, x, y):
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            loop(FAULT_ITERATIONS, fn, source, sys._jit.is_active)
        except Exception as error:
            frames = []
            tb = error.__traceback__
            while tb:
                frame = tb.tb_frame
                instruction = next(value for value in dis.get_instructions(frame.f_code)
                                   if value.offset == tb.tb_lasti)
                positions = instruction.positions
                locals_match = []
                if frame.f_code is fn.__code__:
                    locals_match = [frame.f_locals["a"] is x, frame.f_locals["b"] is y]
                if frame.f_code is loop.__code__:
                    ACTIVE.append(frame.f_locals["was_active"])
                    TRACES["error " + fn.__code__.co_filename] = frame.f_locals["entries"]
                frames.append([frame.f_code.co_name, instruction.opname, tb.tb_lineno,
                    [positions.lineno, positions.end_lineno, positions.col_offset, positions.end_col_offset],
                    locals_match])
                tb = tb.tb_next
            return [type(error).__name__, str(error), str(error.__context__), frames]
        raise AssertionError("arithmetic did not fail")

for label, x, y in [("divide", 1.25, 0.0), ("reverse divide", -0.0, 2.5)]:
    fn, loop = functions[label]
    RESULTS["error " + label] = fault(fn, loop, [(x, y)] + [(1.25, 2.5)] * (FAULT_ITERATIONS - 1), x, y)

class ArmAllocator:
    def __del__(self):
        _testcapi.set_nomemory(0, 1)

def allocation_loop(n, fn, source, active, a, b):
    held = [None] * n
    transient = None
    was_active = False
    entries = []
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(a, b)
        if index == WARM - 1:
            entries = trace(allocation_loop)
    return held

fn, _ = functions["add"]
try:
    raise ValueError("outer")
except ValueError:
    try:
        allocation_loop(WARM + 2, fn, [0, ArmAllocator()] + [0] * WARM,
                        sys._jit.is_active, 1.25, 2.5)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        tb = error.__traceback__
        frames = []
        while tb:
            frame = tb.tb_frame
            instruction = next(value for value in dis.get_instructions(frame.f_code)
                               if value.offset == tb.tb_lasti)
            if frame.f_code is allocation_loop.__code__:
                ACTIVE.append(frame.f_locals["was_active"])
                TRACES["allocation"] = frame.f_locals["entries"]
            frames.append([frame.f_code.co_name, instruction.opname, tb.tb_lineno,
                [frame.f_locals["a"] == 1.25, frame.f_locals["b"] == 2.5]
                if frame.f_code is fn.__code__ else []])
            tb = tb.tb_next
        RESULTS["allocation"] = [str(error), str(error.__context__), frames]
    else:
        raise AssertionError("float allocation did not fail")
    finally:
        _testcapi.remove_mem_hooks()

def error_release_loop(n, funcs, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = funcs.pop()(a, b)
        if index == WARM - 1:
            entries = trace(error_release_loop)
    return value

def sole_error():
    namespace = {}
    exec("def arithmetic(a,b):\n    return a / b\n", namespace)
    remaining = [namespace.pop("arithmetic")] * FAULT_ITERATIONS
    function_ref = weakref.ref(remaining[0])
    code_ref = weakref.ref(remaining[0].__code__)
    try:
        error_release_loop(FAULT_ITERATIONS, remaining, [(1.25, 0.0)] + [(1.25, 2.5)] * (FAULT_ITERATIONS - 1),
                           sys._jit.is_active)
    except ZeroDivisionError as error:
        alive = function_ref() is not None and code_ref() is not None
        tb = error.__traceback__
        while tb.tb_next:
            if tb.tb_frame.f_code is error_release_loop.__code__:
                ACTIVE.append(tb.tb_frame.f_locals["was_active"])
                entries = tb.tb_frame.f_locals["entries"]
            tb = tb.tb_next
        frame = tb.tb_frame
        instruction = next(value for value in dis.get_instructions(frame.f_code)
                           if value.offset == tb.tb_lasti)
        result = [alive, len(remaining), frame.f_code.co_name, instruction.opname,
                  frame.f_locals["a"].hex(), frame.f_locals["b"].hex()]
        return result, entries, function_ref, code_ref
    raise AssertionError("sole-reference arithmetic did not fail")

result, entries, sole_function_ref, sole_code_ref = sole_error()
gc.collect()
RESULTS["sole error"] = [result, sole_function_ref() is None, sole_code_ref() is None]
TRACES["sole error"] = entries

namespace = {}
exec("def released(a,b):\n    return a + b\n", namespace)
remaining = [namespace.pop("released")] * FAULT_ITERATIONS
def observe(label):
    frame = sys._getframe(2)
    EVENTS.append([label, frame.f_code.co_name, str(sys.exception())])
    ACTIVE.append(frame.f_locals["was_active"])
function_ref = weakref.ref(remaining[0], lambda _: observe("function"))
code_ref = weakref.ref(remaining[0].__code__, lambda _: observe("code"))

def released_loop(n, source, x, y, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        value = source.pop()(x, y)
        if index == WARM - 1:
            entries = trace(released_loop)
    return value, entries

try:
    raise ValueError("outer")
except ValueError:
    value, entries = released_loop(FAULT_ITERATIONS, remaining, 2.0, 3.0, sys._jit.is_active)
RESULTS["released"] = [value.hex(), function_ref() is None, code_ref() is None, EVENTS]
TRACES["released"] = entries

class Override(float):
    def __add__(self, other):
        frame = sys._getframe(1)
        return [frame.f_code.co_name, frame.f_back.f_code.co_name, str(sys.exception())]

fn, loop = functions["add"]
try:
    raise ValueError("outer")
except ValueError:
    value, _, _ = loop(20, fn, [(Override(1.25), 2.5)] * 20, sys._jit.is_active)
RESULTS["subclass"] = value

observed = []
def profiler(frame, event, arg):
    if event == "return" and frame.f_code is fn.__code__:
        observed.append([frame.f_locals["a"].hex(), frame.f_locals["b"].hex(), arg.hex()])

sys.setprofile(profiler)
loop(8, fn, [(1.25, 2.5)] * 8, sys._jit.is_active)
sys.setprofile(None)
RESULTS["profile"] = observed.copy()
observed.clear()

def tracer(frame, event, arg):
    profiler(frame, event, arg)
    return tracer
sys.settrace(tracer)
loop(8, fn, [(1.25, 2.5)] * 8, sys._jit.is_active)
sys.settrace(None)
RESULTS["trace"] = observed.copy()
observed.clear()

def returned(code, offset, value):
    if code is fn.__code__:
        frame = sys._getframe(1)
        observed.append([frame.f_locals["a"].hex(), frame.f_locals["b"].hex(), value.hex()])
sys.monitoring.use_tool_id(4, "float leaf integration")
sys.monitoring.register_callback(4, sys.monitoring.events.PY_RETURN, returned)
sys.monitoring.set_events(4, sys.monitoring.events.PY_RETURN)
loop(8, fn, [(1.25, 2.5)] * 8, sys._jit.is_active)
sys.monitoring.set_events(4, 0)
sys.monitoring.free_tool_id(4)
RESULTS["monitor"] = observed.copy()

def alternate(a, b):
    return 99.0
old_code = fn.__code__
fn.__code__ = alternate.__code__
_testinternalcapi.clear_executor_deletion_list()
RESULTS["invalidated"] = not trace(loop)
value, _, _ = loop(20, fn, [(1.25, 2.5)] * 20, sys._jit.is_active)
RESULTS["rebound"] = value.hex()
fn.__code__ = old_code
gc.collect()
print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE}, sort_keys=True))
'''


def run(jit):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0"),
        capture_output=True, text=True, timeout=45)
    assert completed.returncode == 0, (jit, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference = run(True), run(False)
    assert candidate["results"] == reference["results"], (candidate["results"], reference["results"])
    values = {"add": 3.75, "reverse add": 3.75, "subtract": -1.25,
        "reverse subtract": 1.25, "multiply": 3.125, "reverse multiply": 3.125,
        "divide": 0.5, "reverse divide": 2.0}
    for label, value in values.items():
        assert reference["results"][label] == value.hex(), (label, reference["results"][label])
    for label in ("divide", "reverse divide"):
        fault = reference["results"]["error " + label]
        assert fault[:3] == ["ZeroDivisionError", "division by zero", "outer"], fault
        assert [frame[0] for frame in fault[3]] == ["fault", "loop", "arithmetic"], fault
        assert fault[3][-1][:3] == ["arithmetic", "BINARY_OP", 2], fault
        assert fault[3][-1][-1] == [True, True], fault
    allocation = reference["results"]["allocation"]
    assert allocation[:2] == ["", "None"], allocation
    assert allocation[-1][-1] == ["arithmetic", "BINARY_OP", 2, [True, True]], allocation
    assert reference["results"]["sole error"] == [
        [True, 0, "arithmetic", "BINARY_OP", 1.25.hex(), 0.0.hex()], True, True]
    assert reference["results"]["released"] == [5.0.hex(), True, True,
        [["function", "released_loop", "outer"], ["code", "released_loop", "outer"]]]
    assert reference["results"]["subclass"] == ["arithmetic", "loop", "outer"]
    expected_observer = [[1.25.hex(), 2.5.hex(), 3.75.hex()]] * 8
    for kind in ("profile", "trace", "monitor"):
        assert reference["results"][kind] == expected_observer, (kind, reference["results"][kind])
    assert reference["results"]["invalidated"] is True
    assert reference["results"]["rebound"] == 99.0.hex()
    assert candidate["active"] == [True] * 6, candidate["active"]
    required = {*values, "released", "allocation", "sole error", "error float_leaf_divide.py",
                "error float_leaf_reverse divide.py"}
    for label in required:
        entries = candidate["traces"][label]
        names = {entry[0] for entry in entries}
        assert "_CALL_PY_FLOAT_LEAF" in names, (label, names)
        assert "_MATERIALIZE_FLOAT_LEAF_ERROR" in names, (label, names)
        assert not names.intersection({"_PUSH_FRAME", "_RETURN_VALUE"}), (label, names)
    print("OK    float calls preserve arithmetic, materialized errors, finalizers and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
