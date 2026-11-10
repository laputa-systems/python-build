import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import array
import json
import sys

WARM = 6000


class IntSub(int):
    pass


class ArraySub(array.array):
    pass


class Overriding(array.array):
    def __getitem__(self, index):
        if isinstance(index, int) and 0 <= index < len(self):
            return ("override", index)
        return super().__getitem__(index)


class ResizingIndex:
    def __init__(self, target, items):
        self.target = target
        self.items = items

    def __index__(self):
        self.target.extend(self.items)
        return 0


def show(value):
    if isinstance(value, float):
        return ["float", float.hex(value)]
    if isinstance(value, array.array):
        return ["array", value.typecode, [show(item) for item in value]]
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


def loads_d(n, data, scale):
    acc = 0.0
    for k in range(1, n):
        acc = acc + data[k] * scale + data[k - 1]
    return acc


def loads_i(n, idx):
    acc = 0
    for k in range(1, n):
        acc = acc + idx[k] + idx[k - 1] * 3
    return acc


def negatives(n, data):
    acc = 0.0
    last = len(data)
    for k in range(n):
        acc = acc + data[(k % 11) - 5] + data[-(k % 7) - 1]
        if k % 97 == 0:
            acc = acc + data[-last]
    return acc


def out_of_range(n, data):
    values = []
    size = len(data)
    for k in range(n):
        index = size if k % 53 == 0 else k % size
        try:
            values.append(show(data[index]))
        except IndexError as error:
            values.append(show(error))
        index = -size - 1 if k % 71 == 0 else -(k % size) - 1
        try:
            values.append(show(data[index]))
        except IndexError as error:
            values.append(show(error))
    return values[-10:], len(values)


def wrong_types(n, data, keys):
    values = []
    for k in range(n):
        key = keys[k % len(keys)]
        try:
            values.append(show(data[key]))
        except Exception as error:
            values.append(show(error))
    return values[-14:], len(values)


def empty_arrays(n, doubles, ints):
    values = []
    for k in range(n):
        try:
            values.append(show(doubles[0]))
        except IndexError as error:
            values.append(show(error))
        try:
            values.append(show(ints[-1]))
        except IndexError as error:
            values.append(show(error))
    return values[-4:], len(values)


def mixed_typecodes(n, doubles, ints):
    values = []
    total = 0.0
    for k in range(n):
        target = doubles if k % 2 == 0 else ints
        value = target[k % len(target)]
        total = total + value
        values.append(show(value))
    return total, values[-6:], len(values)


def subclasses(n, plain, overriding):
    values = []
    for k in range(n):
        values.append(show(plain[k % len(plain)]))
        values.append(show(overriding[k % len(overriding)]))
    return values[-10:], len(values)


def other_typecodes(n):
    values = []
    floats = array.array("f", [k % 9 + 0.25 for k in range(32)])
    longs = array.array("l", [k % 13 - 6 for k in range(32)])
    shorts = array.array("h", [k % 7 - 3 for k in range(32)])
    for k in range(n):
        index = k % len(floats)
        values.append(show(floats[index]))
        values.append(show(longs[index]))
        values.append(show(shorts[index]))
    return values[-9:], len(values)


def resized(n):
    values = []
    doubles = array.array("d", [0.0, 1.0, 2.0])
    ints = array.array("i", [10, 11, 12])
    for k in range(n):
        index = k % 5
        if k % 3 == 0:
            doubles.append(float(k))
            ints.append(k)
        elif k % 3 == 1 and len(doubles) > 1:
            doubles.pop()
            ints.pop()
        try:
            values.append((show(doubles[index]), show(ints[index])))
        except IndexError as error:
            values.append(show(error))
    return values[-8:], len(values)


def resizing_index(n):
    values = []
    doubles = array.array("d", [0.0, 1.0])
    ints = array.array("i", [5, 6])
    for k in range(n):
        index = ResizingIndex(doubles, [float(k), float(k) + 0.5])
        int_index = ResizingIndex(ints, [k, k + 1])
        values.append((show(doubles[index]), show(ints[int_index])))
    return values[-8:], len(values)


BOUNDARY = [2 ** 30 - 1, 2 ** 30, -2 ** 30, -2 ** 30 - 1, 2 ** 31 - 1, -2 ** 31, 7]


def boundary_ints(n, ints):
    expected = [int(str(value)) for value in BOUNDARY]
    checks = []
    for k in range(n):
        index = k % len(ints)
        value = ints[index]
        want = expected[index]
        if k >= n - len(ints):
            checks.append([value == want, hash(value) == hash(want),
                           {want: True}.get(value, False), str(value) == str(want),
                           value - want == 0])
    return checks


def same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        return float.hex(left) == float.hex(right) or (left != left and right != right)
    return left == right and hash(left) == hash(right)


def reference_loads(n):
    """Checks specialized loads against tolist(), which never runs BINARY_OP."""
    doubles = array.array("d", [0.0, -0.0, 1.5, float("inf"), float("-inf"),
                                float("nan"), 5e-324, -1.7976931348623157e308])
    ints = array.array("i", [0, 1, -1, 255, 256, -5, -6, 2 ** 30 - 1, 2 ** 30,
                             -2 ** 30, -2 ** 30 - 1, 2 ** 31 - 1, -2 ** 31])
    bad = []
    for values in (doubles, ints):
        expected = values.tolist()
        size = len(values)
        indices = sorted({0, 1, size - 1, size, size + 1, -1, -size, -size - 1,
                          2 ** 30 - 1, -2 ** 30 + 1})
        for k in range(n):
            index = indices[k % len(indices)]
            try:
                # Separate sites, so each typecode keeps its own specialization.
                got = values[index] if values is doubles else values[index]
            except IndexError:
                got = IndexError
            want = expected[index] if -size <= index < size else IndexError
            if not (got is want or same(got, want)):
                bad.append([values.typecode, index, repr(got), repr(want)])
    return bad[:8]


def run(n):
    data = array.array("d", [k * 0.5 + 0.25 for k in range(n)])
    idx = array.array("i", [k % 97 - 48 for k in range(n)])
    data[WARM + 30] = -0.0
    idx[WARM + 40] = 0
    keys = [1.0, "1", True, IntSub(2), 2 ** 70, slice(1, 4), None, 3]
    plain = ArraySub("d", [k * 0.25 for k in range(16)])
    overriding = Overriding("d", [k * 0.5 for k in range(16)])
    mixed_d = array.array("d", [k * 0.125 for k in range(32)])
    mixed_i = array.array("i", [k - 16 for k in range(32)])
    empty_d = array.array("d")
    empty_i = array.array("i")
    boundary = array.array("i", BOUNDARY)
    results = {}
    probes = {}
    for name, function, args in (
            ("loads_d", loads_d, (n, data, 1.5)),
            ("loads_i", loads_i, (n, idx)),
            ("negatives", negatives, (n, data)),
            ("out_of_range", out_of_range, (n, data)),
            ("wrong_types", wrong_types, (n, data, keys)),
            ("empty_arrays", empty_arrays, (n, empty_d, empty_i)),
            ("mixed_typecodes", mixed_typecodes, (n, mixed_d, mixed_i)),
            ("subclasses", subclasses, (n, plain, overriding)),
            ("other_typecodes", other_typecodes, (n,)),
            ("resized", resized, (n,)),
            ("resizing_index", resizing_index, (n,)),
            ("boundary_ints", boundary_ints, (n, boundary)),
            ("reference_loads", reference_loads, (n,))):
        results[name] = function(*args)
        probes[name] = uops(function)
    print(json.dumps({"results": show(results), "uops": probes}, sort_keys=True))


run(WARM + 400)
'''

EXPECTED_UOPS = {
    "loads_d": "_BINARY_OP_SUBSCR_ARRAY_D",
    "loads_i": "_BINARY_OP_SUBSCR_ARRAY_I",
}

# JIT on and off share the tier-1 specialization, so comparing them alone
# cannot see a tier-1 defect; boundary_ints and reference_loads check absolute
# results instead.


def run(jit: bool) -> dict[str, object]:
    env = dict(os.environ, PYTHON_JIT="1" if jit else "0")
    completed = subprocess.run([sys.executable, "-c", PROGRAM], env=env,
                               capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        raise AssertionError(f"PYTHON_JIT={int(jit)} exited {completed.returncode}:\n"
                             f"{completed.stderr[-4000:]}")
    return json.loads(completed.stdout)


def main() -> int:
    jit, interpreter, again = run(True), run(False), run(False)
    failures = []
    for field in sorted(set(interpreter) - {"uops"}):
        if jit[field] != interpreter[field]:
            failures.append(f"{field} differs with the JIT:\n  jit {jit[field]}\n  off {interpreter[field]}")
    if interpreter != again:
        failures.append("the JIT-off runs disagree; the program is not deterministic")
    for result in (jit, interpreter):
        checks = [item for row in result["results"] if row[0] == "boundary_ints" for item in row[1]]
        if not checks or any(check != ["bool", "True"] for entry in checks for check in entry):
            failures.append(f"array('i') items outside the compact range box wrong: {checks}")
    for result in (jit, interpreter):
        bad = [row[1] for row in result["results"] if row[0] == "reference_loads"]
        if bad != [[]]:
            failures.append(f"array loads disagree with tolist(): {bad}")
    for name, expected in EXPECTED_UOPS.items():
        if expected not in jit["uops"][name]:
            failures.append(f"{name} never ran the array specialization: missing {expected}")
    for failure in failures:
        print(f"FAIL  {failure}")
    if not failures:
        print(f"OK    {len(EXPECTED_UOPS)} array subscript kernels match with the JIT on and off")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
