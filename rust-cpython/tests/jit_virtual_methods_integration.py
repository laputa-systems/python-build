import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
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

def make():
    class Holder:
        __LAYOUT__
        def __init__(self, value):
            self.value = value
        def calculate(self, x, y, z):
            first = self.value + x
            alias = first
            second = first * y
            return second / z
    namespace = {"trace": trace, "WARM": WARM}
    exec("""def loop(n, owner, source, active):
    entries = []
    was_active = False
    transient = None
    for index in range(n):
        was_active = active()
        method = owner.calculate
        x, y, z, transient = source.pop()
        result = method(x, y, z)
        if index == WARM - 1:
            entries = trace(loop)
    return result, entries, was_active
""", namespace)
    return Holder, namespace["loop"]

class Mutate:
    def __init__(self, callback):
        self.callback = callback
    def __del__(self):
        self.callback()

class Source:
    def __init__(self, n, fault=(2.0, 3.0, 4.0), mutation=None):
        self.values = [(*fault, None)] + [(2.0, 3.0, 4.0, None)] * (n - 1)
        if mutation:
            self.values[1] = (2.0, 3.0, 4.0, Mutate(mutation))
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

def locals_of(frame):
    return {name: ("self" if name == "self" else value.hex())
            for name, value in frame.f_locals.items()}

def capture(error, loop, method, label):
    frames = []
    tb = error.__traceback__
    while tb:
        frame = tb.tb_frame
        instruction = next(item for item in dis.get_instructions(frame.f_code)
                           if item.offset == tb.tb_lasti)
        if frame.f_code is loop.__code__:
            ACTIVE.append(frame.f_locals["was_active"])
            TRACES[label] = frame.f_locals["entries"]
        values = locals_of(frame) if frame.f_code is method.__code__ else {}
        frames.append([frame.f_code.co_name, instruction.opname,
                       instruction.positions.col_offset, instruction.positions.end_col_offset, values])
        tb = tb.tb_next
    return [type(error).__name__, str(error), str(error.__context__), frames]

class Add:
    def __add__(self, other):
        frame = sys._getframe(1)
        EVENTS.append([frame.f_code.co_name, sorted(frame.f_locals), other.hex()])
        return 13.0

class AddFloat(float):
    __add__ = Add.__add__

for label in ("success", "zero", "nonfloat", "float subclass", "missing", "descriptor", "class",
              *(("replace dict", "clear dict") if __MANAGED__ else ())):
    Holder, loop = make()
    owner = Holder(1.25)
    source = Source(WARM)
    result, entries, active = loop(WARM, owner, source, sys._jit.is_active)
    TRACES[label] = entries
    if label == "success":
        before = sys.getrefcount(owner)
        source = Source(16)
        result, _, active = loop(16, owner, source, sys._jit.is_active)
        ACTIVE.append(active)
        RESULTS[label] = [result.hex(), source.calls, len(source.values), sys.getrefcount(owner) - before]
        continue
    mutation = None
    fault = (2.0, 3.0, 0.0) if label == "zero" else (2.0, 3.0, 4.0)
    if label == "nonfloat":
        mutation = lambda: setattr(owner, "value", Add())
    elif label == "float subclass":
        mutation = lambda: setattr(owner, "value", AddFloat(1.25))
    elif label == "missing":
        mutation = lambda: delattr(owner, "value")
    elif label == "descriptor":
        mutation = lambda: setattr(Holder, "value", property(lambda self: 9.0))
    elif label == "class":
        class Other(Holder):
            __slots__ = ()
        mutation = lambda: setattr(owner, "__class__", Other)
    elif label == "replace dict":
        mutation = lambda: setattr(owner, "__dict__", {"value": 9.0})
    elif label == "clear dict":
        mutation = lambda: setattr(owner, "__dict__", {})
    source = Source(WARM + 16, fault, mutation)
    try:
        raise ValueError("outer")
    except ValueError:
        try:
            result, _, active = loop(WARM + 16, owner, source, sys._jit.is_active)
        except (ZeroDivisionError, AttributeError) as error:
            RESULTS[label] = capture(error, loop, Holder.calculate, label)
        else:
            ACTIVE.append(active)
            RESULTS[label] = [result.hex(), str(sys.exception())]
    RESULTS[label + " progress"] = [source.calls, len(source.values)]

class Pair:
    __slots__ = ("left", "right")
    def __init__(self):
        self.left = 1.25
        self.right = 2.5
    def calculate(self):
        return self.left + self.right

def zero_args(n, owner, active):
    was_active = False
    for index in range(n):
        was_active = active()
        result = owner.calculate()
    return result, was_active

result, active = zero_args(WARM, Pair(), sys._jit.is_active)
ACTIVE.append(active)
RESULTS["zero args"] = result.hex()
TRACES["zero args"] = trace(zero_args)

Holder, nested = make()
nested_owner = Holder(1.25)
nested(WARM, nested_owner, Source(WARM), sys._jit.is_active)

def replacement(self, x, y, z):
    return self.value

class Ephemeral:
    __slots__ = ("value", "armed")
    def __init__(self, armed=False):
        self.value = 1.25
        self.armed = armed
    def calculate(self, x, y, z):
        first = self.value + x
        return first * y / z
    def __del__(self):
        if self.armed:
            frame = sys._getframe(1)
            EVENTS.append(["finalizer", frame.f_code.co_name, sorted(frame.f_locals)])
            ACTIVE.append(frame.f_locals["was_active"])
            Ephemeral.calculate.__code__ = replacement.__code__
            EVENTS.append(["nested", nested(WARM, nested_owner, Source(WARM), sys._jit.is_active)[0].hex()])
            gc.collect()

class Owners:
    def __init__(self):
        self.values = [Ephemeral(True)] + [Ephemeral() for _ in range(WARM + 15)]
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

def temporary(n, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        result = source.pop().calculate(2.0, 3.0, 4.0)
        if index == WARM - 1:
            entries = trace(temporary)
    return result, entries

owners = Owners()
result, entries = temporary(WARM + 16, owners, sys._jit.is_active)
TRACES["finalizer"] = entries
RESULTS["finalizer"] = [result.hex(), owners.calls, len(owners.values)]
RESULTS["events"] = EVENTS
print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE}, sort_keys=True))
'''


def run(jit, managed, optimize=True):
    program = PROGRAM.replace("__LAYOUT__", "pass" if managed else "__slots__ = ('value',)")
    program = program.replace("__MANAGED__", repr(managed))
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
        assert results["success"] == [2.4375.hex(), 16, 0, 0]
        assert results["zero"][:3] == ["ZeroDivisionError", "division by zero", "outer"]
        assert results["zero"][-1][-1][-1] == {
            "self": "self", "x": float(2).hex(), "y": float(3).hex(), "z": float(0).hex(),
            "first": 3.25.hex(), "alias": 3.25.hex(), "second": 9.75.hex()}
        assert results["nonfloat"] == [9.75.hex(), "outer"]
        assert results["float subclass"] == [9.75.hex(), "outer"]
        assert results["zero args"] == 3.75.hex()
        assert results["missing"][0] == "AttributeError"
        assert results["descriptor"] == [8.25.hex(), "outer"]
        assert results["class"] == [2.4375.hex(), "outer"]
        if managed:
            assert results["replace dict"] == [8.25.hex(), "outer"]
            assert results["clear dict"][0] == "AttributeError"
        assert results["finalizer"] == [2.4375.hex(), 7016, 0]
        assert results["events"][0] == ["calculate", ["self", "x", "y", "z"], float(2).hex()]
        assert results["events"][-1] == ["nested", 2.4375.hex()]
        for key, value in results.items():
            if key.endswith(" progress"):
                assert value == [7016, 0], (key, value)
        assert candidate["active"] == [True] * (11 if managed else 9), candidate["active"]
        for label, entries in candidate["traces"].items():
            names = [entry[0] for entry in entries]
            assert "_BEGIN_VIRTUAL_CALL" in names, (managed, label, names)
            assert "_RETURN_VIRTUAL_CALL" in names, (managed, label, names)
            assert "_MATERIALIZE_VIRTUAL_FRAME" in names, (managed, label, names)
            body = names[names.index("_BEGIN_VIRTUAL_CALL") + 1:
                         names.index("_RETURN_VIRTUAL_CALL")]
            if managed and label not in ("zero args", "finalizer"):
                assert "_CHECK_MANAGED_OBJECT_HAS_VALUES" in body, (label, body)
                assert "_LOAD_ATTR_INSTANCE_VALUE" in body, (label, body)
            else:
                assert "_LOAD_ATTR_SLOT" in body, (label, body)
        assert all("_BEGIN_VIRTUAL_CALL" not in {entry[0] for entry in entries}
                   for entries in noopt["traces"].values())
    print("OK    bound float methods preserve receivers, mutations, exact progress, error locals and finalizers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
