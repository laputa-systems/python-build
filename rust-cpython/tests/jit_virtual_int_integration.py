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
RESULTS = {}
TRACES = {}
ACTIVE = []
EVENTS = []

def encode(value):
    if type(value) in (int, bool):
        return [type(value).__name__, str(value)]
    if type(value) is float:
        return ["float", value.hex()]
    return ["other", type(value).__name__]

def trace(function):
    result = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            result.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            pass
    return result

def build(parameters, body, arguments):
    namespace = {"trace": trace, "WARM": WARM}
    exec(f"def arithmetic({parameters}):\n{body}\n", namespace)
    exec(f"""def loop(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        {arguments} = source.pop()
        was_active = active()
        result = fn({arguments})
        if index == WARM - 1:
            entries = trace(loop)
    return result, entries, was_active
""", namespace)
    return namespace["arithmetic"], namespace["loop"]

class Source:
    def __init__(self, values):
        self.values = values
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

class Multiply:
    def __rmul__(self, other):
        frame = sys._getframe(1)
        EVENTS.append([frame.f_code.co_name, {key: encode(value) for key, value in frame.f_locals.items()}, encode(other)])
        return other * 4

class Subclass(int):
    pass

for label, body in (
        ("left", "    return (a + b) * b"),
        ("right", "    return a * (a - b)"),
        ("locals", "    first = a + b\n    alias = first\n    first = first * b\n    return first + alias")):
    fn, loop = build("a, b", body, "a, b")
    source = Source([(10000, 3)] * WARM)
    value, entries, active = loop(WARM, fn, source, sys._jit.is_active)
    TRACES[label] = entries
    ACTIVE.append(active)
    RESULTS[label] = [encode(value), source.calls, len(source.values)]
    samples = []
    for a, b in ((1, 2), (-4, 5), (100000, 100001), (2**29, 2),
                 (2**30, 3), (True, 2), (Subclass(17), 3), (2**70, 2)):
        source = Source([(a, b)] + [(10000, 3)] * (WARM + 15))
        value, _, active = loop(WARM + 16, fn, source, sys._jit.is_active)
        ACTIVE.append(active)
        samples.append([encode(value), source.calls, len(source.values)])
    RESULTS[label + " boundaries"] = samples

fn, loop = build("a, b, c, d", "    first = a + b\n    alias = first\n    first = c * d\n    return alias", "a, b, c, d")
source = Source([(999999, 1001, 1.25, 2.5)] * WARM)
value, entries, active = loop(WARM, fn, source, sys._jit.is_active)
RESULTS["mixed locals"] = [encode(value), source.calls, len(source.values)]
TRACES["mixed locals"] = entries
ACTIVE.append(active)

fn, loop = build("a, b, c", "    first = a + b\n    return first * c", "a, b, c")
source = Source([(999999, 1001, 3)] * WARM)
loop(WARM, fn, source, sys._jit.is_active)
source = Source([(999999, 1001, Multiply())] + [(999999, 1001, 3)] * (WARM + 15))
value, entries, active = loop(WARM + 16, fn, source, sys._jit.is_active)
RESULTS["callback"] = [encode(value), source.calls, len(source.values)]
TRACES["callback"] = entries
ACTIVE.append(active)

fn, loop = build("a, b, c", "    first = a + b\n    second = first * b\n    return second * c", "a, b, c")
source = Source([(10000, 3, 3)] * WARM)
loop(WARM, fn, source, sys._jit.is_active)
source = Source([(2**29, 2, Multiply())] + [(10000, 3, 3)] * (WARM + 15))
value, entries, active = loop(WARM + 16, fn, source, sys._jit.is_active)
RESULTS["overflow callback"] = [encode(value), source.calls, len(source.values)]
TRACES["overflow callback"] = entries
ACTIVE.append(active)

class Holder:
    __LAYOUT__
    def __init__(self):
        self.value = int("999999")
    def calculate(self, a, b):
        first = self.value + a
        return first * b

def method_loop(n, owner, active):
    was_active = False
    for index in range(n):
        was_active = active()
        result = owner.calculate(1001, 3)
    return result, was_active

owner = Holder()
before = sys.getrefcount(owner)
value_before = sys.getrefcount(owner.value)
value, active = method_loop(WARM, owner, sys._jit.is_active)
RESULTS["method"] = [encode(value), sys.getrefcount(owner) - before, sys.getrefcount(owner.value) - value_before]
TRACES["method"] = trace(method_loop)
ACTIVE.append(active)

def generator(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        a, b, c = source.pop()
        was_active = active()
        value = fn(a, b, c)
        if index == WARM - 1:
            entries = trace(generator)
        if index == n - 1:
            ACTIVE.append(was_active)
            TRACES["generator"] = entries
        yield value

fn, _ = build("a, b, c", "    first = a + b\n    return first * c", "a, b, c")
source = Source([(999999, 1001, 2000)] + [(999999, 1001, 3)] * (WARM + 15))
values = list(generator(WARM + 16, fn, source, sys._jit.is_active))
RESULTS["generator"] = [len(values), encode(values[-1]), source.calls, len(source.values)]
class Arm:
    def __init__(self, failures):
        self.failures = failures
    def __del__(self):
        frame = sys._getframe(1)
        ACTIVE.append(frame.f_locals["was_active"])
        TRACES["allocation"] = frame.f_locals["entries"]
        _testcapi.set_nomemory(0, self.failures)

def make_allocating():
    namespace = {"trace": trace, "WARM": WARM}
    exec("""def allocating(n, fn, source, active):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(999999, 1001, 3)
        if index == WARM - 1:
            entries = trace(allocating)
    return held, entries, was_active
""", namespace)
    return namespace["allocating"]

ALLOCATION = {}
for failures in (1, 16):
    allocating = make_allocating()
    fn, _ = build("a, b, c", "    first = a + b\n    return first * c", "a, b, c")
    try:
        held, entries, active = allocating(WARM + 18, fn,
            [None, Arm(failures)] + [None] * (WARM + 16), sys._jit.is_active)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        state = None
        active = False
        tb = error.__traceback__
        while tb:
            frame = tb.tb_frame
            if frame.f_code is allocating.__code__:
                active = frame.f_locals["was_active"]
                TRACES["allocation"] = frame.f_locals["entries"]
            if frame.f_code is fn.__code__:
                instruction = next(item for item in dis.get_instructions(frame.f_code)
                                   if item.offset == tb.tb_lasti)
                state = [instruction.opname, instruction.positions.col_offset,
                    instruction.positions.end_col_offset,
                    {key: encode(value) for key, value in frame.f_locals.items()}]
            tb = tb.tb_next
        ALLOCATION[failures] = ["MemoryError", state]
    else:
        ALLOCATION[failures] = ["success", str(held[-1])]
        TRACES["allocation"] = entries

RESULTS["events"] = EVENTS
print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE, "allocation": ALLOCATION}, sort_keys=True))
'''


def run(jit, managed=False, optimize=True):
    program = PROGRAM.replace("__LAYOUT__", "pass" if managed else "__slots__ = ('value',)")
    completed = subprocess.run([sys.executable, "-s", "-c", program],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, (jit, managed, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    for managed in (False, True):
        candidate, reference, noopt = run(True, managed), run(False, managed), run(True, managed, False)
        assert candidate["results"] == reference["results"] == noopt["results"], (
            managed, candidate["results"], reference["results"], noopt["results"])
        results = reference["results"]
        assert results["left"] == [["int", "30009"], 7000, 0]
        assert results["right"] == [["int", str(10000 * (10000 - 3))], 7000, 0]
        assert results["locals"] == [["int", "40012"], 7000, 0]
        assert results["mixed locals"] == [["int", "1001000"], 7000, 0]
        assert results["callback"] == [["int", "4004000"], 7016, 0]
        assert results["method"] == [["int", "3003000"], 0, 0]
        assert results["generator"] == [7016, ["int", str(1001000 * 2000)], 7016, 0]
        assert results["overflow callback"] == [["int", str((2**29 + 2) * 2 * 4)], 7016, 0]
        assert results["events"][0] == ["arithmetic", {"a": ["int", "999999"], "b": ["int", "1001"], "c": ["other", "Multiply"], "first": ["int", "1001000"]}, ["int", "1001000"]]
        assert results["events"][1] == ["arithmetic", {"a": ["int", str(2**29)], "b": ["int", "2"], "c": ["other", "Multiply"], "first": ["int", str(2**29 + 2)], "second": ["int", str((2**29 + 2) * 2)]}, ["int", str((2**29 + 2) * 2)]]
        for name, values in results.items():
            if name.endswith(" boundaries"):
                assert all(item[1:] == [7016, 0] for item in values), (name, values)
        expected_state = ["BINARY_OP", 11, 20, {"a": ["int", "999999"],
            "b": ["int", "1001"], "c": ["int", "3"], "first": ["int", "1001000"]}]
        for result in (candidate, reference, noopt):
            assert result["allocation"]["1"] == ["success", "3003000"], result["allocation"]
            assert result["allocation"]["16"][0] == "MemoryError", result["allocation"]
            state = result["allocation"]["16"][1]
            if state is not None:
                assert state == expected_state, state
        assert candidate["active"] == [True] * 34, candidate["active"]
        for label, entries in candidate["traces"].items():
            names = [entry[0] for entry in entries]
            begin = names.index("_BEGIN_VIRTUAL_CALL")
            end = names.index("_RETURN_VIRTUAL_CALL")
            body = names[begin + 1:end]
            assert "_MATERIALIZE_VIRTUAL_FRAME" in names, (managed, label, names)
            assert any("_INT" in name for name in body), (managed, label, body)
            if label == "left":
                assert "_BINARY_OP_MULTIPLY_INT_INPLACE" in body, body
            if label == "right":
                assert "_BINARY_OP_MULTIPLY_INT_INPLACE_RIGHT" in body, body
            if label == "mixed locals":
                assert "_STORE_FAST_FUSED_INT" in body and "_BINARY_OP_MULTIPLY_FLOAT" in body, body
        assert all("_BEGIN_VIRTUAL_CALL" not in {entry[0] for entry in entries}
                   for entries in noopt["traces"].values())
    print("OK    virtual integers preserve compact exits, reuse ownership, mixed locals, receivers and generators")
    return 0


if __name__ == "__main__":
    sys.exit(main())
