import argparse
import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import json
import sys

WARM = 7000
RESULTS = {}
TRACES = {}

def trace(function):
    traces = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            executor = _opcode.get_executor(function.__code__, offset)
        except (ValueError, RuntimeError):
            continue
        names = []
        for entry in executor:
            names.append(entry[0])
            if entry[0] in {"_JUMP_TO_TOP", "_EXIT_TRACE", "_DYNAMIC_EXIT"}:
                break
        traces.append(names)
    TRACES[function.__name__] = traces

def float_expression(n, zero):
    result = 0.0
    caught = []
    for i in range(n):
        denominator = 0.0 if i == zero else 4.0
        try:
            result = (1.5 + (2.0 * 3.0)) + (4.0 * (5.0 + i)) / denominator
        except ZeroDivisionError as exc:
            caught.append([i, type(exc).__name__, sys._getframe().f_code.co_name])
    return result.hex(), caught

def int_expression(n, large):
    result = 0
    for i in range(n):
        value = (1 << 70) if i == large else i
        result = (value + (value * 3)) * ((value + 2) - (value - 1))
    return result

def unpacked(n):
    result = 0
    for i in range(n):
        a, b, c = (i + 1, i + 2, i + 3)
        result = a * b - c
    return result

def leaf(value):
    return value

def calls(n):
    result = 0
    for i in range(n):
        result = (i + leaf(i + 1)) * (leaf(i + 2) + leaf(i + 3))
    return result

class Number:
    def __add__(self, other):
        raise ValueError("number")

def callback(n, special):
    result = None
    errors = []
    for i in range(n):
        value = Number() if i == special else i
        try:
            result = (i + 1) + ((value + 2) + (i + 3))
        except ValueError as exc:
            errors.append([i, str(exc), sys._getframe().f_code.co_name])
    return result, errors

def generated(n):
    for i in range(n):
        yield (i + 1) * (i + 2) - (i + 3)

def generator_loop(n):
    result = 0
    for value in generated(n):
        result += value
    return result

for function, extra in [(float_expression, -1), (int_expression, -1),
                        (unpacked, None), (calls, None), (callback, -1),
                        (generator_loop, None)]:
    args = (WARM,) if extra is None else (WARM, extra)
    RESULTS[function.__name__] = function(*args)
    trace(function)
RESULTS["float exit"] = float_expression(WARM + 1, WARM)
RESULTS["int exit"] = int_expression(WARM + 1, WARM)
RESULTS["callback exit"] = callback(WARM + 1, WARM)
print(json.dumps({"results": RESULTS, "traces": TRACES}, sort_keys=True))
'''


def run(python, jit):
    env = dict(os.environ, PYTHON_JIT="1" if jit else "0")
    completed = subprocess.run([python, "-s", "-c", PROGRAM], env=env,
                               capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode:
        raise AssertionError(f"{python} JIT={int(jit)} exited {completed.returncode}:\n{completed.stderr}")
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference")
    options = parser.parse_args()
    candidate = run(sys.executable, True)
    interpreter = run(sys.executable, False)
    assert candidate["results"] == interpreter["results"], (candidate, interpreter)
    results = candidate["results"]
    assert results["float exit"][1] == [[7000, "ZeroDivisionError", "float_expression"]]
    assert results["int exit"] == 12 * (1 << 70)
    assert results["callback exit"][1] == [[7000, "number", "callback"]]
    assert all(candidate["traces"].values()), candidate["traces"]
    if options.reference:
        reference = run(options.reference, True)
        assert candidate["results"] == reference["results"], (candidate, reference)
        changes = {}
        for name, traces in candidate["traces"].items():
            before = sum(trace.count("_SPILL_OR_RELOAD") for trace in reference["traces"][name])
            after = sum(trace.count("_SPILL_OR_RELOAD") for trace in traces)
            assert after <= before, (name, before, after)
            changes[name] = [before, after]
        assert any(after < before for before, after in changes.values()), changes
        print("SPILLS", json.dumps(changes, sort_keys=True))
    print("OK    cache allocation preserves arithmetic, calls, generators and exception exits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
