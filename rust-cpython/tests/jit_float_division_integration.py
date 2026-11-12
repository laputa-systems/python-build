import json
import os
import subprocess
import sys


PROGRAM = r'''
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
        value = 1.0 / denominator
    return value

try:
    loop(7000, sys._jit.is_active)
except ZeroDivisionError as error:
    tb = error.__traceback__
    while tb.tb_next:
        tb = tb.tb_next
    instruction = next(value for value in dis.get_instructions(tb.tb_frame.f_code)
                       if value.offset == tb.tb_lasti)
    print(json.dumps({"error": [type(error).__name__, str(error),
        tb.tb_frame.f_code.co_name, instruction.opname, tb.tb_lineno],
        "active": tb.tb_frame.f_locals["was_active"]}))
else:
    raise AssertionError("loop division did not fail")
'''


def run(jit):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0"),
        capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, (jit, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference = run(True), run(False)
    assert candidate["error"] == reference["error"], (candidate, reference)
    assert reference["error"][:4] == ["ZeroDivisionError", "division by zero", "loop", "BINARY_OP"]
    assert candidate["active"] is True
    print("OK    active-JIT float division preserves the interpreter exception text and location")
    return 0


if __name__ == "__main__":
    sys.exit(main())
