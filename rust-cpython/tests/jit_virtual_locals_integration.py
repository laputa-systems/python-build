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

def arithmetic(a, b):
    first = a + b
    alias = first
    first = first * a
    second = (first / b) + (b / a)
    unused = a + b
    return second + unused + alias

def make_loop():
    namespace = {"trace": trace, "WARM": WARM}
    exec("""def loop(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = fn(a, b)
        if index == WARM - 1:
            entries = trace(loop)
    return value, entries
""", namespace)
    return namespace["loop"]

loop = make_loop()

def generator(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        a, b = source.pop()
        value = fn(a, b)
        if index == WARM - 1:
            entries = trace(generator)
        yield value

def capture(error, fn, caller, key):
    frames = []
    tb = error.__traceback__
    while tb:
        frame = tb.tb_frame
        instruction = next(value for value in dis.get_instructions(frame.f_code)
                           if value.offset == tb.tb_lasti)
        if frame.f_code is caller.__code__:
            ACTIVE.append(frame.f_locals["was_active"])
            TRACES[key] = frame.f_locals["entries"]
        local_values = {}
        if frame.f_code is fn.__code__:
            local_values = {name: value.hex() for name, value in frame.f_locals.items()}
        frames.append([frame.f_code.co_name, instruction.opname,
            instruction.positions.col_offset, instruction.positions.end_col_offset, local_values])
        tb = tb.tb_next
    return [type(error).__name__, str(error), str(error.__context__), frames]

def allocating(a, b):
    first = a + b
    second = first * a
    return second

class ArmAllocator:
    def __del__(self):
        _testcapi.set_nomemory(0, 1)

def allocation_loop(n, fn, source, active, a, b):
    held = [None] * n
    entries = []
    was_active = False
    transient = None
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(a, b)
        if index == WARM - 1:
            entries = trace(allocation_loop)
    return held

def argument_write(a, b):
    a = a + b
    return a * b

def unassigned(a, b, initialize):
    if initialize:
        first = a + b
    return first * a

def nested(n):
    value = 0.0
    for i in range(n):
        value = value + 1.0
    return value

def released_loop(n, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        value = source.pop()(1.25, 2.5)
        if index == WARM - 1:
            entries = trace(released_loop)
    return value, entries

def run_case():
    a, b = 1.25, 2.5
    before = sys.getrefcount(a), sys.getrefcount(b)
    value, entries = loop(WARM, arithmetic, [(a, b)] * WARM, sys._jit.is_active)
    RESULTS["value"] = value.hex()
    RESULTS["refcount delta"] = [sys.getrefcount(a) - before[0], sys.getrefcount(b) - before[1]]
    TRACES["arithmetic"] = entries
    for key, x, y in (("first", 1.25, 0.0), ("second", -0.0, 2.5)):
        try:
            raise ValueError("outer")
        except ValueError:
            try:
                loop(FAULT, arithmetic, [(x, y)] + [(a, b)] * (FAULT - 1), sys._jit.is_active)
            except ZeroDivisionError as error:
                RESULTS[key] = capture(error, arithmetic, loop, key)
            else:
                raise AssertionError("local arithmetic did not fail")
    values = list(generator(WARM, arithmetic, [(a, b)] * WARM, sys._jit.is_active))
    RESULTS["generator value"] = [len(values), values[-1].hex()]
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            list(generator(FAULT, arithmetic, [(-0.0, b)] + [(a, b)] * (FAULT - 1), sys._jit.is_active))
        except ZeroDivisionError as error:
            RESULTS["generator error"] = capture(error, arithmetic, generator, "generator")
        else:
            raise AssertionError("generator arithmetic did not fail")
    last_binary = [value for value in dis.get_instructions(allocating) if value.opname == "BINARY_OP"][-1]
    RESULTS["allocation expected position"] = [last_binary.positions.col_offset, last_binary.positions.end_col_offset]
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            allocation_loop(FAULT + 2, allocating, [None, ArmAllocator()] + [None] * FAULT,
                sys._jit.is_active, a, b)
        except MemoryError as error:
            _testcapi.remove_mem_hooks()
            RESULTS["allocation"] = capture(error, allocating, allocation_loop, "allocation")
        else:
            raise AssertionError("local arithmetic allocation did not fail")
        finally:
            _testcapi.remove_mem_hooks()
    argument_loop = make_loop()
    RESULTS["argument write"], TRACES["argument write"] = argument_loop(
        WARM, argument_write, [(a, b)] * WARM, sys._jit.is_active)
    RESULTS["initialized"] = unassigned(a, b, True)
    try:
        unassigned(a, b, False)
    except UnboundLocalError as error:
        RESULTS["unassigned"] = [type(error).__name__, str(error)]
    else:
        raise AssertionError("unassigned local did not fail")

    nested(WARM)
    namespace = {}
    exec("def released(a,b):\n    first = a + b\n    alias = first\n    second = first * a\n    return second + alias\n", namespace)
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

    observed = []
    def profiler(frame, event, arg):
        if event == "return" and frame.f_code is arithmetic.__code__:
            observed.append([arg.hex(), sorted(frame.f_locals),
                frame.f_locals["alias"].hex(), frame.f_locals["first"].hex(),
                frame.f_locals["alias"] is frame.f_locals["first"]])
    sys.setprofile(profiler)
    loop(8, arithmetic, [(a, b)] * 8, sys._jit.is_active)
    sys.setprofile(None)
    RESULTS["profile"] = observed.copy()
    observed.clear()
    def tracer(frame, event, arg):
        profiler(frame, event, arg)
        return tracer
    sys.settrace(tracer)
    loop(8, arithmetic, [(a, b)] * 8, sys._jit.is_active)
    sys.settrace(None)
    RESULTS["trace"] = observed.copy()

run_case()
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
    expected_value = (11.375).hex()
    assert reference["results"]["value"] == expected_value
    assert reference["results"]["refcount delta"] == [0, 0]
    assert reference["results"]["generator value"] == [7000, expected_value]
    assert reference["results"]["generator error"][:3] == ["ZeroDivisionError", "division by zero", "outer"]
    assert reference["results"]["generator error"][-1][-2][0] == "generator"
    assert reference["results"]["generator error"][-1][-1][-1] == reference["results"]["second"][-1][-1][-1]
    for key, a, b in (("first", 1.25, 0.0), ("second", -0.0, 2.5)):
        error = reference["results"][key]
        assert error[:3] == ["ZeroDivisionError", "division by zero", "outer"]
        assert error[-1][-1][-1] == {"a": a.hex(), "b": b.hex(),
            "first": ((a + b) * a).hex(), "alias": (a + b).hex()}, error
    allocation = reference["results"]["allocation"]
    assert allocation[:3] == ["MemoryError", "", "None"]
    assert allocation[-1][-1][2:4] == reference["results"]["allocation expected position"]
    assert allocation[-1][-1][-1] == {"a": (1.25).hex(), "b": (2.5).hex(), "first": (3.75).hex()}
    assert reference["results"]["argument write"] == 9.375
    assert reference["results"]["initialized"] == 4.6875
    assert reference["results"]["unassigned"][0] == "UnboundLocalError"
    assert reference["results"]["released"] == [(8.4375).hex(), True, True,
        [["function", "released_loop", "outer", 20.0], ["code", "released_loop", "outer", 20.0]]]
    expected_observation = [expected_value, ["a", "alias", "b", "first", "second", "unused"],
        (3.75).hex(), (4.6875).hex(), False]
    assert reference["results"]["profile"] == reference["results"]["trace"] == [expected_observation] * 8
    assert candidate["active"] == [True] * 6, candidate["active"]
    for key in ("arithmetic", "first", "second", "generator", "allocation", "released"):
        names = {entry[0] for entry in candidate["traces"][key]}
        assert {"_BEGIN_VIRTUAL_FLOAT_CALL", "_RETURN_VIRTUAL_FLOAT_CALL",
                "_MATERIALIZE_VIRTUAL_FLOAT_FRAME"} <= names, (key, names)
    assert "_BEGIN_VIRTUAL_FLOAT_CALL" not in {entry[0] for entry in candidate["traces"]["argument write"]}
    assert all("_BEGIN_VIRTUAL_FLOAT_CALL" not in {entry[0] for entry in entries}
               for entries in noopt["traces"].values())
    print("OK    virtual float locals preserve aliases, NULL slots, errors, refcounts and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
