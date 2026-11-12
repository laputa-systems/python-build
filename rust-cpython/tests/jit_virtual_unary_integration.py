import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import _testcapi
import dis
import json
import sys
from types import FunctionType

WARM = 7000

def trace(fn):
    result = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            result.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return result

def direct(a, b):
    return -(a + b)

def alias(a, b):
    first = a + b
    other = first
    return -other

def argument(a, b):
    first = a + b
    return -a

def exact(a, b):
    first = b + a
    return -a

class Source:
    def __init__(self, values):
        self.values = values
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

class Callback:
    def __neg__(self):
        events.append('negative')
        return 47
    def __add__(self, other):
        events.append('add')
        return self

def loop(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        a, b = source.pop()
        was_active = active()
        result = fn(a, b)
        if index == WARM - 1:
            entries = trace(loop)
    return result, entries, was_active

loop = FunctionType(loop.__code__, {'trace': trace, 'WARM': WARM})
loop.__globals__['loop'] = loop

def encode(value):
    return [type(value).__name__, value.hex() if type(value) is float else str(value)]

events = []
results = []
traces = []
active = []
for fn, warm in ((direct, (1.25, 2.5)), (alias, (1.25, 2.5)),
                 (direct, (100000, 3)), (alias, (100000, 3))):
    samples = [warm]
    if type(warm[0]) is float:
        samples += [(-0.0, -0.0), (float('nan'), 1.0), (float('inf'), 1.0)]
    else:
        samples += [(2**29, 3), (2**30, 3), (2**100, 2), (-2**100, 2)]
    samples += [(Callback(), 1)]
    for a, b in samples:
        before = [sys.getrefcount(a), sys.getrefcount(b)]
        source = Source([(a, b)] + [warm] * (WARM + 15))
        value, entries, was_active = loop(WARM + 16, fn, source, sys._jit.is_active)
        results.append([encode(value), source.calls, len(source.values),
                        [sys.getrefcount(a) - before[0], sys.getrefcount(b) - before[1]]])
        active.append(was_active)
        if (a, b) == warm:
            traces.append(entries)

for a in (2**70, -2**70, 10**300):
    b = 1.25
    before = [sys.getrefcount(a), sys.getrefcount(b)]
    source = Source([(a, b)] + [(2**70, 1.25)] * (WARM + 15))
    value, entries, was_active = loop(WARM + 16, exact, source, sys._jit.is_active)
    results.append([encode(value), source.calls, len(source.values),
                    [sys.getrefcount(a) - before[0], sys.getrefcount(b) - before[1]]])
    active.append(was_active)
    traces.append(entries)

allocation_active = []
allocation_traces = []
class Arm:
    def __init__(self, failures):
        self.failures = failures
    def __del__(self):
        frame = sys._getframe(1)
        allocation_active.append(frame.f_locals['was_active'])
        allocation_traces.append(frame.f_locals['entries'])
        _testcapi.set_nomemory(0, self.failures)

def allocating(n, fn, source, active):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(100000, 3)
        if index == WARM - 1:
            entries = trace(allocating)
    return held, entries, was_active

allocating = FunctionType(allocating.__code__, {'trace': trace, 'WARM': WARM})
allocating.__globals__['allocating'] = allocating
allocation = {}
for failures in (1, 16):
    try:
        allocating(WARM + 18, argument, [None, Arm(failures)] + [None] * (WARM + 16), sys._jit.is_active)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        state = None
        tb = error.__traceback__
        while tb:
            if tb.tb_frame.f_code is argument.__code__:
                inst = next(i for i in dis.get_instructions(argument) if i.offset == tb.tb_lasti)
                state = [inst.opname, inst.positions.col_offset, inst.positions.end_col_offset,
                         {k: encode(v) for k, v in tb.tb_frame.f_locals.items()}]
            tb = tb.tb_next
        allocation[failures] = state
    else:
        raise AssertionError('allocation did not fail')
print(json.dumps({'results': results, 'traces': traces, 'active': active,
                  'events': events, 'allocation': allocation, 'allocation_active': allocation_active,
                  'allocation_traces': allocation_traces}))
'''


def run(jit, optimize=True):
    result = subprocess.run([sys.executable, '-s', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, (jit, optimize, result.returncode, result.stderr)
    return json.loads(result.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    assert candidate['results'] == reference['results'] == noopt['results']
    expected = [['float', (-3.75).hex()], ['float', (0.0).hex()], ['float', 'nan'],
                ['float', '-inf'], ['int', '47']]
    integers = [['int', str(value)] for value in (-100003, -(2**29 + 3),
                -(2**30 + 3), -(2**100 + 2), 2**100 - 2, 47)]
    expected = expected * 2 + integers * 2 + [['int', str(value)] for value in (-2**70, 2**70, -(10**300))]
    assert [row[0] for row in candidate['results']] == expected, candidate['results']
    assert all(row[1:] == [7016, 0, [0, 0]] for row in candidate['results']), candidate['results']
    assert candidate['events'] == reference['events'] == noopt['events'] == ['add', 'negative'] * 4
    assert all(candidate['active']) and candidate['allocation_active'] == [True, True]
    for result in (candidate, reference, noopt):
        state = result['allocation']['1']
        assert state is not None and state[:3] == ['UNARY_NEGATIVE', 11, 13], state
        assert state[3] == {'a': ['int', '100000'], 'b': ['int', '3'],
                            'first': ['int', '100003']}, state
        if result['allocation']['16'] is not None:
            assert result['allocation']['16'] == state
    for entries in candidate['traces'] + candidate['allocation_traces']:
        names = [entry[0] for entry in entries]
        begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
        assert any(name in ('_UNARY_NEGATIVE', '_UNARY_NEGATIVE_FLOAT_INPLACE')
                   for name in names[begin:end]), names
    assert all('_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
               for entries in noopt['traces'])
    print('OK    virtual unary negation preserves results, ownership and errors')


if __name__ == '__main__':
    main()
