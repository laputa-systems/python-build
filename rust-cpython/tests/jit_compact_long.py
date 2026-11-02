import hashlib
import sys

VALUES = (-1073741825, -1073741824, -1073741823, -257, -7, -6, -5, -1,
          0, 1, 255, 256, 257, 1073741822, 1073741823, 1073741824, 2**80)


def calculate(values, rounds):
    active = False
    result = []
    for _ in range(rounds):
        for left in values:
            for right in values:
                result.append((left + right, left - right, left * right))
                active |= sys._jit.is_active()
    return active, hashlib.sha256(repr(result).encode()).hexdigest()


def trace(frame, event, arg):
    return trace


active, digest = calculate(VALUES, 100)
assert active
print(digest)
sys.settrace(trace)
try:
    _, digest = calculate(VALUES[:8], 20)
finally:
    sys.settrace(None)
print(digest)


class CustomInt(int):
    def __add__(self, other):
        return int(self) + int(other) + 1000


def change_type():
    total = 0
    active = False
    for index in range(20000):
        value = CustomInt(index) if index % 1000 == 999 else index
        total += value + 1
        active |= sys._jit.is_active()
    return active, total


active, total = change_type()
assert active
assert total == sum(range(20000)) + 20000 + 20000
print(total)
