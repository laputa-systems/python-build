import json
import os
import subprocess
import sys


PROGRAM = r'''
import _opcode
import gc
import json
import sys
import weakref

WARM = 7000
RESULTS = {}
TRACES = {}
EVENTS = []

class Token:
    pass

X = Token()
Y = Token()

def pair(a, b):
    return __PAIR_FORWARD__

def reverse(a, b):
    return __PAIR_REVERSE__

def loop(n, fn, x, y):
    for _ in range(n):
        left, right = fn(x, y)
    return left, right

def reverse_loop(n, fn, x, y):
    for _ in range(n):
        left, right = fn(x, y)
    return left, right

def trace(function):
    entries = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        entries.extend(list(executor))
    return entries

before_x, before_y = sys.getrefcount(X), sys.getrefcount(Y)
left, right = loop(WARM, pair, X, Y)
RESULTS["forward"] = left is X and right is Y
del left, right
TRACES["forward"] = trace(loop)
left, right = reverse_loop(WARM, reverse, X, Y)
RESULTS["reverse"] = left is Y and right is X
del left, right
TRACES["reverse"] = trace(reverse_loop)
RESULTS["refcounts"] = [sys.getrefcount(X) - before_x, sys.getrefcount(Y) - before_y]
left, right = loop(WARM, pair, X, X)
RESULTS["aliased"] = left is X and right is X
del left, right

def escaping(n, fn):
    held = []
    for _ in range(n):
        held.append(fn(X, Y))
    return held

held = escaping(WARM, pair)
RESULTS["escaped"] = [len({id(value) for value in held}),
    all(type(value) is __PAIR_TYPE__ and value[0] is X and value[1] is Y for value in held)]
TRACES["escaped"] = trace(escaping)
del held

namespace = {}
exec("def released_pair(a, b):\n    return __PAIR_FORWARD__\n", namespace)
functions = [namespace.pop("released_pair")] * (WARM + 1)

def observe(label):
    containers = [value for value in gc.get_referrers(X)
              if type(value) is __PAIR_TYPE__ and len(value) == 2 and value[0] is X and value[1] is Y]
    EVENTS.append([label, len(containers), sys._getframe(2).f_code.co_name, str(sys.exception())])

function_ref = weakref.ref(functions[0], lambda _: observe("function"))
code_ref = weakref.ref(functions[0].__code__, lambda _: observe("code"))

def released_loop(n, source, x, y):
    for index in range(n):
        left, right = source.pop()(x, y)
        if index == WARM - 1:
            entries = trace(released_loop)
    return left is x and right is y, entries

try:
    raise ValueError("outer")
except ValueError:
    result, entries = released_loop(WARM + 1, functions, X, Y)
RESULTS["released"] = [result, len(functions), function_ref() is None, code_ref() is None, EVENTS]
TRACES["released"] = entries

observed = []
def profiler(frame, event, arg):
    if event == "return" and frame.f_code is pair.__code__:
        observed.append([arg, frame.f_locals["a"] is X, frame.f_locals["b"] is Y])

def observations():
    result = [len(observed), len({id(value[0]) for value in observed}),
              all(type(value[0]) is __PAIR_TYPE__ and value[0][0] is X and value[0][1] is Y
                  and value[1] and value[2] for value in observed)]
    observed.clear()
    return result

sys.setprofile(profiler)
loop(8, pair, X, Y)
sys.setprofile(None)
RESULTS["profile"] = observations()

def tracer(frame, event, arg):
    profiler(frame, event, arg)
    return tracer

sys.settrace(tracer)
loop(8, pair, X, Y)
sys.settrace(None)
RESULTS["trace"] = observations()

def returned(code, offset, value):
    if code is pair.__code__:
        frame = sys._getframe(1)
        observed.append([value, frame.f_locals["a"] is X, frame.f_locals["b"] is Y])

sys.monitoring.use_tool_id(4, "pair integration")
sys.monitoring.register_callback(4, sys.monitoring.events.PY_RETURN, returned)
sys.monitoring.set_events(4, sys.monitoring.events.PY_RETURN)
loop(8, pair, X, Y)
sys.monitoring.set_events(4, 0)
sys.monitoring.free_tool_id(4)
RESULTS["monitor"] = observations()

def alternative(a, b):
    return __PAIR_REVERSE__

old_code = pair.__code__
pair.__code__ = alternative.__code__
left, right = loop(20, pair, X, Y)
RESULTS["rebound"] = left is Y and right is X
pair.__code__ = old_code
del left, right

def cell_pair(a, b):
    def inner():
        return a
    return __PAIR_FORWARD__

left, right = loop(20, cell_pair, X, Y)
RESULTS["cell"] = left is X and right is Y
print(json.dumps({"results": RESULTS, "traces": TRACES}, sort_keys=True))
'''


def run(jit, kind):
    forward, reverse = {"tuple": ("(a, b)", "(b, a)"),
                        "list": ("[a, b]", "[b, a]")}[kind]
    program = PROGRAM.replace("__PAIR_FORWARD__", forward).replace("__PAIR_REVERSE__", reverse)
    program = program.replace("__PAIR_TYPE__", kind)
    completed = subprocess.run([sys.executable, "-s", "-c", program],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0"),
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, (jit, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    expected = {"forward": True, "reverse": True, "refcounts": [0, 0], "aliased": True,
        "escaped": [7000, True],
        "released": [True, 0, True, True,
            [["function", 1, "released_loop", "outer"], ["code", 1, "released_loop", "outer"]]],
        "profile": [8, 8, True], "trace": [8, 8, True], "monitor": [8, 8, True],
        "rebound": True, "cell": True}
    for kind in ("tuple", "list"):
        candidate, reference = run(True, kind), run(False, kind)
        assert reference["results"] == expected, (kind, reference["results"])
        assert candidate["results"] == expected, (kind, candidate["results"])
        for name in ("forward", "reverse", "released"):
            names = {entry[0] for entry in candidate["traces"][name]}
            assert "_CALL_PY_LEAF_PAIR" in names, (kind, name, names)
            assert not names.intersection({"_BUILD_TUPLE", "_BUILD_LIST", "_PUSH_FRAME",
                "_UNPACK_SEQUENCE_UNIQUE_TWO_TUPLE", "_UNPACK_SEQUENCE_LIST"}), (kind, name, names)
        assert "_CALL_PY_LEAF_PAIR" not in {entry[0] for entry in candidate["traces"]["escaped"]}
    print("OK    six pair traces preserve tuple/list identities, unpack order, finalizers and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
