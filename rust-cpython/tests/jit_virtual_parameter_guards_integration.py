import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import _testcapi
import dis
import gc
import json
import sys
from types import FunctionType

WARM = 7000
COUNT = WARM + 16

def trace(fn):
    entries = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            entries.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return entries

def fresh(template):
    namespace = {'trace': trace, 'WARM': WARM}
    fn = FunctionType(template.__code__.replace(), namespace)
    namespace[template.__name__] = fn
    return fn

def encode(value):
    return [type(value).__name__, value.hex() if type(value) is float else
            str(value) if type(value) in (int, bool) else type(value).__name__]

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
        a, b, c = source.pop()
        was_active = active()
        value = fn(a, b, c)
        if index == WARM - 1:
            entries = trace(loop)
    return value, entries, was_active

def generator(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        a, b, c = source.pop()
        was_active = active()
        value = fn(a, b, c)
        if index == WARM - 1:
            entries = trace(generator)
        yield value

def released_loop(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        value = fn(source.pop(), 2.5, 1.0)
        if index == WARM - 1:
            entries = trace(released_loop)
    return value, entries, was_active

def replace(a, b, c):
    first = b + b
    a = first + b
    return a / c

def allocating(a, b, c):
    first = c + b
    a = ~b
    return ~a

class Scale:
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value
    def replace(self, a, b, c):
        first = b + self.value
        a = first + b
        return a / c

results = []
traces = []
active = []
for fn in (replace, Scale(2.5).replace):
    for old in (1.25, int(str(2**100)), float('nan'), True, None):
        caller = fresh(loop)
        before = sys.getrefcount(old)
        source = Source([(old, 2.5, 1.0)] + [(1.25, 2.5, 1.0)] * (COUNT - 1))
        value, entries, was_active = caller(COUNT, fn, source, sys._jit.is_active)
        results.append([encode(value), source.calls, len(source.values), sys.getrefcount(old) - before])
        traces.append(entries)
        active.append(was_active)

def state(error, fn):
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is fn.__code__:
            inst = next(i for i in dis.get_instructions(fn) if i.offset == tb.tb_lasti)
            return [inst.opname, inst.positions.col_offset, inst.positions.end_col_offset,
                    {k: encode(v) for k,v in tb.tb_frame.f_locals.items()}]
        tb = tb.tb_next
    return None

def capture_caller(error, caller):
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is caller.__code__:
            active.append(tb.tb_frame.f_locals['was_active'])
            traces.append(tb.tb_frame.f_locals['entries'])
        tb = tb.tb_next

errors = []
for template in (loop, generator):
    caller = fresh(template)
    source = Source([(1.25, 2.5, 0.0)] + [(1.25, 2.5, 1.0)] * (COUNT - 1))
    try:
        raise ValueError('outer')
    except ValueError:
        try:
            if template is loop:
                caller(COUNT, replace, source, sys._jit.is_active)
            else:
                list(caller(COUNT, replace, source, sys._jit.is_active))
        except ZeroDivisionError as error:
            errors.append([type(error).__name__, str(error), str(error.__context__),
                state(error, replace), source.calls, len(source.values)])
            capture_caller(error, caller)
        else:
            raise AssertionError('division did not fail')

events = []
class Released:
    def __del__(self):
        frame = sys._getframe(1)
        inst = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        events.append([frame.f_code.co_name, inst.opname, str(sys.exception()),
            {k: encode(v) for k,v in frame.f_locals.items()}])
        frame.f_locals['a'] = 11.0
        gc.collect()

caller = fresh(released_loop)
source = Source([Released() for _ in range(4)] + [1.25] * WARM)
try:
    raise ValueError('outer')
except ValueError:
    value, entries, was_active = caller(WARM + 4, replace, source, sys._jit.is_active)
results.append([encode(value), source.calls, len(source.values), len(events)])
traces.append(entries)

allocation_traces = []
allocation_active = []
class Arm:
    def __init__(self, skip, failures):
        self.skip, self.failures = skip, failures
    def __del__(self):
        frame = sys._getframe(1)
        allocation_active.append(frame.f_locals['was_active'])
        allocation_traces.append(frame.f_locals['entries'])
        _testcapi.set_nomemory(self.skip, self.skip + self.failures)

def allocation_loop(n, fn, source, active, b):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(1.25, b, 1.25)
        if index == WARM - 1:
            entries = trace(allocation_loop)
    return held

allocation = []
for skip, failures in ((0, 1), (1, 1), (1, 16)):
    caller = fresh(allocation_loop)
    try:
        caller(COUNT + 2, allocating, Source([None, Arm(skip, failures)] + [None] * COUNT),
               sys._jit.is_active, 2**70)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        allocation.append(state(error, allocating))
    else:
        _testcapi.remove_mem_hooks()
        raise AssertionError('allocation did not fail')

observed = []
def profiler(frame, event, arg):
    if frame.f_code is replace.__code__ and event == 'return':
        observed.append([encode(arg), {k: encode(v) for k,v in frame.f_locals.items()}])
sys.setprofile(profiler)
fresh(loop)(8, replace, Source([(1.25, 2.5, 1.0)] * 8), sys._jit.is_active)
sys.setprofile(None)
print(json.dumps({'results': results, 'errors': errors, 'events': events,
    'active': active, 'traces': traces, 'allocation': allocation,
    'allocation_traces': allocation_traces, 'allocation_active': allocation_active,
    'observed': observed}))
'''


def run(jit, optimize=True):
    result = subprocess.run([sys.executable, '-s', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, (jit, optimize, result.returncode, result.stderr)
    return json.loads(result.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    for key in ('results', 'errors', 'events', 'observed'):
        assert candidate[key] == reference[key] == noopt[key], (key, candidate[key], reference[key], noopt[key])
    assert candidate['results'][:10] == [[['float', (7.5).hex()], 7016, 0, 0]] * 10
    assert candidate['results'][10] == [['float', (11.0).hex()], 7004, 0, 4]
    locals_state = {'a': ['float', (7.5).hex()], 'b': ['float', (2.5).hex()],
        'c': ['float', (0.0).hex()], 'first': ['float', (5.0).hex()]}
    assert candidate['errors'] == [['ZeroDivisionError', 'division by zero', 'outer',
        ['BINARY_OP', 11, 16, locals_state], 7016, 0]] * 2
    for event in candidate['events']:
        assert event[:3] == ['replace', 'STORE_FAST', 'outer'], event
        assert event[3] == {'a': ['float', (7.5).hex()], 'b': ['float', (2.5).hex()],
            'c': ['float', (1.0).hex()], 'first': ['float', (5.0).hex()]}, event
    for result in (candidate, reference, noopt):
        before = result['allocation'][0]
        after = result['allocation'][1]
        assert before == ['UNARY_INVERT', 8, 10, {'a': ['float', (1.25).hex()],
            'b': ['int', str(2**70)], 'c': ['float', (1.25).hex()],
            'first': ['float', (float(2**70) + 1.25).hex()]}], before
        assert after == ['UNARY_INVERT', 11, 13, {'a': ['int', str(-(2**70) - 1)],
            'b': ['int', str(2**70)], 'c': ['float', (1.25).hex()],
            'first': ['float', (float(2**70) + 1.25).hex()]}], after
        assert result['allocation'][2] in (None, after)
    assert len(candidate['active']) == 12 and all(candidate['active']), candidate['active']
    assert candidate['allocation_active'] == [True] * 3
    for entries in candidate['traces'] + candidate['allocation_traces']:
        names = [entry[0] for entry in entries]
        begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
        assert names[begin:end].count('_STORE_VIRTUAL_ARGUMENT_NUMERIC') == 1, names
        assert '_SWAP_VIRTUAL_ARGUMENT' not in names[begin:end], names
    for entries in noopt['traces'] + noopt['allocation_traces']:
        assert '_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
    assert candidate['observed'] == [[['float', (7.5).hex()],
        {'a': ['float', (7.5).hex()], 'b': ['float', (2.5).hex()],
         'c': ['float', (1.0).hex()], 'first': ['float', (5.0).hex()]}]] * 8
    print('OK    numeric parameter entry guards preserve refusal, stores and errors')


if __name__ == '__main__':
    main()
