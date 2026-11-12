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

WARM = 7000

def arithmetic(a, b):
    return a + b

def arithmetic_local(a, b):
    first = a + 0.5
    return first + b

from types import FunctionType

class Source:
    def __init__(self, values):
        self.values = values
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

def trace(fn):
    entries = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            entries.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return entries

def loop(n, fn, values, active):
    entries = []
    was_active = False
    result = None
    for index in range(n):
        b = values.pop()
        was_active = active()
        result = fn(1.25, b)
        if index == WARM - 1:
            entries = trace(loop)
    return result, entries, was_active

loop = FunctionType(loop.__code__, {'trace': trace, 'WARM': WARM})
loop.__globals__['loop'] = loop

def state(error, fn):
    result = None
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is fn.__code__:
            inst = next(i for i in dis.get_instructions(fn) if i.offset == tb.tb_lasti)
            frame = tb.tb_frame
            result = [inst.opname, inst.positions.lineno, inst.positions.col_offset,
                      inst.positions.end_col_offset, frame.f_locals['a'].hex(),
                      str(frame.f_locals['b']),
                      frame.f_locals['first'].hex() if 'first' in frame.f_locals else None]
        tb = tb.tb_next
    return result

small = int('10000000000')
before = sys.getrefcount(small)
value, entries, active = loop(WARM, arithmetic, [small] * WARM, sys._jit.is_active)
results = [value.hex(), sys.getrefcount(small) - before]
fault_active = []
fault_traces = []
for integer in (2**70, 10**300, 10**400):
    before = sys.getrefcount(integer)
    source = Source([integer] + [small] * (WARM + 15))
    try:
        result, fault_entries, was_active = loop(WARM + 16, arithmetic_local, source, sys._jit.is_active)
    except OverflowError as error:
        tb = error.__traceback__
        while tb:
            if tb.tb_frame.f_code is loop.__code__:
                fault_active.append(tb.tb_frame.f_locals['was_active'])
                fault_traces.append(tb.tb_frame.f_locals['entries'])
            tb = tb.tb_next
        results.append(['OverflowError', str(error), state(error, arithmetic_local)])
    else:
        fault_active.append(was_active)
        fault_traces.append(fault_entries)
        results.append(['value', result.hex()])
    results.append([sys.getrefcount(integer) - before, source.calls, len(source.values)])

allocation_active = []

class Arm:
    def __init__(self, failures):
        self.failures = failures
    def __del__(self):
        allocation_active.append(sys._getframe(1).f_locals['was_active'])
        _testcapi.set_nomemory(0, self.failures)

def allocating(n, values, active):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = values.pop()
        held[index] = arithmetic(1.25, small)
        if index == WARM - 1:
            entries = trace(allocating)
    return held, entries, was_active

allocating = FunctionType(allocating.__code__,
    {'trace': trace, 'WARM': WARM, 'arithmetic': arithmetic, 'small': small})
allocating.__globals__['allocating'] = allocating

allocation = {}
for failures in (1, 16):
    try:
        held, alloc_entries, alloc_active = allocating(WARM + 18,
            [None, Arm(failures)] + [None] * (WARM + 16), sys._jit.is_active)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        allocation[failures] = ['MemoryError', state(error, arithmetic)]
    else:
        allocation[failures] = ['success', held[-1].hex()]
print(json.dumps({'results': results, 'trace': entries, 'active': active,
                  'allocation': allocation, 'fault_active': fault_active,
                  'allocation_active': allocation_active, 'fault_traces': fault_traces}))
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
    assert candidate['results'] == reference['results'] == noopt['results'], (candidate, reference, noopt)
    results = candidate['results']
    assert results[1] == 0 and results[3] == results[5] == results[7] == [0, 7016, 0], results
    assert results[2] == ['value', (1.75 + 2**70).hex()], results
    assert results[4] == ['value', (1.75 + 10**300).hex()], results
    assert results[6][0] == 'OverflowError', results
    assert results[6][2] == ['BINARY_OP', 15, 11, 20, (1.25).hex(), str(10**400), (1.75).hex()], results
    for result in (candidate, reference, noopt):
        for count in ('1', '16'):
            failure = result['allocation'][count]
            assert failure[0] == 'MemoryError', failure
            if count == '1':
                assert failure[1] is not None, failure
            if failure[1] is not None:
                assert failure[1] == ['BINARY_OP', 11, 11, 16, (1.25).hex(), str(10000000000), None], failure
    assert candidate['active'] and candidate['fault_active'] == [True] * 3, candidate
    assert candidate['allocation_active'] == [True] * 2, candidate
    names = [entry[0] for entry in candidate['trace']]
    begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
    assert '_BINARY_OP_ADD_FLOAT_INT' in names[begin:end], names
    assert '_MATERIALIZE_VIRTUAL_FRAME' in names, names
    for entries in candidate['fault_traces']:
        names = [entry[0] for entry in entries]
        begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
        assert '_BINARY_OP_ADD_FLOAT_INT' in names[begin:end], names
    assert '_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in noopt['trace']]
    print('OK    virtual mixed addition preserves results, errors and ownership')


if __name__ == '__main__':
    main()
