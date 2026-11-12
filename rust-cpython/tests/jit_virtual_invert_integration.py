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

def fresh(template):
    namespace = {'trace': trace, 'WARM': WARM}
    fn = FunctionType(template.__code__.replace(), namespace)
    namespace[template.__name__] = fn
    return fn

def compact(a, b):
    value = a + b
    other = value
    return ~other

def chained(a, b):
    value = a + 1
    inverted = ~value
    return inverted * b

def exact(a, b):
    value = b + a
    return ~a

def unknown(a, b):
    return ~a

def state(error, fn):
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is fn.__code__:
            inst = next(i for i in dis.get_instructions(fn) if i.offset == tb.tb_lasti)
            return [inst.opname, inst.positions.col_offset, inst.positions.end_col_offset,
                    {k: encode(v) for k, v in tb.tb_frame.f_locals.items()}]
        tb = tb.tb_next
    return None

events = []
class Multiply:
    def __rmul__(self, other):
        frame = sys._getframe(1)
        inst = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        events.append(['multiply', frame.f_code.co_name, inst.opname,
                       inst.positions.col_offset, inst.positions.end_col_offset,
                       {k: encode(v) for k, v in frame.f_locals.items()}, str(other)])
        return other * 3

class Callback:
    def __invert__(self):
        events.append('invert')
        return 47
    def __add__(self, other):
        events.append('add')
        return self

class Source:
    def __init__(self, values):
        self.values = values
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

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

def encode(value):
    return [type(value).__name__, value.hex() if type(value) is float else
            str(value) if type(value) in (int, bool) else type(value).__name__]

results = []
traces = []
active = []
for fn, warm, samples in (
    (compact, (100000, 3), [(2**30 - 2, 1), (-2**30 + 1, 0), (Callback(), 1)]),
    (chained, (100000, 3), [(2**30 - 2, 1), (-2**30 + 1, 1),
                           (2**30 - 2, Multiply())]),
    (exact, (2**70, 1.25), [(2**100, 1.25), (-2**100, 1.25)]),
    (unknown, (100000, 1), [(Callback(), 1)]),
):
    caller = fresh(loop)
    for a, b in [warm] + samples:
        before = [sys.getrefcount(a), sys.getrefcount(b)]
        source = Source([(a, b)] + [warm] * (WARM + 15))
        value, entries, was_active = caller(WARM + 16, fn, source, sys._jit.is_active)
        results.append([encode(value), source.calls, len(source.values),
                        [sys.getrefcount(a) - before[0], sys.getrefcount(b) - before[1]]])
        active.append(was_active)
        if (a, b) == warm:
            traces.append(entries)

caller = fresh(loop)
source = Source([(1.25, 2.5)] + [(100000, 3)] * (WARM + 15))
try:
    caller(WARM + 16, compact, source, sys._jit.is_active)
except TypeError as error:
    refusal = [type(error).__name__, str(error), state(error, compact),
               source.calls, len(source.values)]
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is caller.__code__:
            active.append(tb.tb_frame.f_locals['was_active'])
        tb = tb.tb_next
else:
    raise AssertionError('float inversion did not fail')

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

def allocating(n, fn, source, active, a, b):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(a, b)
        if index == WARM - 1:
            entries = trace(allocating)
    return held, entries, was_active

allocation = {}
for failures in (1, 16):
    caller = fresh(allocating)
    try:
        caller(WARM + 18, exact, [None, Arm(failures)] + [None] * (WARM + 16),
               sys._jit.is_active, 2**70, 1.25)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        allocation[failures] = state(error, exact)
    else:
        _testcapi.remove_mem_hooks()
        raise AssertionError('allocation did not fail')
print(json.dumps({'results': results, 'traces': traces, 'active': active, 'events': events,
                  'allocation': allocation, 'allocation_active': allocation_active,
                  'allocation_traces': allocation_traces, 'refusal': refusal}))
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
    assert candidate['events'] == reference['events'] == noopt['events']
    assert candidate['events'] == ['add', 'invert', ['multiply', 'chained', 'BINARY_OP', 11, 23,
        {'a': ['int', str(2**30 - 2)], 'b': ['Multiply', 'Multiply'],
         'value': ['int', str(2**30 - 1)], 'inverted': ['int', str(-2**30)]}, str(-2**30)], 'invert']
    expected = [-100004, -2**30, 2**30 - 2, 47,
                -300006, -2**30, 2**30 - 3, -3 * 2**30,
                -(2**70) - 1, -(2**100) - 1, 2**100 - 1, -100001, 47]
    assert [row[0] for row in candidate['results']] == [['int', str(v)] for v in expected]
    assert candidate['refusal'] == reference['refusal'] == noopt['refusal']
    assert candidate['refusal'][0] == 'TypeError'
    assert candidate['refusal'][2] == ['UNARY_INVERT', 11, 17,
        {'a': ['float', (1.25).hex()], 'b': ['float', (2.5).hex()],
         'value': ['float', (3.75).hex()], 'other': ['float', (3.75).hex()]}]
    assert candidate['refusal'][3:] == [7016, 0]
    assert all(row[1:] == [7016, 0, [0, 0]] for row in candidate['results'])
    assert all(candidate['active']) and candidate['allocation_active'] == [True, True]
    for result in (candidate, reference, noopt):
        state = result['allocation']['1']
        assert state == ['UNARY_INVERT', 11, 13, {'a': ['int', str(2**70)],
                         'b': ['float', (1.25).hex()],
                         'value': ['float', (float(2**70) + 1.25).hex()]}], state
        if result['allocation']['16'] is not None:
            assert result['allocation']['16'] == state
    for entries in candidate['traces'][:3] + candidate['allocation_traces']:
        names = [entry[0] for entry in entries]
        begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
        assert '_MATERIALIZE_VIRTUAL_FRAME' in names, names
        assert '_UNARY_INVERT' in names[begin:end], names
    chain = [entry[0] for entry in candidate['traces'][1]]
    assert '_GUARD_NOS_INT' in chain[chain.index('_UNARY_INVERT') + 1:], chain
    assert '_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in candidate['traces'][3]]
    assert all('_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
               for entries in noopt['traces'])
    print('OK    virtual inversion preserves results, ownership and errors')


if __name__ == '__main__':
    main()
