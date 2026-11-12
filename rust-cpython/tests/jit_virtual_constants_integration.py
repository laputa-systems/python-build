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

WARM = 7000
RESULTS = {}
TRACES = {}
ACTIVE = []
EVENTS = []

def trace(function):
    result = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            result.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            pass
    return result

def encode(value):
    return value.hex() if type(value) is float else "unused"

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

def capture(error, fn, loop, label):
    frames = []
    tb = error.__traceback__
    while tb:
        frame = tb.tb_frame
        instruction = next(item for item in dis.get_instructions(frame.f_code) if item.offset == tb.tb_lasti)
        if frame.f_code is loop.__code__:
            ACTIVE.append(frame.f_locals["was_active"])
            TRACES[label] = frame.f_locals["entries"]
        values = {key: encode(value) for key, value in frame.f_locals.items()} if frame.f_code is fn.__code__ else {}
        frames.append([frame.f_code.co_name, instruction.opname,
            instruction.positions.col_offset, instruction.positions.end_col_offset, values])
        tb = tb.tb_next
    return [type(error).__name__, str(error), str(error.__context__), frames]

class Right:
    def __radd__(self, other):
        frame = sys._getframe(1)
        EVENTS.append([frame.f_code.co_name, {key: encode(value) for key, value in frame.f_locals.items()}, other.hex()])
        return 12.0

unused = object()
fn, loop = build("a, unused", "    first = a + 2.5\n    return first * 0.25 / (a - 1.25)", "a, unused")
source = Source([(2.5, unused)] * WARM)
value, entries, active = loop(WARM, fn, source, sys._jit.is_active)
RESULTS["constants"] = [value.hex(), source.calls, len(source.values)]
TRACES["constants"] = entries
ACTIVE.append(active)
source = Source([(1.25, unused)] + [(2.5, unused)] * (WARM + 15))
try:
    raise ValueError("outer")
except ValueError:
    try:
        loop(WARM + 16, fn, source, sys._jit.is_active)
    except ZeroDivisionError as error:
        RESULTS["zero"] = capture(error, fn, loop, "zero")
RESULTS["zero progress"] = [source.calls, len(source.values)]

fn, loop = build("a, b, unused", "    first = a + 2.5\n    return first + b", "a, b, unused")
source = Source([(2.5, 2.0, unused)] * WARM)
value, entries, _ = loop(WARM, fn, source, sys._jit.is_active)
TRACES["fallback"] = entries
source = Source([(1.25, Right(), unused)] + [(2.5, 2.0, unused)] * (WARM + 15))
value, _, active = loop(WARM + 16, fn, source, sys._jit.is_active)
RESULTS["fallback"] = [value.hex(), source.calls, len(source.values)]
ACTIVE.append(active)

class Arm:
    def __del__(self):
        _testcapi.set_nomemory(0, 1)

def allocating(n, fn, source, active, unused):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(2.5, unused)
        if index == WARM - 1:
            entries = trace(allocating)
    return held

fn, _ = build("a, unused", "    first = a + 2.5\n    return first * 0.25", "a, unused")
try:
    allocating(WARM + 18, fn, [None, Arm()] + [None] * (WARM + 16), sys._jit.is_active, unused)
except MemoryError as error:
    _testcapi.remove_mem_hooks()
    RESULTS["allocation"] = capture(error, fn, allocating, "allocation")
else:
    raise AssertionError("allocation did not fail")

nested_fn, nested_loop = build("a, unused", "    first = a + 2.5\n    return first * 0.25", "a, unused")
nested_loop(WARM, nested_fn, Source([(2.5, unused)] * WARM), sys._jit.is_active)

def replacement(a):
    return a

class Token:
    def __init__(self, label=None):
        self.label = label
    def __del__(self):
        if self.label:
            frame = sys._getframe(1)
            ACTIVE.append(frame.f_locals["was_active"])
            EVENTS.append(["finalizer", self.label, frame.f_code.co_name, sorted(frame.f_locals)])
            if self.label == "second":
                frame.f_locals["fn"].__code__ = replacement.__code__
                value = nested_loop(WARM, nested_fn, Source([(2.5, unused)] * WARM), sys._jit.is_active)[0]
                EVENTS.append(["nested", value.hex()])
            gc.collect()

def temporary(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        result = fn(2.5, source.pop(), source.pop())
        if index == WARM - 1:
            entries = trace(temporary)
    return result, entries

fn, _ = build("a, unused, other", "    first = a + 2.5\n    return first * 0.25", "a, unused, other")
source = Source([Token("second"), Token("first")] + [Token() for _ in range(2 * (WARM + 15))])
value, entries = temporary(WARM + 16, fn, source, sys._jit.is_active)
RESULTS["finalizer"] = [value.hex(), source.calls, len(source.values)]
TRACES["finalizer"] = entries
RESULTS["events"] = EVENTS
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
    results = reference["results"]
    assert results["constants"] == [float(1).hex(), 7000, 0]
    assert results["zero"][:3] == ["ZeroDivisionError", "division by zero", "outer"]
    assert results["zero"][-1][-1][-1] == {"a": float(1.25).hex(), "unused": "unused", "first": float(3.75).hex()}
    assert results["zero progress"] == [7016, 0]
    assert results["fallback"] == [float(12).hex(), 7016, 0]
    assert results["allocation"][:3] == ["MemoryError", "", "None"]
    assert results["allocation"][-1][-1][-1] == {"a": float(2.5).hex(), "unused": "unused", "first": float(5).hex()}
    assert results["finalizer"] == [float(1.25).hex(), 14032, 0]
    assert results["events"][0] == ["arithmetic", {"a": float(1.25).hex(), "b": "unused", "unused": "unused", "first": float(3.75).hex()}, float(3.75).hex()]
    assert results["events"][-3][:2] == ["finalizer", "second"]
    assert results["events"][-2] == ["nested", float(1.25).hex()]
    assert results["events"][-1][:2] == ["finalizer", "first"]
    assert candidate["active"] == [True] * 6, candidate["active"]
    for label, entries in candidate["traces"].items():
        names = [entry[0] for entry in entries]
        begin = names.index("_BEGIN_VIRTUAL_CALL")
        end = names.index("_RETURN_VIRTUAL_CALL")
        body = names[begin + 1:end]
        assert any(name in ("_LOAD_CONST_INLINE", "_LOAD_CONST_INLINE_BORROW") for name in body), (label, body)
        assert any(name in ("_GUARD_TOS_FLOAT", "_GUARD_NOS_FLOAT") for name in body), (label, body)
        assert "_MATERIALIZE_VIRTUAL_FRAME" in names, (label, names)
    assert all("_BEGIN_VIRTUAL_CALL" not in {entry[0] for entry in entries}
               for entries in noopt["traces"].values())
    print("OK    virtual constants preserve use-site guards, unused arguments, later errors, allocation faults and finalizers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
