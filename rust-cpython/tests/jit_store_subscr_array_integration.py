import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import array
import json
import sys

WARM = 12000


def store_d(n, data, values):
    for k in range(n):
        data[k % len(data)] = values[k % len(values)]


def store_d_negative(n, data, values):
    for k in range(n):
        data[(k % 7) - 3] = values[k % len(values)]


def store_i(n, data, values):
    for k in range(n):
        data[k % len(data)] = values[k % len(values)]


def store_odd(n, data, values):
    out = []
    for k in range(n):
        try:
            data[k % len(data)] = values[k % len(values)]
            out.append("ok")
        except Exception as error:
            out.append(type(error).__name__)
    return out[-8:], list(data)


def uops(function):
    code = function.__code__
    found = set()
    for offset in range(0, len(code.co_code), 2):
        try:
            executor = _opcode.get_executor(code, offset)
        except (ValueError, RuntimeError):
            continue
        found.update(entry[0] for entry in executor)
    return sorted(found)


values = [1.5, -2.25, 4, 0]
data = array.array("d", [0.0] * 16)
store_d(WARM + 50, data, values)
probe = uops(store_d)

neg = array.array("d", [0.0] * 8)
store_d_negative(WARM + 50, neg, values)
neg_probe = uops(store_d_negative)
neg_tail = list(neg)

ints = array.array("i", [0] * 8)
store_i(WARM + 50, ints, [3, -1, 7, 0])
ints_probe = uops(store_i)
ints_tail = list(ints)

class IntIndex(int):
    pass

class FloatValue(float):
    pass

class ArraySub(array.array):
    pass

class Overriding(array.array):
    def __setitem__(self, index, value):
        super().__setitem__(index, value * 2)

odd_values = [True, IntIndex(4), FloatValue(2.5), 7, 2.5, None, "x", -3]
odd = array.array("d", [0.0] * 8)
odd_result = store_odd(400, odd, odd_values)

sub = ArraySub("d", [0.0] * 8)
store_d(300, sub, values)
sub_result = list(sub)

over = Overriding("d", [1.0] * 8)
over[0] = 2.0
store_d(300, over, values)
over_result = list(over)

real_array = array.array


class Fake:
    def __len__(self):
        return 8

    def __setitem__(self, index, value):
        pass


array.array = Fake
try:
    store_d(50, Fake(), values)
    spoof_result = "ok"
finally:
    array.array = real_array

big = array.array("d", [0.0] * 4)
try:
    big[2 ** 40] = 1.0
except IndexError:
    big_result = "IndexError"
else:
    big_result = "stored"

print(json.dumps({
    "results": [list(data), list(neg), neg_tail, list(ints), ints_tail,
                odd_result, sub_result, over_result, big_result, spoof_result],
    "uops": {"d": probe, "d_negative": neg_probe, "i": ints_probe, "odd": uops(store_odd)},
}))
'''


def run(jit):
    env = dict(os.environ, PYTHON_JIT="1" if jit else "0")
    completed = subprocess.run([sys.executable, "-c", PROGRAM], env=env,
                               capture_output=True, text=True, timeout=600, check=False)
    if completed.returncode != 0:
        raise AssertionError(f"PYTHON_JIT={int(jit)} exited {completed.returncode}:\n"
                             f"{completed.stderr[-4000:]}")
    return json.loads(completed.stdout)


def main():
    jit, interpreter = run(True), run(False)
    assert jit["results"] == interpreter["results"], (jit["results"], interpreter["results"])
    assert "_STORE_SUBSCR_ARRAY_D" in jit["uops"]["d"], jit["uops"]
    assert "_STORE_SUBSCR_ARRAY_D" in jit["uops"]["d_negative"], jit["uops"]
    assert "_STORE_SUBSCR_ARRAY_D" not in jit["uops"]["i"], jit["uops"]
    print("OK array('d') store subscript specialization matches with the JIT on and off")


if __name__ == "__main__":
    main()
