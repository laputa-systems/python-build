import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import json
import math
import operator
import struct

WARM = 6000
NAN = float("nan")
INF = float("inf")


class IntSub(int):
    pass


class FloatSub(float):
    pass


def show(value):
    if isinstance(value, float):
        return ["float", struct.pack("<d", value).hex()]
    if isinstance(value, complex):
        return ["complex", struct.pack("<d", value.real).hex(),
                struct.pack("<d", value.imag).hex()]
    if isinstance(value, BaseException):
        return ["error", type(value).__name__, str(value)]
    if isinstance(value, (list, tuple)):
        return [show(item) for item in value]
    if isinstance(value, dict):
        return sorted((key, show(item)) for key, item in value.items())
    return [type(value).__name__, repr(value)]


def executor_entries(function):
    code = function.__code__
    for offset in range(0, len(code.co_code), 2):
        try:
            executor = _opcode.get_executor(code, offset)
        except (ValueError, RuntimeError):
            continue
        yield from executor


def uops(function):
    return sorted({entry[0] for entry in executor_entries(function)})


def add_hot(n):
    acc = 0.0
    for _ in range(n):
        acc += 2147483647
    return acc


def add_hot_reference(n):
    acc = 0.0
    for _ in range(n):
        acc = operator.add(acc, 2147483647)
    return acc


ADD_VALUES = [0, 1, -1, 7, 2 ** 30 - 1, 2 ** 30, -2 ** 30, 2 ** 31 - 1,
              2 ** 53, 2 ** 53 + 1, 2 ** 60, -2 ** 60, IntSub(11), True]


def add_values(n, values):
    acc = 0.0
    for k in range(n):
        acc += values[k % len(values)]
    return acc


def add_values_reference(n, values):
    acc = 0.0
    for k in range(n):
        acc = operator.add(acc, values[k % len(values)])
    return acc


ADD_ERROR_VALUES = [2 ** 2000, 3, -2 ** 2000, 5]


def add_errors(n, values):
    acc = 0.0
    seen = []
    for k in range(n):
        try:
            acc += values[k % len(values)]
        except BaseException as error:
            seen.append([k, show(error)])
    return acc, seen[-8:], len(seen)


def add_errors_reference(n, values):
    acc = 0.0
    seen = []
    for k in range(n):
        try:
            acc = operator.add(acc, values[k % len(values)])
        except BaseException as error:
            seen.append([k, show(error)])
    return acc, seen[-8:], len(seen)


def add_subclasses(n):
    acc = 0.0
    for k in range(n):
        acc += FloatSub(0.5) if k % 2 else True
    return acc


def add_subclasses_reference(n):
    acc = 0.0
    for k in range(n):
        acc = operator.add(acc, FloatSub(0.5) if k % 2 else True)
    return acc


def pow_hot(n, base, exponent):
    acc = 0.0
    for _ in range(n):
        acc += base ** exponent
    return acc


def pow_hot_reference(n, base, exponent):
    acc = 0.0
    for _ in range(n):
        acc = operator.add(acc, float.__pow__(base, exponent))
    return acc


POW_CASES = [
    (2.0, 10.0),
    (2.0, -1.0),
    (-2.0, 3.0),
    (-2.0, 2.0),
    (-2.0, -3.0),
    (-2.0, 0.5),
    (-8.0, 1.0 / 3.0),
    (0.0, 0.0),
    (0.0, 3.0),
    (0.0, -1.0),
    (-0.0, 3.0),
    (-0.0, 2.0),
    (-0.0, -3.0),
    (1.0, NAN),
    (NAN, 0.0),
    (NAN, 2.0),
    (NAN, -1.0),
    (2.0, NAN),
    (0.5, INF),
    (2.0, INF),
    (1.0, INF),
    (-1.0, INF),
    (-2.0, INF),
    (-2.0, -INF),
    (INF, 0.5),
    (INF, -1.0),
    (-INF, 3.0),
    (-INF, 2.0),
    (-INF, 0.5),
    (1e308, 2.0),
    (1e-308, 2.0),
    (2.0, 1e308),
    (-1.0, 1e308),
    (1.0000000000000002, 1e16),
    (0.9999999999999999, 1e16),
]


def pow_values(n, cases):
    out = []
    for k in range(n):
        base, exponent = cases[k % len(cases)]
        try:
            out.append(show(base ** exponent))
        except BaseException as error:
            out.append(show(error))
    return out


def pow_values_reference(n, cases):
    out = []
    for k in range(n):
        base, exponent = cases[k % len(cases)]
        try:
            out.append(show(float.__pow__(base, exponent)))
        except BaseException as error:
            out.append(show(error))
    return out


def pow_assign(n, exponent):
    acc = 1.5
    for _ in range(n):
        acc **= exponent
    return acc


def pow_assign_reference(n, exponent):
    acc = 1.5
    for _ in range(n):
        acc = float.__pow__(acc, exponent)
    return acc


def pow_subclass(n, base, exponent):
    acc = FloatSub(base)
    for _ in range(n):
        acc **= exponent
    return acc


def pow_subclass_reference(n, base, exponent):
    acc = float.__pow__(FloatSub(base), exponent)
    for _ in range(n - 1):
        acc = float.__pow__(acc, exponent)
    return acc


def run(n):
    results = {}
    probes = {}
    for name, function, args in (
            ("add_hot", add_hot, (n,)),
            ("add_hot_reference", add_hot_reference, (n,)),
            ("add_values", add_values, (n, ADD_VALUES)),
            ("add_values_reference", add_values_reference, (n, ADD_VALUES)),
            ("add_errors", add_errors, (n, ADD_ERROR_VALUES)),
            ("add_errors_reference", add_errors_reference, (n, ADD_ERROR_VALUES)),
            ("add_subclasses", add_subclasses, (n,)),
            ("add_subclasses_reference", add_subclasses_reference, (n,)),
            ("pow_hot", pow_hot, (n, 1.5, -1.5)),
            ("pow_hot_reference", pow_hot_reference, (n, 1.5, -1.5)),
            ("pow_values", pow_values, (n, POW_CASES)),
            ("pow_values_reference", pow_values_reference, (n, POW_CASES)),
            # An exponent of -1.0 alternates the value, so an off-by-one in
            # the iteration count changes the result instead of converging.
            ("pow_assign", pow_assign, (n, -1.0)),
            ("pow_assign_reference", pow_assign_reference, (n, -1.0)),
            ("pow_assign_odd", pow_assign, (n + 1, -1.0)),
            ("pow_assign_odd_reference", pow_assign_reference, (n + 1, -1.0)),
            ("pow_subclass", pow_subclass, (n, 0.5, -1.0)),
            ("pow_subclass_reference", pow_subclass_reference, (n, 0.5, -1.0))):
        results[name] = show(function(*args))
        probes[name] = uops(function)
    print(json.dumps({"results": results, "uops": probes}, sort_keys=True))


run(WARM + 400)
'''

EXPECTED_UOPS = {
    "add_hot": "_BINARY_OP_ADD_FLOAT_INT",
    "pow_hot": "_BINARY_OP_POWER_FLOAT",
}

# The reference functions call operator.add and float.__pow__, which never
# execute a BINARY_OP, so the JIT-off comparison alone would miss a tier-1
# defect in the specialized instruction; the results are also compared against
# those references and against the literal cases below.

LITERAL_CHECKS = r'''
import dis
import struct

WARM = 6000
INF = float("inf")
NAN = float("nan")


def bits(value):
    return struct.pack("<d", value).hex()


def power(base, exponent):
    return base ** exponent


def add_into(total, value):
    total += value
    return total


def specialized(function, name):
    return any(instr.opname == name
               for instr in dis.get_instructions(function, adaptive=True))


# Literal operands are folded at compile time and never reach BINARY_OP, so
# every case runs through a site warmed into the specialized instruction.
for _ in range(WARM):
    power(1.5, -1.5)
    add_into(0.5, 2 ** 31 - 1)
assert specialized(power, "BINARY_OP_POWER_FLOAT"), "power never specialized"
assert specialized(add_into, "BINARY_OP_ADD_FLOAT_INT"), "add_into never specialized"

POWER_CASES = [
    (2.0, 10.0, 1024.0), (-2.0, 3.0, -8.0), (-2.0, 2.0, 4.0),
    (-0.0, 3.0, -0.0), (-0.0, 2.0, 0.0), (0.0, 0.0, 1.0),
    (1.0, NAN, 1.0), (NAN, 0.0, 1.0), (0.5, INF, 0.0), (2.0, INF, INF),
    (-1.0, INF, 1.0), (-2.0, -INF, 0.0), (INF, -1.0, 0.0),
    (-INF, 3.0, -INF), (-INF, 2.0, INF), (-INF, -3.0, -0.0),
    (-1.0, 1e300, 1.0), (5e-324, 0.5, 2.2227587494850775e-162),
]
for base, exponent, expected in POWER_CASES:
    got = power(base, exponent)
    assert type(got) is float and bits(got) == bits(expected), (base, exponent, got)
for base, exponent, kind, message in (
        (0.0, -1.0, ZeroDivisionError, "zero to a negative power"),
        (-0.0, -3.0, ZeroDivisionError, "zero to a negative power"),
        (1e308, 2.0, OverflowError, None)):
    try:
        power(base, exponent)
    except kind as error:
        assert message is None or str(error) == message, str(error)
    else:
        raise AssertionError(f"{base!r} ** {exponent!r} did not raise {kind.__name__}")
result = power(-8.0, 0.5)
assert type(result) is complex, type(result)
assert abs(result.real) < 1e-15 and abs(result.imag - 2.8284271247461903) < 1e-15

ADD_CASES = [
    (0.0, 2 ** 31 - 1, 2147483647.0), (-0.0, 2 ** 31, 2147483648.0),
    (0.5, 2 ** 53 + 1, 0.5 + float(2 ** 53 + 1)), (1.0, -(2 ** 40), 1.0 - 2.0 ** 40),
    (INF, 2 ** 40, INF), (1.0, 2 ** 1023, 2.0 ** 1023),
]
for total, value, expected in ADD_CASES:
    got = add_into(total, value)
    assert type(got) is float and bits(got) == bits(expected), (total, value, got)
got = add_into(NAN, 2 ** 40)
assert got != got, got
try:
    add_into(1.0, 2 ** 2000)
except OverflowError as error:
    assert str(error) == "int too large to convert to float", str(error)
else:
    raise AssertionError("float + huge int did not raise")
'''


def run(jit: bool) -> dict[str, object]:
    env = dict(os.environ, PYTHON_JIT="1" if jit else "0")
    completed = subprocess.run([sys.executable, "-c", PROGRAM], env=env,
                               capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        raise AssertionError(f"PYTHON_JIT={int(jit)} exited {completed.returncode}:\n"
                             f"{completed.stderr[-4000:]}")
    return json.loads(completed.stdout)


def literal_checks() -> str | None:
    for jit in ("1", "0"):
        completed = subprocess.run([sys.executable, "-c", LITERAL_CHECKS],
                                   env=dict(os.environ, PYTHON_JIT=jit),
                                   capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            return f"PYTHON_JIT={jit}: {completed.stderr[-4000:]}"
    return None


def main() -> int:
    jit, interpreter, again = run(True), run(False), run(False)
    failures = []
    for field in sorted(set(interpreter["results"])):
        if jit["results"][field] != interpreter["results"][field]:
            failures.append(f"results[{field}] differs with the JIT:\n"
                            f"  jit {jit['results'][field]}\n  off {interpreter['results'][field]}")
    if interpreter != again:
        failures.append("the JIT-off runs disagree; the program is not deterministic")
    for result in (jit, interpreter):
        for hot, reference in (
                ("add_hot", "add_hot_reference"),
                ("add_values", "add_values_reference"),
                ("add_errors", "add_errors_reference"),
                ("add_subclasses", "add_subclasses_reference"),
                ("pow_hot", "pow_hot_reference"),
                ("pow_values", "pow_values_reference"),
                ("pow_assign", "pow_assign_reference"),
                ("pow_assign_odd", "pow_assign_odd_reference"),
                ("pow_subclass", "pow_subclass_reference")):
            if result["results"][hot] != result["results"][reference]:
                failures.append(f"{hot} disagrees with {reference}:\n"
                                f"  hot {result['results'][hot]}\n  ref {result['results'][reference]}")
    for name, expected in EXPECTED_UOPS.items():
        if expected not in jit["uops"][name]:
            failures.append(f"{name} never ran the specialization: missing {expected}")
    error = literal_checks()
    if error is not None:
        failures.append(f"literal semantics checks failed:\n{error}")
    for failure in failures:
        print(f"FAIL  {failure}")
    if not failures:
        print(f"OK    {len(EXPECTED_UOPS)} specialized binary-op kernels match the references, literals and JIT-off")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
