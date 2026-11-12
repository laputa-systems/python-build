import json
import os
import subprocess
import sys


PROGRAM = r'''
import _opcode
import _testinternalcapi
import json
import sys
import weakref

WARM = 7000
RESULTS = {}
TRACES = {}
ACTIVE = []
EVENTS = []

def uops(function):
    names = set()
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        names.update(entry[0] for entry in executor)
    return sorted(names)

def result_kinds(function):
    kinds = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            entries = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        kinds.extend(entry[3] & 3 for entry in entries if entry[0] == "_CALL_PY_LEAF")
    return kinds

class Holder:
    __HOLDER_LAYOUT__
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value
    def get_argument(self, other):
        return other.value

class Derived(Holder):
    __slots__ = ()

def slot_loop(n, holder):
    for _ in range(n):
        ACTIVE.append(sys._jit.is_active())
        result = holder.get()
    return result

def inherited_loop(n, holder):
    for _ in range(n):
        result = holder.get()
    return result

def argument_loop(n, holder):
    for _ in range(n):
        result = holder.get_argument(holder)
    return result

token = object()
holder = Holder(token)
def record_self(holder, token):
    before = sys.getrefcount(token)
    result = slot_loop(WARM, holder)
    RESULTS["self"] = result is token
    del result
    RESULTS["refcount delta"] = sys.getrefcount(token) - before
    TRACES["self"] = uops(slot_loop)
record_self(holder, token)
RESULTS["inherited"] = inherited_loop(WARM, Derived(token)) is token
TRACES["inherited"] = uops(inherited_loop)
RESULTS["argument"] = argument_loop(WARM, holder) is token
TRACES["argument"] = uops(argument_loop)

if __MANAGED__:
    TRACES["managed result kinds"] = result_kinds(slot_loop)
    class ReplaceDict:
        def __init__(self, owner, values):
            self.owner = owner
            self.values = values
        def __del__(self):
            self.owner.__dict__ = self.values
    def separate_loop(n, receiver, other, source):
        for i in range(n):
            proof = other.value
            transient = source.pop()
            ACTIVE.append(sys._jit.is_active())
            result = receiver.get_argument(other)
        return result
    other = Holder(token)
    RESULTS["separate"] = separate_loop(WARM, holder, other, [None] * WARM) is token
    TRACES["separate result kinds"] = result_kinds(separate_loop)
    ACTIVE.clear()
    try:
        RESULTS["separate changed dict"] = separate_loop(2, holder, other,
            [None, ReplaceDict(other, {} if __CLEAR__ else {"value": "separate dictionary"})])
    except AttributeError as error:
        frames = []
        tb = error.__traceback__
        while tb:
            frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        RESULTS["separate changed dict"] = [str(error), frames]
    TRACES["separate mutation active"] = bool(ACTIVE) and ACTIVE[-1]
    RESULTS["materialized dict"] = holder.__dict__["value"] is token
    holder.__dict__ = {"value": "replaced dictionary"}
    RESULTS["replaced dict"] = slot_loop(8, holder)
    holder.__dict__.clear()
    try:
        slot_loop(8, holder)
    except AttributeError as error:
        frames = []
        tb = error.__traceback__
        while tb:
            frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        RESULTS["cleared dict"] = [str(error), frames]
    holder = Holder(token)

class Foreign:
    __slots__ = ("pad", "value")
    get = Holder.get
    def __init__(self):
        self.pad = "wrong slot"
        self.value = "foreign"

RESULTS["foreign layout"] = inherited_loop(32, Foreign())

if __MANAGED__:
    class ForeignManaged:
        get = Holder.get
        def __init__(self):
            self.pad = "different inline offset"
            self.value = "foreign managed"
    RESULTS["foreign managed layout"] = inherited_loop(32, ForeignManaged())

class MortalHolder(Holder):
    __slots__ = ("active",)
    def __init__(self, value, active=False):
        self.value = value
        self.active = active
    def __del__(self):
        if self.active:
            EVENTS.append(["owner", sys._getframe(1).f_code.co_name, str(sys.exception())])
        self.value = None

class Source:
    def __init__(self):
        self.values = [MortalHolder(token, True)] + [MortalHolder(token)] * WARM
    def __call__(self):
        return self.values.pop()

def ephemeral_loop(n, source):
    for i in range(n):
        result = source().get()
        if i == WARM - 1:
            TRACES["ephemeral"] = uops(ephemeral_loop)
    return result

try:
    raise ValueError("outer")
except ValueError:
    RESULTS["ephemeral"] = ephemeral_loop(WARM + 1, Source()) is token
RESULTS["finalizers"] = EVENTS

holder.value = "updated"
RESULTS["updated"] = slot_loop(8, holder)

class Remover:
    def __init__(self, holder, remove=False):
        self.holder = holder
        self.remove = remove
    def __del__(self):
        if self.remove:
            del self.holder.value

def missing_loop(n, holder, source):
    for _ in range(n):
        transient = source.pop()
        ACTIVE.append(sys._jit.is_active())
        result = holder.get()
    return result

missing_loop(WARM, holder, [Remover(holder) for _ in range(WARM)])
TRACES["missing"] = uops(missing_loop)
ACTIVE.clear()
try:
    missing_loop(2, holder, [Remover(holder), Remover(holder, True)])
except AttributeError as exc:
    frames = []
    tb = exc.__traceback__
    while tb:
        frames.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    RESULTS["missing"] = [str(exc), frames]
missing_was_jitted = bool(ACTIVE) and ACTIVE[-1]
holder.value = token

profiled = []
def profiler(frame, event, arg):
    if event == "call" and frame.f_code is Holder.get.__code__:
        profiled.append(frame.f_locals["self"] is holder)
sys.setprofile(profiler)
slot_loop(8, holder)
sys.setprofile(None)
RESULTS["profiled"] = profiled

traced = []
def tracer(frame, event, arg):
    if event == "return" and frame.f_code is Holder.get.__code__:
        traced.append(arg is token and frame.f_locals["self"] is holder)
    return tracer
sys.settrace(tracer)
slot_loop(8, holder)
sys.settrace(None)
RESULTS["traced"] = traced

monitored = []
def start(code, offset):
    if code is Holder.get.__code__:
        monitored.append(sys._getframe(1).f_locals["self"] is holder)
sys.monitoring.use_tool_id(4, "slot leaf integration")
sys.monitoring.register_callback(4, sys.monitoring.events.PY_START, start)
sys.monitoring.set_events(4, sys.monitoring.events.PY_START)
slot_loop(8, holder)
sys.monitoring.set_events(4, 0)
sys.monitoring.free_tool_id(4)
RESULTS["monitored"] = monitored

slot_loop(WARM, holder)
Holder.value = property(lambda self: "descriptor")
_testinternalcapi.clear_executor_deletion_list()
RESULTS["invalidated"] = not uops(slot_loop)
RESULTS["descriptor"] = slot_loop(8, holder)
print(json.dumps({"results": RESULTS, "traces": TRACES,
                  "missing_was_jitted": missing_was_jitted}, sort_keys=True))
'''


def run(jit, managed, clear, optimize=True):
    program = PROGRAM.replace("__HOLDER_LAYOUT__", "pass" if managed else '__slots__ = ("value",)')
    program = program.replace("__MANAGED__", repr(managed))
    program = program.replace("__CLEAR__", repr(clear))
    completed = subprocess.run([sys.executable, "-s", "-c", program],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=120)
    if completed.returncode:
        raise AssertionError(f"JIT={int(jit)} exited {completed.returncode}:\n{completed.stderr}")
    return json.loads(completed.stdout)


def main():
    expected = {
        "self": True, "refcount delta": 0, "inherited": True, "argument": True,
        "foreign layout": "foreign",
        "ephemeral": True, "finalizers": [["owner", "ephemeral_loop", "outer"]],
        "updated": "updated", "missing": ["'Holder' object has no attribute 'value'",
            ["<module>", "missing_loop", "get"]],
        "profiled": [True] * 8, "traced": [True] * 8, "monitored": [True] * 8,
        "invalidated": True, "descriptor": "descriptor",
    }
    for managed, clear in ((False, False), (True, False), (True, True)):
        candidate, reference = run(True, managed, clear), run(False, managed, clear)
        noopt = run(True, managed, clear, False)
        assert candidate["results"] == reference["results"], (managed, candidate, reference)
        absolute = dict(expected)
        if managed:
            absolute.update({"materialized dict": True, "replaced dict": "replaced dictionary",
                "foreign managed layout": "foreign managed",
                "separate": True,
                "separate changed dict": ["'Holder' object has no attribute 'value'",
                    ["<module>", "separate_loop", "get_argument"]] if clear else "separate dictionary",
                "cleared dict": ["'Holder' object has no attribute 'value'",
                    ["<module>", "slot_loop", "get"]]})
            assert candidate["traces"]["managed result kinds"] == [3], candidate["traces"]
            assert candidate["traces"]["separate result kinds"] == [3], candidate["traces"]
            assert candidate["traces"]["separate mutation active"], candidate["traces"]
        assert reference["results"] == absolute, (managed, reference)
        assert noopt["results"] == absolute, (managed, noopt)
        assert all("_CALL_PY_LEAF" not in noopt["traces"][name]
                   for name in ("self", "inherited", "argument", "ephemeral", "missing")), noopt
        assert candidate["missing_was_jitted"], (managed, candidate)
        for name in ("self", "inherited", "argument", "ephemeral", "missing"):
            assert "_CALL_PY_LEAF" in candidate["traces"][name], (managed, name, candidate["traces"])
    print("OK    slot/instance getter traces preserve storage, guards, missing-value exits and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
