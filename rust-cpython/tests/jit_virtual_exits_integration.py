import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import _testcapi
import dis
import json
import sys

WARM = 7000
FAULT = WARM + 16
RESULTS = {}
TRACES = {}
ACTIVE = []

def encode(value):
    if isinstance(value, complex):
        return ["complex", value.real.hex(), value.imag.hex()]
    return ["float", value.hex()]

def trace(function):
    entries = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            entries.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            continue
    return entries

class Source:
    def __init__(self, values):
        self.values = values
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

def build(expression, prefix="", unary=False):
    namespace = {}
    parameters = "a" if unary else "a, b"
    exec(f"def power({parameters}):\n{prefix}    return {expression}\n", namespace)
    caller = {"trace": trace, "WARM": WARM}
    exec(f"""def loop(n, fn, source, active):
    entries = []
    observed = []
    was_active = False
    for index in range(n):
        a, b = source.pop()
        was_active = active()
        value = fn({parameters})
        if isinstance(value, complex):
            observed.append(was_active)
        if index == {WARM - 1}:
            entries = trace(loop)
    return value, entries, observed
""", caller)
    return namespace["power"], caller["loop"]

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
            local_values = {name: encode(value) for name, value in frame.f_locals.items()}
        frames.append([frame.f_code.co_name, instruction.opname,
            instruction.positions.col_offset, instruction.positions.end_col_offset, local_values])
        tb = tb.tb_next
    return [type(error).__name__, str(error), str(error.__context__), frames]

class ArmAllocator:
    def __del__(self):
        _testcapi.set_nomemory(0, 1)

def allocation_loop(n, fn, source, active, a, b):
    held = [None] * n
    entries = []
    transient = None
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(a, b)
        if index == WARM - 1:
            entries = trace(allocation_loop)
    return held

def generator(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        a, b = source.pop()
        was_active = active()
        value = fn(a, b)
        if index == WARM - 1:
            entries = trace(generator)
        if isinstance(value, complex):
            ACTIVE.append(was_active)
            TRACES["generator"] = entries
        yield value

def run_case():
    functions = {}
    for label, expression, prefix, unary in (
            ("scalar", "a ** b", "", False),
            ("locals", "(first ** b) + a", "    first = a + b\n", False),
            ("after", "result / a", "    first = a + b\n    result = first ** b\n", False),
            ("repeated", "a + a", "", False),
            ("unary", "a * a", "", True)):
        fn, loop = build(expression, prefix, unary)
        source = Source([(2.0, 2.0)] * WARM)
        value, entries, observed = loop(WARM, fn, source, sys._jit.is_active)
        RESULTS[label] = [encode(value), source.calls, len(source.values)]
        TRACES[label] = entries
        functions[label] = fn, loop
    for label in ("scalar", "locals"):
        fn, loop = functions[label]
        source = Source([(-2.0, 0.5)] * 8 + [(2.0, 2.0)] * (FAULT - 8))
        try:
            raise ValueError("outer")
        except ValueError:
            value, entries, observed = loop(FAULT, fn, source, sys._jit.is_active)
            context = str(sys.exception())
        ACTIVE.extend(observed)
        RESULTS["complex " + label] = [encode(value), source.calls, len(source.values), context, len(observed)]
        TRACES["complex " + label] = entries
    specials = []
    fn, loop = functions["scalar"]
    for a, b in ((0.0, 0.0), (-0.0, 3.0), (-0.0, 2.0),
            (float("nan"), 0.0), (1.0, float("nan")), (float("nan"), 2.0),
            (float("inf"), -2.0), (-float("inf"), 3.0), (-2.0, 3.0),
            (0.5, float("inf")), (2.0, -float("inf")), (-2.0, 0.5)):
        source = Source([(a, b)] * 20)
        value, _, _ = loop(20, fn, source, sys._jit.is_active)
        specials.append([encode(value), source.calls, len(source.values)])
    RESULTS["specials"] = specials
    for label, kernel, a, b in (("zero", "locals", 1.0, -1.0),
            ("overflow", "locals", 1e308, 2.0),
            ("after complex", "after", 0.0, -0.5),
            ("after real", "after", 0.0, 0.5)):
        fn, loop = functions[kernel]
        source = Source([(a, b)] + [(2.0, 2.0)] * (FAULT - 1))
        try:
            raise ValueError("outer")
        except ValueError:
            try:
                loop(FAULT, fn, source, sys._jit.is_active)
            except (ZeroDivisionError, OverflowError) as error:
                RESULTS[label] = capture(error, fn, loop, label)
                RESULTS[label + " count"] = [source.calls, len(source.values)]
            else:
                raise AssertionError("power arithmetic did not fail")
    fn, _ = functions["scalar"]
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            allocation_loop(FAULT + 2, fn, [None, ArmAllocator()] + [None] * FAULT,
                sys._jit.is_active, 2.0, 2.0)
        except MemoryError as error:
            _testcapi.remove_mem_hooks()
            RESULTS["allocation"] = capture(error, fn, allocation_loop, "allocation")
        else:
            raise AssertionError("power allocation did not fail")
        finally:
            _testcapi.remove_mem_hooks()
    fn, _ = functions["locals"]
    source = Source([(-2.0, 0.5)] * 8 + [(2.0, 2.0)] * (FAULT - 8))
    try:
        raise ValueError("outer")
    except ValueError:
        values = list(generator(FAULT, fn, source, sys._jit.is_active))
    RESULTS["generator"] = [len(values), encode(values[-1]), source.calls, len(source.values)]

run_case()
print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE}, sort_keys=True))
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, (jit, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    assert candidate["results"] == reference["results"] == noopt["results"], (
        candidate["results"], reference["results"], noopt["results"])
    for label, value in (("scalar", 4.0), ("locals", 18.0), ("after", 8.0),
                         ("repeated", 4.0), ("unary", 4.0)):
        assert reference["results"][label] == [["float", value.hex()], 7000, 0]
    for label in ("scalar", "locals"):
        value = reference["results"]["complex " + label]
        assert value[0][0] == "complex" and value[1:] == [7016, 0, "outer", 8], (label, value)
    assert reference["results"]["zero"][:3] == ["ZeroDivisionError", "zero to a negative power", "outer"]
    assert reference["results"]["overflow"][0] == "OverflowError"
    assert reference["results"]["after complex"][:3] == ["ZeroDivisionError", "division by zero", "outer"]
    assert reference["results"]["after complex"][-1][-1][-1]["result"][0] == "complex"
    assert reference["results"]["after real"][:3] == ["ZeroDivisionError", "division by zero", "outer"]
    assert reference["results"]["after real"][-1][-1][-1]["result"][0] == "float"
    for label in ("zero", "overflow", "after complex", "after real"):
        assert reference["results"][label + " count"] == [7016, 0]
    assert reference["results"]["allocation"][:3] == ["MemoryError", "", "None"]
    assert reference["results"]["generator"][0] == 7016
    assert reference["results"]["generator"][1][0] == "complex"
    assert reference["results"]["generator"][2:] == [7016, 0]
    assert candidate["active"] == [True] * 29, candidate["active"]
    for label, entries in candidate["traces"].items():
        names = [entry[0] for entry in entries]
        assert "_BEGIN_VIRTUAL_CALL" in names, (label, names)
        assert "_RETURN_VIRTUAL_CALL" in names, (label, names)
        assert "_MATERIALIZE_VIRTUAL_FRAME" in names, (label, names)
        if label not in ("repeated", "unary"):
            assert "_BINARY_OP_POWER_FLOAT" in names, (label, names)
            assert names.count("_MATERIALIZE_VIRTUAL_FRAME") >= 2, (label, names)
    assert all("_BEGIN_VIRTUAL_CALL" not in {entry[0] for entry in entries}
               for entries in noopt["traces"].values())
    print("OK    virtual side exits preserve complex fallback, later errors, exact progress and generators")
    return 0


if __name__ == "__main__":
    sys.exit(main())
