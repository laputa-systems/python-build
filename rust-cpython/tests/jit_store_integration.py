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
import weakref

WARM = 7000
RESULTS = {}
TRACES = {}
EVENTS = []
ACTIVE = []

class Token:
    __slots__ = ("label", "__weakref__")
    def __init__(self, label):
        self.label = label

FRESH = Token("fresh")
REPLACEMENT = Token("replacement")
BINDING = Token("original")
binding_ref = weakref.ref(BINDING)

def trace(function):
    entries = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        entries.extend(list(executor))
    return entries

class Trigger:
    def __del__(self):
        global BINDING
        frame = sys._getframe(1)
        instruction = next(value for value in dis.get_instructions(frame.f_code)
                           if value.offset == frame.f_lasti)
        EVENTS.append([frame.f_code.co_name, frame.f_locals["current"].label,
            instruction.opname, instruction.argval, str(sys.exception())])
        ACTIVE.append(frame.f_locals["was_active"])
        frame.f_locals["current"] = REPLACEMENT
        BINDING = Token("updated")
        gc.collect()

def store_loop(n, source, active):
    current = None
    was_active = False
    for index in range(n):
        was_active = active()
        current = source.pop()
        label = BINDING.label
        if index == WARM - 1:
            entries = trace(store_loop)
    return current.label, label, entries

try:
    raise ValueError("outer")
except ValueError:
    current, label, entries = store_loop(WARM + 2, [FRESH, Trigger()] + [0] * WARM, sys._jit.is_active)
RESULTS["finalizer"] = [current, label, EVENTS, binding_ref() is None]
TRACES["finalizer"] = entries

def alias_loop(n, source):
    current = None
    for _ in range(n):
        current = source.pop()
    return current

before = sys.getrefcount(FRESH)
value = alias_loop(WARM, [FRESH] * WARM)
RESULTS["alias"] = value is FRESH
del value
RESULTS["alias refcount"] = sys.getrefcount(FRESH) - before
TRACES["alias"] = trace(alias_loop)

def float_loop(n, step):
    value = 0.0
    for _ in range(n):
        value = value + step
    return value

def int_loop(n, step):
    value = 0
    for _ in range(n):
        value = value + step
    return value

RESULTS["float"] = float_loop(WARM, 0.25)
RESULTS["int"] = int_loop(WARM, 1000000)
TRACES["float"] = trace(float_loop)
TRACES["int"] = trace(int_loop)

profiled = []
def profiler(frame, event, arg):
    if event == "return" and frame.f_code is alias_loop.__code__:
        profiled.append([frame.f_locals["current"] is FRESH, arg is FRESH])

sys.setprofile(profiler)
alias_loop(8, [FRESH] * 8)
sys.setprofile(None)
RESULTS["profile"] = profiled

traced = []
def tracer(frame, event, arg):
    if event == "return" and frame.f_code is alias_loop.__code__:
        traced.append([frame.f_locals["current"] is FRESH, arg is FRESH])
    return tracer

sys.settrace(tracer)
alias_loop(8, [FRESH] * 8)
sys.settrace(None)
RESULTS["trace"] = traced
print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE}, sort_keys=True))
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, (jit, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference, unoptimized = run(True), run(False), run(True, False)
    expected = {"finalizer": ["replacement", "updated",
        [["store_loop", "fresh", "STORE_FAST", "current", "outer"]], True],
        "alias": True, "alias refcount": 0, "float": 1750.0, "int": 7000000000,
        "profile": [[True, True]], "trace": [[True, True]]}
    for side in (candidate, reference, unoptimized):
        assert side["results"] == expected, side["results"]
    assert candidate["active"] == [True], candidate["active"]
    for name in ("finalizer", "alias", "int"):
        names = {entry[0] for entry in candidate["traces"][name]}
        assert "_STORE_FAST_FUSED" in names, (name, names)
    names = {entry[0] for entry in candidate["traces"]["float"]}
    assert {"_GUARD_LOCAL_FLOAT", "_POP_TOP_FLOAT"} <= names, names
    assert all("_STORE_FAST_FUSED" not in {entry[0] for entry in entries}
               for entries in unoptimized["traces"].values())
    print("OK    fused and typed stores preserve finalizer IP, locals, invalidation and numeric boxing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
