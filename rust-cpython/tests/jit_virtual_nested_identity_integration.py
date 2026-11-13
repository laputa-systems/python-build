import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import dis
import json
import sys

def trace(fn):
    entries = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            entries.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return entries

class Vector:
    __LAYOUT__
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z
    def mustBeVector(self):
        return self
    def dot(self, other):
        other.mustBeVector()
        return self.x * other.x + self.y * other.y + self.z * other.z

def loop(n, left, right, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        result = left.dot(right)
        if index == 6999:
            entries = trace(loop)
    return result, entries, was_active

def loop_limits(n, left, right, active, set_limit, limits):
    was_active = False
    for index in range(n):
        was_active = active()
        set_limit(limits.pop())
        result = left.dot(right)
    return result, was_active

def descend(depth, left, right, limits):
    if depth:
        return descend(depth - 1, left, right, limits)
    return loop_limits(32, left, right, sys._jit.is_active, sys.setrecursionlimit, limits)

def probe(left, right):
    old_limit = sys.getrecursionlimit()
    rows = []
    try:
        loop_limits(7000, left, right, sys._jit.is_active, sys.setrecursionlimit, [1000] * 7000)
        for depth in range(90, 98):
            try:
                result, active = descend(depth, left, right, [100] + [1000] * 31)
            except (RecursionError, ValueError) as error:
                frames = []
                active = False
                tb = error.__traceback__
                while tb:
                    frame = tb.tb_frame
                    instruction = next((i for i in dis.get_instructions(frame.f_code) if i.offset == tb.tb_lasti), None)
                    frames.append([frame.f_code.co_name, instruction.opname if instruction else None])
                    if frame.f_code is loop_limits.__code__:
                        active = frame.f_locals['was_active']
                    tb = tb.tb_next
                rows.append([depth, type(error).__name__, str(error), frames, active])
            else:
                rows.append([depth, result, active])
            finally:
                sys.setrecursionlimit(1000)
    finally:
        sys.setrecursionlimit(old_limit)
    return rows

def run():
    left, right = Vector(1.25, 2.5, 3.75), Vector(2.0, 4.0, 6.0)
    result, entries, active = loop(7000, left, right, sys._jit.is_active)
    counts = [sys.getrefcount(left), sys.getrefcount(right)]
    value, _, _ = loop(16, left, right, sys._jit.is_active)
    counts = [sys.getrefcount(left) - counts[0], sys.getrefcount(right) - counts[1]]
    recursion = probe(left, right)
    return {'result': [result, value, counts], 'trace': entries, 'active': active, 'recursion': recursion}

print(json.dumps(run()))
'''


def run(jit, optimize, managed):
    layout = 'pass' if managed else "__slots__ = ('x', 'y', 'z')"
    env = dict(os.environ, PYTHON_JIT='1' if jit else '0',
               PYTHON_UOPS_OPTIMIZE='1' if optimize else '0')
    process = subprocess.run([sys.executable, '-s', '-c', PROGRAM.replace('__LAYOUT__', layout)],
                             env=env, capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def main():
    baseline = '--incumbent' in sys.argv
    for managed in (False, True):
        candidate, off, noopt = run(True, True, managed), run(False, True, managed), run(True, False, managed)
        assert candidate['result'] == off['result'] == noopt['result'] == [35.0, 35.0, [0, 0]]
        normalize = lambda rows: [row[:-1] for row in rows]
        assert normalize(candidate['recursion']) == normalize(off['recursion']) == normalize(noopt['recursion'])
        assert candidate['active']
        assert any(row[1] == 'RecursionError' and row[-1] and ['dot', 'CALL'] in row[-2]
                   for row in candidate['recursion'] if len(row) == 5)
        names = [entry[0] for entry in candidate['trace']]
        if baseline:
            assert '_BEGIN_VIRTUAL_CALL' not in names and '_PUSH_FRAME' in names
        else:
            start, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
            assert '_CALL_PY_LEAF' in names[start:end]
            assert '_PUSH_FRAME' not in names[start:end]
        assert '_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in noopt['trace']]
    print('OK nested identity results, recursion, and executor shape')


if __name__ == '__main__':
    main()
