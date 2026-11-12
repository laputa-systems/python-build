import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import dis
import json
import sys

WARM = 7000
RESULTS = {}
TRACES = {}
ACTIVE = []

def encode(value):
    if isinstance(value, complex):
        return ["complex", value.real.hex(), value.imag.hex()]
    return ["float", value.hex()]

def trace(function):
    result = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            result.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            pass
    return result

def make(operation):
    namespace = {"trace": trace, "WARM": WARM}
    exec(f"""def arithmetic(a, b):
    first = a + b
    return first {operation} b

def loop(n, fn, source, active):
    entries = []
    observed = []
    was_active = False
    for index in range(n):
        a, b = source.pop()
        was_active = active()
        result = (a * a + b * b) + fn(a, b) + (a * b + b * a)
        if isinstance(result, complex):
            observed.append(was_active)
        if index == WARM - 1:
            entries = trace(loop)
    return result, entries, observed, was_active
""", namespace)
    return namespace["arithmetic"], namespace["loop"]

for label, operation in (("divide", "/"), ("power", "**")):
    fn, loop = make(operation)
    source = [(1.25, 2.5)] * WARM
    result, entries, _, active = loop(WARM, fn, source, sys._jit.is_active)
    RESULTS[label] = [encode(result), len(source)]
    TRACES[label] = entries
    ACTIVE.append(active)
    if label == "divide":
        source = [(1.25, 0.0)] + [(1.25, 2.5)] * (WARM + 15)
        try:
            raise ValueError("outer")
        except ValueError:
            try:
                loop(WARM + 16, fn, source, sys._jit.is_active)
            except ZeroDivisionError as error:
                frames = []
                tb = error.__traceback__
                while tb:
                    frame = tb.tb_frame
                    instruction = next(value for value in dis.get_instructions(frame.f_code)
                                       if value.offset == tb.tb_lasti)
                    values = {}
                    if frame.f_code is loop.__code__:
                        ACTIVE.append(frame.f_locals["was_active"])
                        TRACES["error"] = frame.f_locals["entries"]
                    elif frame.f_code is fn.__code__:
                        values = {key: value.hex() for key, value in frame.f_locals.items()}
                    frames.append([frame.f_code.co_name, instruction.opname,
                        instruction.positions.col_offset, instruction.positions.end_col_offset, values])
                    tb = tb.tb_next
                RESULTS["error"] = [type(error).__name__, str(error), str(error.__context__), frames, len(source)]
            else:
                raise AssertionError("division did not fail")
    else:
        source = [(-2.5, 0.5)] * 8 + [(1.25, 2.5)] * (WARM + 8)
        result, entries, observed, _ = loop(WARM + 16, fn, source, sys._jit.is_active)
        ACTIVE.extend(observed)
        RESULTS["complex"] = [encode(result), len(source), len(observed)]
        TRACES["complex"] = entries

print(json.dumps({"results": RESULTS, "traces": TRACES, "active": ACTIVE}, sort_keys=True))
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, "-s", "-c", PROGRAM],
        env=dict(os.environ, PYTHON_JIT="1" if jit else "0",
                 PYTHON_UOPS_OPTIMIZE="1" if optimize else "0"),
        capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, (jit, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    assert candidate["results"] == reference["results"] == noopt["results"], (
        candidate["results"], reference["results"], noopt["results"])
    assert reference["results"]["divide"] == [["float", 15.5625.hex()], 0]
    assert reference["results"]["power"] == [["float", ((7.8125 + 3.75 ** 2.5) + 6.25).hex()], 0]
    error = reference["results"]["error"]
    assert error[:3] == ["ZeroDivisionError", "division by zero", "outer"]
    assert error[-1] == 0
    assert error[-2][-1][-1] == {"a": 1.25.hex(), "b": float(0).hex(), "first": 1.25.hex()}
    assert reference["results"]["complex"][1:] == [0, 8]
    assert candidate["active"] == [True] * 11, candidate["active"]
    for label, entries in candidate["traces"].items():
        names = [entry[0] for entry in entries]
        begin = names.index("_BEGIN_VIRTUAL_CALL")
        end = names.index("_RETURN_VIRTUAL_CALL")
        assert "_MATERIALIZE_VIRTUAL_FRAME" in names, (label, names)
        assert any(name.endswith("_FLOAT_UNBOXED") for name in names[:begin]), (label, names)
        assert any(name.endswith("_FLOAT_UNBOXED") for name in names[end + 1:]), (label, names)
        assert not any("UNBOX" in name for name in names[begin:end + 1]), (label, names)
        assert "_BOX_FLOAT" in names[:begin], (label, names)
    assert all("_BEGIN_VIRTUAL_CALL" not in {entry[0] for entry in entries}
               for entries in noopt["traces"].values())
    print("OK    regional unboxing preserves boxed virtual calls and live caller prefixes on errors and exits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
