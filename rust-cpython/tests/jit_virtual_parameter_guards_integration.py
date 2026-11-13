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

def replace_two(a, b, c):
    first = c + c
    a = first + c
    b = a + c
    a = b + c
    return a / c

class Scale:
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value
    def replace(self, a, b, c):
        first = b + self.value
        a = first + b
        return a / c
    def replace_two(self, a, b, c):
        first = c + self.value
        a = first + c
        b = a + c
        a = b + c
        return a / c

results = []
traces = []
active = []
multi_results = []
multi_traces = []
multi_active = []
for fn in (replace_two, Scale(1.0).replace_two):
    for a, b in ((1.25, 2**100), (2**100, 1.25), (True, 1.25),
                 (1.25, True), (None, 1.25), (1.25, None),
                 (2**100, 2**100)):
        caller = fresh(loop)
        source = Source([(a, b, 1.0)] + [(1.25, 2.5, 1.0)] * (COUNT - 1))
        value, entries, was_active = caller(COUNT, fn, source, sys._jit.is_active)
        multi_results.append([encode(value), source.calls, len(source.values)])
        multi_traces.append(entries)
        multi_active.append(was_active)
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

class ReleasedInt(int):
    __del__ = Released.__del__

class ReleasedFloat(float):
    __del__ = Released.__del__

caller = fresh(released_loop)
source = Source([Released(), ReleasedInt(123), ReleasedFloat(1.25), Released()] + [1.25] * WARM)
try:
    raise ValueError('outer')
except ValueError:
    value, entries, was_active = caller(WARM + 4, replace, source, sys._jit.is_active)
results.append([encode(value), source.calls, len(source.values), len(events)])
traces.append(entries)

multi_events = []
class MultiReleased:
    def __del__(self):
        frame = sys._getframe(1)
        inst = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        multi_events.append([frame.f_code.co_name, inst.opname, inst.argval,
            {k: encode(v) for k,v in frame.f_locals.items()}])

class MultiInt(int):
    __del__ = MultiReleased.__del__

class MultiFloat(float):
    __del__ = MultiReleased.__del__

def multi_released_loop(n, fn, left, right, active):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        value = fn(left.pop(), right.pop(), 1.0)
        if index == WARM - 1:
            entries = trace(multi_released_loop)
    return value, entries, was_active

for fn in (replace_two, Scale(1.0).replace_two):
    for cls in (MultiReleased, MultiInt, MultiFloat):
        for position in (0, 1):
            old = cls() if cls is MultiReleased else cls(19)
            left = Source(([old] if position == 0 else [1.25]) + [1.25] * WARM)
            right = Source(([old] if position == 1 else [2.5]) + [2.5] * WARM)
            del old
            value, entries, was_active = fresh(multi_released_loop)(
                WARM + 1, fn, left, right, sys._jit.is_active)
            multi_traces.append(entries)
            multi_active.append(was_active)

def fail_before(a, b, c):
    first = 1.0 / c
    a = first + 1.0
    b = a + 1.0
    return b

def fail_between(a, b, c):
    first = 1.0 + 1.0
    a = first + 1.0
    second = a / c
    b = second + 1.0
    return b

multi_errors = []
for fn in (fail_before, fail_between, replace_two):
    for template in (loop, generator):
        caller = fresh(template)
        source = Source([(1.25, 2**100, 0.0)] + [(1.25, 2.5, 1.0)] * (COUNT - 1))
        try:
            result = caller(COUNT, fn, source, sys._jit.is_active)
            if template is generator:
                list(result)
        except ZeroDivisionError as error:
            multi_errors.append(state(error, fn))
            tb = error.__traceback__
            while tb:
                if tb.tb_frame.f_code is caller.__code__:
                    multi_traces.append(tb.tb_frame.f_locals['entries'])
                    multi_active.append(tb.tb_frame.f_locals['was_active'])
                tb = tb.tb_next
        else:
            raise AssertionError('multi division did not fail')

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

def multi_allocating(a, b, c):
    a = ~c
    b = ~c
    return ~c

def multi_allocation_loop(n, fn, source, active, c):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(1.25, 2.5, c)
        if index == WARM - 1:
            entries = trace(multi_allocation_loop)
    return held

multi_allocation = []
multi_allocation_traces = []
multi_allocation_active = []
class MultiArm(Arm):
    def __del__(self):
        frame = sys._getframe(1)
        multi_allocation_active.append(frame.f_locals['was_active'])
        multi_allocation_traces.append(frame.f_locals['entries'])
        _testcapi.set_nomemory(self.skip, self.skip + self.failures)

for skip in (0, 1, 2):
    caller = fresh(multi_allocation_loop)
    try:
        caller(COUNT + 2, multi_allocating, Source([None, MultiArm(skip, 1)] + [None] * COUNT),
               sys._jit.is_active, 2**70)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        multi_allocation.append(state(error, multi_allocating))
    else:
        _testcapi.remove_mem_hooks()
        raise AssertionError('multi allocation did not fail')

multi_observed = []
def multi_profiler(frame, event, arg):
    if frame.f_code is replace_two.__code__ and event == 'return':
        multi_observed.append([encode(arg), {k: encode(v) for k,v in frame.f_locals.items()}])
sys.setprofile(multi_profiler)
fresh(loop)(8, replace_two, Source([(1.25, 2.5, 1.0)] * 8), sys._jit.is_active)
sys.setprofile(None)

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
    'observed': observed, 'multi_results': multi_results,
    'multi_traces': multi_traces, 'multi_active': multi_active,
    'multi_events': multi_events, 'multi_errors': multi_errors,
    'multi_allocation': multi_allocation, 'multi_observed': multi_observed,
    'multi_allocation_traces': multi_allocation_traces,
    'multi_allocation_active': multi_allocation_active}))
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
    for key in ('results', 'errors', 'events', 'observed', 'multi_results', 'multi_events', 'multi_errors',
                'multi_allocation', 'multi_observed'):
        assert candidate[key] == reference[key] == noopt[key], (key, candidate[key], reference[key], noopt[key])
    assert candidate['multi_results'] == [[['float', (5.0).hex()], 7016, 0]] * 14
    assert all(candidate['multi_active'])
    assert len(candidate['multi_events']) == 12
    for index, event in enumerate(candidate['multi_events']):
        assert event[:3] == ['replace_two', 'STORE_FAST', 'a' if index % 2 == 0 else 'b'], event
        assert event[3]['a'] == ['float', (3.0).hex()], event
        if index % 2:
            assert event[3]['b'] == ['float', (4.0).hex()], event
    for index, error in enumerate(candidate['multi_errors']):
        assert error[0] == 'BINARY_OP', error
        assert error[3]['a'] == ['float', (1.25 if index < 2 else 3.0 if index < 4 else 0.0).hex()], error
        assert error[3]['b'] == (['int', str(2**100)] if index < 4 else ['float', (0.0).hex()]), error
    for index, error in enumerate(candidate['multi_allocation']):
        assert error[0] == 'UNARY_INVERT', error
        assert error[3]['a'] == (['float', (1.25).hex()] if index == 0 else ['int', str(-(2**70)-1)]), error
        assert error[3]['b'] == (['float', (2.5).hex()] if index < 2 else ['int', str(-(2**70)-1)]), error
    assert len(candidate['multi_observed']) == 8
    assert candidate['multi_allocation_active'] == [True] * 3
    for entries in candidate['multi_allocation_traces']:
        names = [entry[0] for entry in entries]
        begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
        assert names[begin:end].count('_STORE_VIRTUAL_ARGUMENT_NUMERIC') == 2, names
    for entries in candidate['multi_traces'][:26]:
        names = [entry[0] for entry in entries]
        begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
        assert names[begin:end].count('_STORE_VIRTUAL_ARGUMENT_NUMERIC') >= 2, names
    for entries in noopt['multi_traces']:
        assert '_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
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
