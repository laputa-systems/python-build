import json
import os
import subprocess
import sys


PROGRAM = r'''
import _opcode
import json
import sys
import types
import weakref

WARM = 7000
RESULTS = {}
TRACES = {}
ACTIVE = {}
EVENTS = []

SOURCE = """
def loop(n, source, active):
    result = None
    was_active = False
    for i in range(n):
        was_active = active()
        transient = source.pop()
        result = VALUE.label
    return result, was_active
"""

class Token:
    __slots__ = ("label", "__weakref__")
    def __init__(self, label):
        self.label = label

class Trigger:
    def __init__(self, namespace, replace=False):
        self.namespace = namespace
        self.replace = replace
    def __del__(self):
        if self.replace:
            EVENTS.append([sys._getframe(1).f_code.co_name, str(sys.exception())])
            self.namespace["VALUE"] = Token("replacement")

class Lookup(dict):
    def __getitem__(self, key):
        if key == "VALUE":
            self.hits.append(sys._getframe(1).f_code.co_name)
            return self.token
        return super().__getitem__(key)

def trace(function):
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        entries = list(executor)
        if any(entry[0] == "_JUMP_TO_TOP" for entry in entries):
            return entries
    return []

def record(name, function, source):
    result, active = function(WARM, source, sys._jit.is_active)
    RESULTS[name] = [result, len(source)]
    ACTIVE[name] = active
    TRACES[name] = trace(function)

def builtin_loop(n):
    result = 0
    for _ in range(n):
        result += len("ab") + abs(-1) + ord("c")
    return result

def run_program():
    namespace = {"VALUE": Token("original"), "__builtins__": __builtins__}
    exec(SOURCE, namespace)
    loop = namespace["loop"]
    foreign_namespace = {"VALUE": Token("foreign"), "extra": None, "__builtins__": __builtins__}
    foreign = types.FunctionType(loop.__code__, foreign_namespace)
    record("shared", loop, [0] * WARM)
    source = [0, 0]
    result, _ = foreign(2, source, sys._jit.is_active)
    RESULTS["foreign"] = [result, len(source)]
    try:
        foreign(2, [0], sys._jit.is_active)
    except IndexError as exc:
        tb = exc.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        RESULTS["entry error"] = [str(exc), tb.tb_frame.f_code.co_name, tb.tb_frame.f_locals["i"]]

    namespace = {"VALUE": Token("warm"), "__builtins__": __builtins__}
    exec(SOURCE, namespace)
    loop = namespace["loop"]
    record("mutation", loop, [Trigger(namespace) for _ in range(WARM)])
    old = weakref.ref(namespace["VALUE"])
    try:
        raise ValueError("outer")
    except ValueError:
        source = [Trigger(namespace), Trigger(namespace, True)]
        result, active = loop(2, source, sys._jit.is_active)
    RESULTS["replacement"] = [result, len(source), old() is None]
    ACTIVE["mutation before callback"] = active
    RESULTS["finalizer"] = EVENTS

    profiled = []
    def profiler(frame, event, arg):
        if event == "call" and frame.f_code is loop.__code__:
            profiled.append(frame.f_globals is namespace)
    sys.setprofile(profiler)
    RESULTS["profile result"] = loop(8, [0] * 8, sys._jit.is_active)[0]
    sys.setprofile(None)
    RESULTS["profiled"] = profiled

    exact = {"VALUE": Token("exact"), "__builtins__": __builtins__}
    exec(SOURCE, exact)
    mapping = Lookup({"__builtins__": __builtins__})
    mapping.hits = []
    mapping.token = Token("mapping")
    mapped = types.FunctionType(exact["loop"].__code__, mapping)
    record("mapping entry", exact["loop"], [0] * WARM)
    source = [0, 0]
    result, _ = mapped(2, source, sys._jit.is_active)
    RESULTS["mapping"] = [result, len(source), mapping.hits]
    RESULTS["builtins"] = builtin_loop(WARM)
    TRACES["builtins"] = trace(builtin_loop)

run_program()
print(json.dumps({"results": RESULTS, "active": ACTIVE, "traces": TRACES}, sort_keys=True))
'''


def run(jit):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0"),
        capture_output=True, text=True, timeout=20)
    if completed.returncode:
        raise AssertionError(f"JIT={int(jit)} exited {completed.returncode}:\n{completed.stderr}")
    return json.loads(completed.stdout)


def main():
    candidate, reference = run(True), run(False)
    assert candidate["results"] == reference["results"], (candidate, reference)
    assert reference["results"] == {
        "shared": ["original", 0], "foreign": ["foreign", 0],
        "entry error": ["pop from empty list", "loop", 1],
        "mutation": ["warm", 0], "replacement": ["replacement", 0, True],
        "finalizer": [["loop", "outer"]], "profiled": [True],
        "profile result": "replacement",
        "builtins": 102 * 7000,
        "mapping entry": ["exact", 0], "mapping": ["mapping", 0, ["loop", "loop"]],
    }, reference
    for name, entries in candidate["traces"].items():
        names = [entry[0] for entry in entries]
        if name != "builtins":
            assert candidate["active"][name], candidate
        assert names[1] == "_GUARD_LOOP_GLOBALS_VERSION", (name, entries)
        warm = names.index("_MAKE_WARM")
        assert warm > 1
        jump = next(entry for entry in entries if entry[0] == "_JUMP_TO_TOP")
        assert jump[2] == warm, (name, jump, warm)
        if name == "builtins":
            assert names.count("_GUARD_BUILTINS_IS_CANONICAL") == 1, entries
            assert "_LOAD_GLOBAL_BUILTINS" not in names, entries
    assert candidate["active"]["mutation before callback"], candidate
    print("OK    hoisted loop guards preserve entry state, callback mutation and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
