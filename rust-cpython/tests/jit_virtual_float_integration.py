import json
import os
import subprocess
import sys


PROGRAM = r'''
import _opcode
import _testcapi
import dis
import gc
import json
import sys
import weakref

WARM = 7000
FAULT = WARM + 16
RESULTS = {}
TRACES = {}
ACTIVE = []

def trace(function):
    entries = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            entries.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            continue
    return entries

def build(expression):
    namespace = {"trace": trace}
    source = f"""def arithmetic(a, b):
    return {expression}

def loop(n, fn, source, active):
    was_active = False
    entries = []
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = fn(a, b)
        if index == {WARM - 1}:
            entries = trace(loop)
    return value, entries
"""
    exec(compile(source, "virtual_float.py", "exec"), namespace)
    return namespace["arithmetic"], namespace["loop"]

expressions = ["(a + b) * a", "a * (a - b)", "(a / b) + (b / a)",
    "a + (" * 24 + "b" + ")" * 24]
functions = []
for index, expression in enumerate(expressions):
    fn, loop = build(expression)
    value, entries = loop(WARM, fn, [(1.25, 2.5)] * WARM, sys._jit.is_active)
    RESULTS[f"value {index}"] = value.hex()
    TRACES[f"value {index}"] = entries
    functions.append((fn, loop))
RESULTS["wide stack"] = functions[-1][0].__code__.co_stacksize > functions[-1][1].__code__.co_stacksize

fn, loop = functions[2]
for label, a, b in (("first", 1.25, 0.0), ("second", -0.0, 2.5)):
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            loop(FAULT, fn, [(a, b)] + [(1.25, 2.5)] * (FAULT - 1), sys._jit.is_active)
        except ZeroDivisionError as error:
            frames = []
            tb = error.__traceback__
            while tb:
                frame = tb.tb_frame
                instruction = next(value for value in dis.get_instructions(frame.f_code)
                                   if value.offset == tb.tb_lasti)
                if frame.f_code is loop.__code__:
                    ACTIVE.append(frame.f_locals["was_active"])
                    TRACES[label] = frame.f_locals["entries"]
                frames.append([frame.f_code.co_name, instruction.opname, tb.tb_lineno,
                    instruction.positions.col_offset, instruction.positions.end_col_offset,
                    [frame.f_locals["a"] is a, frame.f_locals["b"] is b]
                    if frame.f_code is fn.__code__ else []])
                tb = tb.tb_next
            RESULTS[label] = [str(error), str(error.__context__), frames]
        else:
            raise AssertionError("virtual arithmetic did not fail")

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

fn = functions[0][0]
last_binary = [value for value in dis.get_instructions(fn) if value.opname == "BINARY_OP"][-1]
RESULTS["allocation expected position"] = [last_binary.positions.col_offset, last_binary.positions.end_col_offset]
try:
    raise ValueError("outer")
except ValueError:
    try:
        allocation_loop(FAULT + 2, fn, [None, ArmAllocator()] + [None] * FAULT,
                        sys._jit.is_active, 1.25, 2.5)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        frames = []
        tb = error.__traceback__
        while tb:
            frame = tb.tb_frame
            instruction = next(value for value in dis.get_instructions(frame.f_code)
                               if value.offset == tb.tb_lasti)
            if frame.f_code is allocation_loop.__code__:
                ACTIVE.append(frame.f_locals["was_active"])
                TRACES["allocation"] = frame.f_locals["entries"]
            frames.append([frame.f_code.co_name, instruction.opname,
                instruction.positions.col_offset, instruction.positions.end_col_offset,
                [frame.f_locals["a"] == 1.25, frame.f_locals["b"] == 2.5]
                if frame.f_code is fn.__code__ else []])
            tb = tb.tb_next
        RESULTS["allocation"] = [str(error), str(error.__context__), frames]
    else:
        raise AssertionError("virtual result allocation did not fail")
    finally:
        _testcapi.remove_mem_hooks()

def sole_loop(n, funcs, source, active):
    was_active = False
    entries = []
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = funcs.pop()(a, b)
        if index == WARM - 1:
            entries = trace(sole_loop)
    return value

def sole_error():
    namespace = {}
    exec("def failing(a,b):\n    return (a / b) + (b / a)\n", namespace)
    remaining = [namespace.pop("failing")] * FAULT
    function_ref = weakref.ref(remaining[0])
    code_ref = weakref.ref(remaining[0].__code__)
    try:
        sole_loop(FAULT, remaining, [(-0.0, 2.5)] + [(1.25, 2.5)] * (FAULT - 1), sys._jit.is_active)
    except ZeroDivisionError as error:
        tb = error.__traceback__
        entries = []
        while tb.tb_next:
            if tb.tb_frame.f_code is sole_loop.__code__:
                ACTIVE.append(tb.tb_frame.f_locals["was_active"])
                entries = tb.tb_frame.f_locals["entries"]
            tb = tb.tb_next
        frame = tb.tb_frame
        instruction = next(value for value in dis.get_instructions(frame.f_code)
                           if value.offset == tb.tb_lasti)
        result = [function_ref() is not None, code_ref() is not None, len(remaining),
            frame.f_code.co_name, instruction.opname,
            frame.f_locals["a"].hex(), frame.f_locals["b"].hex()]
        return result, entries, function_ref, code_ref
    raise AssertionError("sole-callable virtual arithmetic did not fail")

sole_result, entries, sole_function_ref, sole_code_ref = sole_error()
gc.collect()
RESULTS["sole error"] = [sole_result, sole_function_ref() is None, sole_code_ref() is None]
TRACES["sole error"] = entries

def multiple_loop(n, first, second, source, active):
    was_active = False
    entries = []
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = first(a, b) + second(a, b)
        if index == WARM - 1:
            entries = trace(multiple_loop)
    return value, entries

def multiple_case():
    value, entries = multiple_loop(WARM, functions[0][0], functions[2][0],
        [(1.25, 2.5)] * WARM, sys._jit.is_active)
    RESULTS["multiple"] = value.hex()
    TRACES["multiple"] = entries
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            multiple_loop(FAULT, functions[0][0], functions[2][0],
                [(0.0, 2.5)] + [(1.25, 2.5)] * (FAULT - 1), sys._jit.is_active)
        except ZeroDivisionError as error:
            frames = []
            tb = error.__traceback__
            while tb:
                frame = tb.tb_frame
                instruction = next(value for value in dis.get_instructions(frame.f_code)
                                   if value.offset == tb.tb_lasti)
                if frame.f_code is multiple_loop.__code__:
                    ACTIVE.append(frame.f_locals["was_active"])
                    TRACES["multiple error"] = frame.f_locals["entries"]
                frames.append([frame.f_code.co_name, instruction.opname,
                    instruction.positions.col_offset, instruction.positions.end_col_offset])
                tb = tb.tb_next
            RESULTS["multiple error"] = [str(error), str(error.__context__), frames]
        else:
            raise AssertionError("second virtual call did not fail")

multiple_case()

def nested(n):
    total = 0.0
    for i in range(n):
        total = total + 1.0
    return total
nested(WARM)

def released_loop(n, source, active):
    was_active = False
    entries = []
    for index in range(n):
        was_active = active()
        value = source.pop()(1.25, 2.5)
        if index == WARM - 1:
            entries = trace(released_loop)
    return value, entries

namespace = {}
exec("def released(a,b):\n    return (a + b) * a\n", namespace)
remaining = [namespace.pop("released")] * FAULT
events = []
def finalized(label):
    frame = sys._getframe(2)
    ACTIVE.append(frame.f_locals["was_active"])
    events.append([label, frame.f_code.co_name, str(sys.exception()), nested(20)])
    gc.collect()
function_ref = weakref.ref(remaining[0], lambda _: finalized("function"))
code_ref = weakref.ref(remaining[0].__code__, lambda _: finalized("code"))
try:
    raise ValueError("outer")
except ValueError:
    value, entries = released_loop(FAULT, remaining, sys._jit.is_active)
RESULTS["released"] = [value.hex(), function_ref() is None, code_ref() is None, events]
TRACES["released"] = entries

fn, loop = functions[0]
class Override(float):
    def __add__(self, other):
        frame = sys._getframe(1)
        return float(self) + other + len(frame.f_locals)
RESULTS["subclass"] = loop(20, fn, [(Override(1.25), 2.5)] * 20, sys._jit.is_active)[0].hex()

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
def monitored_return(code, offset, value):
    if code is fn.__code__:
        frame = sys._getframe(1)
        observed.append([frame.f_code.co_name, frame.f_locals["a"].hex(), value.hex()])
sys.monitoring.use_tool_id(4, "virtual float integration")
sys.monitoring.register_callback(4, sys.monitoring.events.PY_RETURN, monitored_return)
sys.monitoring.set_events(4, sys.monitoring.events.PY_RETURN)
loop(8, fn, [(1.25, 2.5)] * 8, sys._jit.is_active)
sys.monitoring.set_events(4, 0)
sys.monitoring.free_tool_id(4)
RESULTS["monitor"] = observed.copy()

fn.__code__ = (lambda a,b: a - b).__code__
RESULTS["rebound"] = loop(20, fn, [(1.25, 2.5)] * 20, sys._jit.is_active)[0].hex()
print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE}, sort_keys=True))
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, (jit, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    assert candidate["results"] == reference["results"] == noopt["results"], (
        candidate["results"], reference["results"], noopt["results"])
    assert reference["results"]["wide stack"] is True
    assert reference["results"]["value 0"] == (4.6875).hex()
    assert reference["results"]["value 1"] == (-1.5625).hex()
    assert reference["results"]["value 2"] == (2.5).hex()
    assert reference["results"]["value 3"] == (32.5).hex()
    assert reference["results"]["first"][0:2] == ["division by zero", "outer"]
    assert reference["results"]["second"][0:2] == ["division by zero", "outer"]
    assert reference["results"]["first"][2][-1][3:5] != reference["results"]["second"][2][-1][3:5]
    assert reference["results"]["allocation"][0:2] == ["", "None"]
    assert reference["results"]["allocation"][2][-1][2:4] == reference["results"]["allocation expected position"]
    assert reference["results"]["allocation"][2][-1][-1] == [True, True]
    assert reference["results"]["sole error"] == [
        [True, True, 0, "failing", "BINARY_OP", (-0.0).hex(), (2.5).hex()], True, True]
    assert reference["results"]["multiple"] == (7.1875).hex()
    assert reference["results"]["multiple error"][0:2] == ["division by zero", "outer"]
    expected_observers = [[(1.25).hex(), (2.5).hex(), (4.6875).hex()]] * 8
    assert reference["results"]["profile"] == reference["results"]["trace"] == expected_observers
    assert reference["results"]["monitor"] == [["arithmetic", (1.25).hex(), (4.6875).hex()]] * 8
    assert reference["results"]["released"] == [(4.6875).hex(), True, True,
        [["function", "released_loop", "outer", 20.0], ["code", "released_loop", "outer", 20.0]]]
    assert reference["results"]["rebound"] == (-1.25).hex()
    assert candidate["active"] == [True] * 7, candidate["active"]
    for key, entries in candidate["traces"].items():
        names = [entry[0] for entry in entries]
        assert "_BEGIN_VIRTUAL_CALL" in names, (key, names)
        assert "_RETURN_VIRTUAL_CALL" in names, (key, names)
        assert "_MATERIALIZE_VIRTUAL_FRAME" in names, (key, names)
        if key.startswith("multiple"):
            assert names.count("_BEGIN_VIRTUAL_CALL") >= 2, (key, names)
    assert all("_BEGIN_VIRTUAL_CALL" not in {entry[0] for entry in entries}
               for entries in noopt["traces"].values())
    print("OK    virtual float calls preserve scratch bounds, distinct errors, finalizers and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
