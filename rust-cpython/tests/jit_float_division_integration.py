import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import dis
import json
import sys

def loop(n, active):
    denominator = float(n)
    value = 0.0
    was_active = False
    for i in range(n):
        was_active = active()
        denominator = denominator - 1.0
        value = __EXPRESSION__
    return value

try:
    loop(7000, sys._jit.is_active)
except ZeroDivisionError as error:
    tb = error.__traceback__
    while tb.tb_next:
        tb = tb.tb_next
    instruction = next(value for value in dis.get_instructions(tb.tb_frame.f_code)
                       if value.offset == tb.tb_lasti)
    names = set()
    for offset in range(0, len(loop.__code__.co_code), 2):
        try:
            names.update(entry[0] for entry in _opcode.get_executor(loop.__code__, offset))
        except (ValueError, RuntimeError):
            continue
    print(json.dumps({"error": [type(error).__name__, str(error),
        tb.tb_frame.f_code.co_name, instruction.opname, tb.tb_lineno],
        "active": tb.tb_frame.f_locals["was_active"], "uops": sorted(names)}))
else:
    raise AssertionError("loop division did not fail")
'''


def run(jit, expression):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM.replace("__EXPRESSION__", expression)],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0"),
        capture_output=True, text=True, timeout=20, check=False)
    assert completed.returncode == 0, (jit, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    for expression, opcode in (("1.0 / denominator", "_BINARY_OP_TRUEDIV_FLOAT"),
            ("(denominator + 1.0) / denominator", "_BINARY_OP_TRUEDIV_FLOAT_INPLACE"),
            ("1.0 / (denominator + 0.0)", "_BINARY_OP_TRUEDIV_FLOAT_INPLACE_RIGHT")):
        candidate, reference = run(True, expression), run(False, expression)
        assert candidate["error"] == reference["error"], (expression, candidate, reference)
        assert reference["error"][:4] == ["ZeroDivisionError", "division by zero", "loop", "BINARY_OP"]
        assert candidate["active"] is True
        assert opcode in candidate["uops"], (expression, candidate["uops"])
    print("OK    three active-JIT float divisions preserve interpreter exception text and location")
    return 0


if __name__ == "__main__":
    sys.exit(main())
