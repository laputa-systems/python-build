"""Source-mode exec compilation on the `--serc` interpreter named by SERC_PYTHON.

The C compiler converts a decimal literal of over 6,000 digits through the replaceable
`_pylong.int_from_string`, while Serc converts it natively, so replacing that function shows
which compiler handled a request.
"""

import json
import os
import subprocess
import textwrap
import unittest

SERC_PYTHON = os.environ.get("SERC_PYTHON")

ROUTING_PROBE = r'''
import __future__, _pylong, ast, importlib, json, sys, tempfile, _testcapi

sys.set_int_max_str_digits(0)
_pylong.int_from_string = lambda text: -1
literal = "1" * 7000
source = f"x = {literal}\n"


def compiler(value):
    return "c" if value == -1 else "serc"


def run(code):
    namespace = {}
    exec(code, namespace)
    return compiler(namespace["x"])


def exec_source(text):
    namespace = {}
    exec(text, namespace)
    return compiler(namespace["x"])


def run_string(text, start):
    namespace = {"__builtins__": __builtins__}
    _testcapi.run_stringflags(text, start, namespace, namespace)
    return compiler(namespace["x"])


def imported():
    with tempfile.TemporaryDirectory() as directory:
        with open(f"{directory}/serc_probe_module.py", "w") as module:
            module.write(source)
        sys.path.insert(0, directory)
        sys.dont_write_bytecode = True
        importlib.invalidate_caches()
        try:
            return compiler(importlib.import_module("serc_probe_module").x)
        finally:
            sys.path.remove(directory)


class Filename(str):
    pass


future_helper = {}
exec(
    "from __future__ import annotations\n"
    "def inheriting(text, namespace):\n"
    "    exec(text, namespace)\n"
    "def not_inheriting(text):\n"
    "    return compile(text, 'probe', 'exec', dont_inherit=True)\n",
    future_helper,
)
inherited = {}
future_helper["inheriting"](source, inherited)

print(json.dumps({
    "compile str exec": run(compile(source, "probe", "exec")),
    "compile bytes exec": run(compile(source.encode(), "probe", "exec")),
    "compile bytes exec utf-8 cookie": run(compile(b"# coding: utf-8\n" + source.encode(), "probe", "exec")),
    "exec str": exec_source(source),
    "exec bytes": exec_source(source.encode()),
    "import": imported(),
    "dont_inherit under a future": run(future_helper["not_inheriting"](source)),
    "PyRun_StringFlags exec": run_string(source.encode(), 257),
    "PyRun_StringFlags single": run_string(source.encode(), 256),
    "compile single": run(compile(source, "probe", "single")),
    "eval": compiler(eval(literal)),
    "compile eval": compiler(eval(compile(literal, "probe", "eval"))),
    "compile ast": run(compile(ast.parse(source), "probe", "exec")),
    "top-level await flag": run(compile(source, "probe", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)),
    "future flag": run(compile(source, "probe", "exec", flags=__future__.annotations.compiler_flag)),
    "inherited future": compiler(inherited["x"]),
    "latin-1 cookie": run(compile(b"# coding: latin-1\n" + source.encode(), "probe", "exec")),
    "utf8 alias cookie": run(compile(b"# coding: utf8\n" + source.encode(), "probe", "exec")),
    "str subclass filename": run(compile(source, Filename("probe"), "exec")),
    "barry_as_FLUFL future": run(compile("from __future__ import barry_as_FLUFL\n" + source, "probe", "exec")),
    "nesting deeper than Serc models": run(compile("x = " + "-" * 202 + literal, "probe", "exec")),
}))
'''

SERC_REQUESTS = {
    "compile str exec", "compile bytes exec", "compile bytes exec utf-8 cookie", "exec str",
    "exec bytes", "import", "dont_inherit under a future", "PyRun_StringFlags exec",
}

DIAGNOSTIC_PROBE = r'''
import __future__, json, sys, warnings

C_COMPILER = __future__.generator_stop.compiler_flag
sources = json.loads(sys.argv[1])


def outcome(source, flags, action):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter(action)
        try:
            compile(source, "<probe>", "exec", flags=flags)
            raised = None
        except Exception as error:
            raised = [type(error).__name__, repr(error.args)]
    return {"raised": raised,
            "warnings": [[w.category.__name__, str(w.message), w.filename, w.lineno] for w in caught]}


results = []
for text in sources:
    for source in (text, text.encode()):
        for action in ("always", "error"):
            results.append([text, action, outcome(source, 0, action), outcome(source, C_COMPILER, action)])
print(json.dumps(results))
'''

INVALID_SOURCES = [
    "x = (\n",
    "def f(:\n    pass\n",
    "if x:\nreturn\n",
    "x = 'unterminated\n",
    "nonlocal x\n",
    "return 1\n",
    "x = '\\N{NO SUCH NAME}'\n",
    "def f():\n    yield = 1\n",
    "f(**x, *y)\n",
    "match x:\n    case {1: a, 1: b}:\n        pass\n",
    "x = " + "-" * 100000 + "1\n",
    "x = " + "lambda a=" * 1000 + "0" + ": 0" * 1000 + "\n",
]

WARNING_SOURCES = [
    "x = '\\d'\n",
    "x = b'\\N{EM DASH}'\n",
    "x = '\\400'\n",
    "x = f'\\{x}'\n",
    "x = 1if y else 2\n",
    "x is 1\n",
    "x is not ('a', 1)\n",
    "1()\n",
    "[x](y)\n",
    "{1, 2}[0]\n",
    "'abc'['b']\n",
    "assert (x, 'message')\n",
    "def f():\n    try:\n        pass\n    finally:\n        return\n",
    "for x in y:\n    try:\n        pass\n    finally:\n        continue\n",
    "from . lazy import x\n",
]

REFUSAL_PROBE = r'''
import json, _testcapi

outcomes = {}
for name, source, start in (("exec", b"x = 1\n", 257), ("eval", b"1", 258)):
    try:
        outcomes[name] = type(_testcapi.run_compilestringobject(source, "a\0b", start)).__name__
    except SystemError as error:
        outcomes[name] = "SystemError: " + str(error)
print(json.dumps(outcomes))
'''

VERIFY_PROBE = r'''
import _pylong, json, sys

sys.set_int_max_str_digits(0)
literal = "1" * 7000
outcomes = {}
for name, source in (("plain", "x = 1\n"), ("large literal", f"x = {literal}\n")):
    compile(source, "probe", "exec")
    outcomes[name] = "same"
_pylong.int_from_string = lambda text: -1
try:
    compile(f"x = {literal}\n", "probe", "exec")
    outcomes["replaced _pylong"] = "same"
except SystemError as error:
    outcomes["replaced _pylong"] = str(error)
print(json.dumps(outcomes))
'''


C_API_PROBE = r'''
import __future__, _pylong, ctypes, json, sys, _testcapi

PyCF_IGNORE_COOKIE = 0x0800
sys.set_int_max_str_digits(0)
_pylong.int_from_string = lambda text: -1
literal = "1" * 7000


class PyCompilerFlags(ctypes.Structure):
    _fields_ = [("cf_flags", ctypes.c_int), ("cf_feature_version", ctypes.c_int)]


compile_string = ctypes.pythonapi.Py_CompileStringExFlags
compile_string.restype = ctypes.py_object
compile_string.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
                           ctypes.POINTER(PyCompilerFlags), ctypes.c_int]

outcomes = {}
for name in ("annotations", "barry_as_FLUFL"):
    flags = PyCompilerFlags(0, sys.version_info.minor)
    code = compile_string(f"from __future__ import {name}\nx = {literal}\n".encode(),
                          b"probe", 257, ctypes.byref(flags), -1)
    namespace = {}
    exec(code, namespace)
    outcomes[name] = ["c" if namespace["x"] == -1 else "serc",
                      flags.cf_flags == getattr(__future__, name).compiler_flag]
for name, source in (("comment", b"y = 1  # \xff\n"), ("code", b"y = '\xff'\n")):
    namespace = {}
    try:
        _testcapi.run_stringflags(source, 257, namespace, namespace, PyCF_IGNORE_COOKIE)
        outcomes[name] = namespace["y"]
    except Exception as error:
        outcomes[name] = [type(error).__name__, str(error)]
print(json.dumps(outcomes))
'''


C_COMPILER_PROBE = r'''
import json, sys, threading, warnings
warnings.simplefilter("ignore")
outcomes = {}

def small_stack():
    try:
        compile("x = " + "(" * 150 + "1" + ")" * 150, "probe", "exec")
        outcomes["small stack"] = "compiled"
    except MemoryError:
        outcomes["small stack"] = "MemoryError"

threading.stack_size(1 << 16)
thread = threading.Thread(target=small_stack)
thread.start()
thread.join()

namespace = {}
exec("""
import contextlib, sys
def main():
    try:
        raise KeyError("outer")
    finally:
        with contextlib.nullcontext():
            try:
                raise ValueError("inner")
            finally:
                return 1
result = main(), repr(sys.exception())
""", namespace)
outcomes["finally return"] = list(namespace["result"])
print(json.dumps(outcomes))
'''


@unittest.skipUnless(SERC_PYTHON, "set SERC_PYTHON to the interpreter of a --serc build")
class SercCompileTests(unittest.TestCase):
    def run_probe(self, script, *arguments, verify=False):
        env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        if verify:
            env["PYTHONSERCVERIFY"] = "1"
        completed = subprocess.run(
            [SERC_PYTHON, "-s", "-c", textwrap.dedent(script), *arguments],
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.splitlines()[-1])

    def test_only_source_exec_requests_reach_serc(self):
        compilers = self.run_probe(ROUTING_PROBE)
        self.assertEqual(
            compilers,
            {request: "serc" if request in SERC_REQUESTS else "c" for request in compilers},
        )

    def test_invalid_source_raises_the_c_compiler_exception(self):
        for text, action, serc, reference in self.run_probe(DIAGNOSTIC_PROBE, json.dumps(INVALID_SOURCES)):
            with self.subTest(source=text, action=action):
                self.assertIsNotNone(serc["raised"])
                self.assertEqual(serc, reference)

    def test_warnings_match_the_c_compiler_and_become_errors_under_an_error_filter(self):
        for text, action, serc, reference in self.run_probe(DIAGNOSTIC_PROBE, json.dumps(WARNING_SOURCES)):
            with self.subTest(source=text, action=action):
                self.assertEqual(serc, reference)
                if action == "always":
                    self.assertTrue(serc["warnings"])
                else:
                    self.assertEqual(serc["raised"][0], "SyntaxError")

    def test_a_serc_refusal_raises_instead_of_compiling_with_the_c_compiler(self):
        outcomes = self.run_probe(REFUSAL_PROBE)
        self.assertEqual(outcomes["eval"], "code")
        self.assertRegex(outcomes["exec"], r"^SystemError: Serc cannot compile 'a\\x00b': ")

    def test_c_api_callers_get_future_flags_and_non_utf8_text_compiles_as_c_does(self):
        outcomes = self.run_probe(C_API_PROBE)
        self.assertEqual(outcomes["annotations"], ["serc", True])
        self.assertEqual(outcomes["barry_as_FLUFL"], ["c", True])
        self.assertEqual(outcomes["comment"], 1)
        self.assertEqual(outcomes["code"][0], "SyntaxError")
        self.assertIn("'utf-8' codec can't decode byte 0xff", outcomes["code"][1])

    def test_the_c_compiler_handles_small_stacks_and_finally_exits(self):
        outcomes = self.run_probe(C_COMPILER_PROBE, verify=True)
        self.assertEqual(outcomes["small stack"], "MemoryError")
        self.assertEqual(outcomes["finally return"], [1, "None"])

    def test_verify_mode_raises_when_serc_and_the_c_compiler_differ(self):
        outcomes = self.run_probe(VERIFY_PROBE, verify=True)
        self.assertEqual(outcomes["plain"], "same")
        self.assertEqual(outcomes["large literal"], "same")
        self.assertRegex(outcomes["replaced _pylong"],
                         r"^PYTHONSERCVERIFY: Serc and the C compiler compiled 'probe' differently")


if __name__ == "__main__":
    unittest.main()
