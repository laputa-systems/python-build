import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import _testinternalcapi
import json
import sys
import types
import weakref

WARM = 7000
VALUE = "wrong caller globals"
RESULTS = {}
ACTIVE = {}
TRACES = {}

def uops(function):
    names = set()
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        names.update(entry[0] for entry in executor)
    return sorted(names)

class Token:
    def __init__(self, label):
        self.label = label

def global_loop(n, fn):
    active = False
    for _ in range(n):
        result = fn()
        active = sys._jit.is_active()
    return result, active

def foreign_loop(n, fn):
    active = False
    for _ in range(n):
        result = fn()
        active = sys._jit.is_active()
    return result, active

def reentrant_loop(n, fn):
    active = False
    for _ in range(n):
        result = fn()
        active = sys._jit.is_active()
    return result, active

class ReentrantToken(Token):
    def __init__(self, label, read):
        self.label = label
        self.read = read
    def __del__(self):
        RESULTS["reentrant callback"] = reentrant_loop(8, self.read)[0].label

def record(name, loop, fn):
    result, active = loop(WARM, fn)
    RESULTS[name] = result.label
    ACTIVE[name] = active
    TRACES[name] = uops(loop)

def error(loop, fn):
    try:
        loop(8, fn)
    except NameError as exc:
        frames = []
        tb = exc.__traceback__
        while tb:
            frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        return [str(exc), frames]
    raise AssertionError("missing global did not raise")

class Lookup(dict):
    enabled = False
    def __getitem__(self, key):
        if key == "VALUE":
            if self.enabled:
                self.hits.append(sys._getframe(1).f_code.co_name)
            return "mapping"
        return super().__getitem__(key)

def run_program():
    namespace = {"VALUE": Token("first"), "__builtins__": {}}
    exec("def read():\n    return VALUE\n", namespace)
    read = namespace["read"]
    foreign_namespace = {"VALUE": Token("foreign"), "padding": None, "__builtins__": {}}
    exec("def read():\n    return VALUE\n", foreign_namespace)
    foreign = foreign_namespace["read"]
    record("global", global_loop, read)
    record("foreign", foreign_loop, foreign)
    old = weakref.ref(namespace["VALUE"])
    namespace["VALUE"] = Token("replacement")
    _testinternalcapi.clear_executor_deletion_list()
    RESULTS["old collected"] = old() is None
    RESULTS["replacement"] = global_loop(32, read)[0].label
    namespace.update(VALUE=Token("update"), added=1)
    RESULTS["update"] = global_loop(32, read)[0].label
    del namespace["VALUE"]
    RESULTS["delete"] = error(global_loop, read)
    namespace["VALUE"] = Token("restored")
    RESULTS["restore"] = global_loop(32, read)[0].label
    namespace.clear()
    RESULTS["clear"] = error(global_loop, read)

    shared = types.FunctionType(read.__code__, foreign_namespace)
    RESULTS["shared code"] = global_loop(32, shared)[0].label

    reentrant_namespace = {"__builtins__": {}}
    exec("def read():\n    return VALUE\n", reentrant_namespace)
    reentrant = reentrant_namespace["read"]
    reentrant_namespace["VALUE"] = ReentrantToken("old", reentrant)
    record("reentrant", reentrant_loop, reentrant)
    old_reentrant = weakref.ref(reentrant_namespace["VALUE"])
    reentrant_namespace["VALUE"] = Token("replacement")
    RESULTS["reentrant collected"] = old_reentrant() is None

    builtins_namespace = {"VALUE": "builtin"}
    fallback_namespace = {"VALUE": "global", "__builtins__": builtins_namespace}
    exec("def read():\n    return VALUE\n", fallback_namespace)
    fallback = fallback_namespace["read"]
    RESULTS["before fallback"] = global_loop(32, fallback)[0]
    del fallback_namespace["VALUE"]
    RESULTS["builtin fallback"] = global_loop(32, fallback)[0]
    builtins_namespace["VALUE"] = "changed builtin"
    RESULTS["builtin change"] = global_loop(32, fallback)[0]

    mapping = Lookup({"__builtins__": {}})
    mapping.hits = []
    exec("def read():\n    return VALUE\n", mapping)
    callback = dict.__getitem__(mapping, "read")
    global_loop(WARM, callback)
    mapping.enabled = True
    RESULTS["mapping"] = global_loop(8, callback)[0]
    RESULTS["mapping frames"] = mapping.hits

    lazy_namespace = {}
    exec("lazy import math as VALUE\ndef read():\n    return VALUE\n", lazy_namespace)
    lazy = lazy_namespace["read"]
    RESULTS["lazy"] = global_loop(WARM, lazy)[0].__name__
    unresolved_namespace = {}
    exec("lazy import no_such_jit_leaf_module_for_lazy_test as VALUE\n"
         "def read():\n    return VALUE\n", unresolved_namespace)
    try:
        global_loop(8, unresolved_namespace["read"])
    except ModuleNotFoundError as exc:
        frames = []
        tb = exc.__traceback__
        while tb:
            frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        RESULTS["lazy failure"] = [exc.name, frames]

    profiled = []
    def profiler(frame, event, arg):
        if event == "call" and frame.f_code is foreign.__code__:
            profiled.append(frame.f_globals is foreign_namespace)
    sys.setprofile(profiler)
    foreign_loop(8, foreign)
    sys.setprofile(None)
    RESULTS["profiled"] = profiled
    traced = []
    def tracer(frame, event, arg):
        if event == "return" and frame.f_code is foreign.__code__:
            traced.append(frame.f_globals is foreign_namespace and arg.label == "foreign")
        return tracer
    sys.settrace(tracer)
    foreign_loop(8, foreign)
    sys.settrace(None)
    RESULTS["traced"] = traced

run_program()
print(json.dumps({"results": RESULTS, "active": ACTIVE, "traces": TRACES}, sort_keys=True))
'''


def run(jit):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0"),
        capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode:
        raise AssertionError(f"JIT={int(jit)} exited {completed.returncode}:\n{completed.stderr}")
    return json.loads(completed.stdout)


def main():
    candidate, reference = run(True), run(False)
    assert candidate["results"] == reference["results"], (candidate, reference)
    expected = {
        "global": "first", "foreign": "foreign", "old collected": True,
        "shared code": "foreign",
        "reentrant": "old", "reentrant callback": "replacement", "reentrant collected": True,
        "replacement": "replacement", "update": "update", "restore": "restored",
        "delete": ["name 'VALUE' is not defined", ["error", "global_loop", "read"]],
        "clear": ["name 'VALUE' is not defined", ["error", "global_loop", "read"]],
        "before fallback": "global", "builtin fallback": "builtin",
        "builtin change": "changed builtin", "mapping": "mapping",
        "mapping frames": ["read"] * 8, "lazy": "math",
        "lazy failure": ["no_such_jit_leaf_module_for_lazy_test",
                         ["run_program", "global_loop", "read"]],
        "profiled": [True] * 8, "traced": [True] * 8,
    }
    assert reference["results"] == expected, reference
    for name in ("global", "foreign", "reentrant"):
        assert candidate["active"][name], candidate
        assert {"_CALL_PY_LEAF", "_CHECK_LEAF_GLOBALS_VERSION"} <= set(candidate["traces"][name]), candidate
    print("OK    global getters preserve callee dictionaries, mutation, lazy imports and observers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
