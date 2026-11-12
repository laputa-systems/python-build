import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import _testinternalcapi
import gc
import json
import sys
import threading
import weakref

WARM = 7000
EVENTS = []
PROBES = {}

def uops(function):
    names = set()
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        names.update(entry[0] for entry in executor)
    return sorted(names)

def identity(value):
    return value

def constant(a, b):
    return "leaf"

def returns_none():
    return None

def fifth(a, b, c, d, e, f):
    return e

class Receiver:
    def myself(self):
        return self
    def argument(self, value):
        return value

def identity_loop(n, fn, value):
    for _ in range(n):
        result = fn(value)
    return result

def constant_loop(n, fn, a, b):
    for _ in range(n):
        result = fn(a, b)
    return result

def many_loop(n, fn):
    for _ in range(n):
        result = fn(0, 1, 2, 3, 4, 5)
    return result

def self_loop(n, fn):
    for _ in range(n):
        result = fn()
    return result

def bound_loop(n, fn, value):
    for _ in range(n):
        result = fn(value)
    return result

def generated(n, fn):
    for _ in range(n):
        yield fn(1)

results = {}
token = Receiver()
before = sys.getrefcount(token)
last = identity_loop(WARM, identity, token)
results["identity"] = last is token
del last
results["operand refcount"] = sys.getrefcount(token) - before
PROBES["identity"] = uops(identity_loop)
results["constant"] = constant_loop(WARM, constant, token, token)
PROBES["constant"] = uops(constant_loop)
results["none"] = self_loop(WARM, returns_none)
PROBES["none"] = uops(self_loop)
results["six arguments"] = many_loop(WARM, fifth)
PROBES["six arguments"] = uops(many_loop)
results["bound self"] = self_loop(WARM, token.myself) is token
PROBES["bound self"] = uops(self_loop)
results["bound argument"] = bound_loop(WARM, token.argument, token) is token
PROBES["bound argument"] = uops(bound_loop)
results["generator"] = sum(generated(WARM, identity))
PROBES["generator"] = uops(generated)

namespace = {}
exec("def released_leaf(a, b):\n    return 'released'\n", namespace)
FUNCS = [namespace.pop("released_leaf")]

def observe(label):
    frame = sys._getframe(1)
    EVENTS.append([label, frame.f_code.co_name, str(sys.exception())])

function_ref = weakref.ref(FUNCS[0], lambda _: observe("function"))
code_ref = weakref.ref(FUNCS[0].__code__, lambda _: observe("code"))

class Argument:
    def __init__(self, name, clears_function=False):
        self.name = name
        self.clears_function = clears_function
    def __del__(self):
        EVENTS.append([self.name, sys._getframe(1).f_code.co_name, str(sys.exception())])
        if self.clears_function:
            FUNCS[0] = None

class Source:
    def __init__(self):
        self.values = [Argument("b", True), Argument("a")] + [0] * (2 * WARM)
    def __call__(self):
        return self.values.pop()

def released_loop(n, source):
    for index in range(n):
        result = FUNCS[0](source(), source())
        if index == WARM - 1:
            PROBES["released"] = uops(released_loop)
    return result

try:
    raise ValueError("outer")
except ValueError:
    results["released"] = released_loop(WARM + 1, Source())
results["function collected"] = function_ref() is None
results["code collected"] = code_ref() is None
results["finalizers"] = EVENTS.copy()

def alternate(value):
    return "changed"

old_code = identity.__code__
identity.__code__ = alternate.__code__
_testinternalcapi.clear_executor_deletion_list()
results["executor invalidated"] = not uops(identity_loop)
results["code rebinding"] = identity_loop(20, identity, token)
identity.__code__ = old_code

def observer(value):
    frame = sys._getframe()
    return [frame.f_code.co_name, frame.f_back.f_code.co_name, frame.f_locals["value"]]

results["observer"] = identity_loop(20, observer, 9)

profiled = []
def profiler(frame, event, arg):
    if event == "call" and frame.f_code is identity.__code__:
        profiled.append(frame.f_locals["value"])
sys.setprofile(profiler)
identity_loop(8, identity, 9)
sys.setprofile(None)
results["profiled"] = profiled

traced = []
def tracer(frame, event, arg):
    if event == "return" and frame.f_code is identity.__code__:
        traced.append([frame.f_locals["value"], arg])
    return tracer
sys.settrace(tracer)
identity_loop(8, identity, 10)
sys.settrace(None)
results["traced"] = traced

monitored = []
def start(code, offset):
    if code is identity.__code__:
        frame = sys._getframe(1)
        monitored.append(frame.f_locals["value"])
sys.monitoring.use_tool_id(4, "leaf integration")
sys.monitoring.register_callback(4, sys.monitoring.events.PY_START, start)
sys.monitoring.set_events(4, sys.monitoring.events.PY_START)
identity_loop(8, identity, 11)
sys.monitoring.set_events(4, 0)
sys.monitoring.free_tool_id(4)
results["monitored"] = monitored

threaded = []
def worker(value):
    threaded.append(identity_loop(200, identity, value))
threads = [threading.Thread(target=worker, args=(value,)) for value in (12, 13)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
results["threaded"] = sorted(threaded)
gc.collect()
print(json.dumps({"results": results, "uops": PROBES}, sort_keys=True))
'''


def run(jit):
    env = dict(os.environ, PYTHON_JIT="1" if jit else "0")
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM], env=env,
                               capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode:
        raise AssertionError(f"PYTHON_JIT={int(jit)} exited {completed.returncode}:\n{completed.stderr}")
    return json.loads(completed.stdout)


def main():
    candidate, reference = run(True), run(False)
    assert candidate["results"] == reference["results"], (candidate["results"], reference["results"])
    expected = {
        "identity": True, "operand refcount": 0, "constant": "leaf", "none": None,
        "six arguments": 4,
        "bound self": True, "bound argument": True, "generator": 7000,
        "released": "released", "function collected": True, "code collected": True,
        "finalizers": [["b", "released_loop", "outer"], ["a", "released_loop", "outer"],
                       ["function", "<lambda>", "outer"], ["code", "<lambda>", "outer"]],
        "code rebinding": "changed", "executor invalidated": True,
        "observer": ["observer", "identity_loop", 9],
        "profiled": [9] * 8, "traced": [[10, 10]] * 8,
        "monitored": [11] * 8, "threaded": [12, 13],
    }
    assert reference["results"] == expected, reference["results"]
    eligible = {"identity", "constant", "none", "six arguments", "bound self", "bound argument", "released"}
    for name in eligible:
        names = candidate["uops"][name]
        assert "_CALL_PY_LEAF" in names, (name, names)
    print(f"OK    {len(eligible)} leaf-call traces preserve results, frames and reference lifetime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
