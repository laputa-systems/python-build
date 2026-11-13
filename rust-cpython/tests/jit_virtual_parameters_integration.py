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

def state(error, fn):
    code = getattr(fn, '__func__', fn).__code__
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is code:
            inst = next(i for i in dis.get_instructions(code) if i.offset == tb.tb_lasti)
            return [inst.opname, inst.positions.col_offset, inst.positions.end_col_offset,
                    {name: encode(value) for name, value in tb.tb_frame.f_locals.items()}]
        tb = tb.tb_next
    return None

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
        yield value, entries, was_active

def floats(a, b, c):
    alias = a + b
    a = alias * a
    b = b + a
    a = a + b
    return a + alias

def integers(a, b, c):
    alias = a + b
    a = alias - b
    b = a * b
    a = a + b
    return a - alias

def complement(a, b, c):
    value = a + b
    a = ~value
    return a * b

def changing(a, b, c):
    value = b + a
    a = value * b
    return a + b

def failure(a, b, c):
    a = b + a
    b = a * b
    return a / c

def allocating(a, b, c):
    value = b + a
    a = ~a
    return ~a

class Scale:
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value
    def calculate(self, a, b, c):
        a = a + b
        b = a * self.value
        return a + b
    def rebind(self, a, b, c):
        value = a + b
        self = value
        return self * a

results = {}
traces = {}
active = []
for key, fn, warm, samples in (
    ('floats', floats, (1.25, 2.5, None), [(1.25, 2.5, None), (-0.0, 0.0, None),
        (float('inf'), 2.5, None), (float('nan'), 2.5, None)]),
    ('integers', integers, (10000, 3, None), [(10000, 3, None), (2**30 - 2, 1, None),
        (-2**29, 1, None)]),
    ('complement', complement, (10000, 3, None), [(10000, 3, None), (2**30 - 2, 1, None)]),
    ('changing', changing, (2**70, 1.25, None), [(2**70, 1.25, None), (-2**100, 1.25, None)]),
    ('bound', Scale(2.5).calculate, (1.25, 2.5, None), [(1.25, 2.5, None)]),
    ('receiver', Scale(2.5).rebind, (1.25, 2.5, None), [(1.25, 2.5, None)]),
):
    caller = fresh(loop)
    for index, sample in enumerate(samples):
        a, b, c = sample
        before = [sys.getrefcount(a), sys.getrefcount(b)]
        source = Source([sample] + [warm] * (COUNT - 1))
        value, entries, was_active = caller(COUNT, fn, source, sys._jit.is_active)
        results[f'{key}-{index}'] = [encode(value), source.calls, len(source.values),
            [sys.getrefcount(a) - before[0], sys.getrefcount(b) - before[1]]]
        active.append(was_active)
        traces[f'{key}-{index}'] = entries

errors = {}
for key, warm, sample in (
    ('float-error', (1.25, 2.5, 1.0), (1.25, 2.5, 0.0)),
    ('mixed-error', (2**70, 1.25, 1.0), (2**70, 1.25, 0.0)),
):
    caller = fresh(loop)
    source = Source([sample] + [warm] * (COUNT - 1))
    try:
        raise ValueError('outer')
    except ValueError:
        try:
            caller(COUNT, failure, source, sys._jit.is_active)
        except ZeroDivisionError as error:
            errors[key] = [type(error).__name__, str(error), str(error.__context__),
                           state(error, failure), source.calls, len(source.values)]
            tb = error.__traceback__
            while tb:
                if tb.tb_frame.f_code is caller.__code__:
                    active.append(tb.tb_frame.f_locals['was_active'])
                    traces[key] = tb.tb_frame.f_locals['entries']
                tb = tb.tb_next
        else:
            raise AssertionError('division did not fail')

caller = fresh(generator)
source = Source([(1.25, 2.5, 0.0)] + [(1.25, 2.5, 1.0)] * (COUNT - 1))
try:
    list(caller(COUNT, failure, source, sys._jit.is_active))
except ZeroDivisionError as error:
    errors['generator'] = [type(error).__name__, str(error), state(error, failure),
                           source.calls, len(source.values)]
    tb = error.__traceback__
    while tb:
        if tb.tb_frame.f_code is caller.__code__:
            active.append(tb.tb_frame.f_locals['was_active'])
            traces['generator'] = tb.tb_frame.f_locals['entries']
        tb = tb.tb_next
else:
    raise AssertionError('generator division did not fail')

events = []
class Divide:
    def __rtruediv__(self, other):
        frame = sys._getframe(1)
        inst = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        events.append(['divide', frame.f_code.co_name, inst.opname,
            {name: encode(value) for name, value in frame.f_locals.items()}, encode(other)])
        return 47.0

caller = fresh(loop)
source = Source([(1.25, 2.5, Divide())] + [(1.25, 2.5, 1.0)] * (COUNT - 1))
value, entries, was_active = caller(COUNT, failure, source, sys._jit.is_active)
results['callback'] = [encode(value), source.calls, len(source.values)]
active.append(was_active)
traces['callback'] = entries

def unproved(a, b):
    a = b + b
    return a * b

class Released:
    def __del__(self):
        frame = sys._getframe(1)
        events.append(['released', frame.f_code.co_name,
            {name: encode(value) for name, value in frame.f_locals.items()}])

def release_loop(n, fn, source, active, b):
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        value = fn(source.pop(), b)
        if index == WARM - 1:
            entries = trace(release_loop)
    return value, entries, was_active

caller = fresh(release_loop)
source = Source([Released()] + [1.25] * (COUNT - 1))
value, entries, was_active = caller(COUNT, unproved, source, sys._jit.is_active, 2.5)
results['unproved'] = [encode(value), source.calls, len(source.values)]
traces['unproved'] = entries
active.append(was_active)

allocation_active = []
allocation_traces = []
class Arm:
    def __init__(self, failures):
        self.failures = failures
    def __del__(self):
        frame = sys._getframe(1)
        allocation_active.append(frame.f_locals['was_active'])
        allocation_traces.append(frame.f_locals['entries'])
        _testcapi.set_nomemory(1, 1 + self.failures)

def allocation_loop(n, fn, source, active, a, b):
    held = [None] * n
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(a, b, None)
        if index == WARM - 1:
            entries = trace(allocation_loop)
    return held

allocation = {}
for failures in (1, 16):
    caller = fresh(allocation_loop)
    try:
        caller(COUNT + 2, allocating, Source([None, Arm(failures)] + [None] * COUNT),
               sys._jit.is_active, 2**70, 1.25)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        allocation[failures] = state(error, allocating)
    else:
        _testcapi.remove_mem_hooks()
        raise AssertionError('allocation did not fail')

observed = {}
for kind in ('profile', 'trace', 'monitor'):
    caller = fresh(loop)
    observations = []
    def profiler(frame, event, arg):
        if event == 'return' and frame.f_code is floats.__code__:
            observations.append([encode(arg), {k: encode(v) for k,v in frame.f_locals.items()}])
    def tracer(frame, event, arg):
        profiler(frame, event, arg)
        return tracer
    def monitor(code, offset, retval):
        if code is floats.__code__:
            observations.append(encode(retval))
    if kind == 'profile':
        sys.setprofile(profiler)
    elif kind == 'trace':
        sys.settrace(tracer)
    else:
        sys.monitoring.use_tool_id(5, 'parameters')
        sys.monitoring.register_callback(5, sys.monitoring.events.PY_RETURN, monitor)
        sys.monitoring.set_events(5, sys.monitoring.events.PY_RETURN)
    caller(8, floats, Source([(1.25, 2.5, None)] * 8), sys._jit.is_active)
    sys.setprofile(None)
    sys.settrace(None)
    if kind == 'monitor':
        sys.monitoring.set_events(5, 0)
        sys.monitoring.free_tool_id(5)
    observed[kind] = observations
print(json.dumps({'results': results, 'errors': errors, 'events': events,
    'active': active, 'traces': traces, 'allocation': allocation,
    'allocation_active': allocation_active, 'allocation_traces': allocation_traces,
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
        assert candidate[key] == reference[key] == noopt[key], key
    expected = {
        'floats-0': ['float', (15.625).hex()], 'floats-1': ['float', (0.0).hex()],
        'floats-2': ['float', 'inf'], 'floats-3': ['float', 'nan'],
        'integers-0': ['int', '29997'], 'integers-1': ['int', str(2**30 - 3)],
        'integers-2': ['int', str(-2**29 - 1)],
        'complement-0': ['int', '-30012'], 'complement-1': ['int', str(-2**30)],
        'changing-0': ['float', ((1.25 + 2**70) * 1.25 + 1.25).hex()],
        'changing-1': ['float', ((1.25 - 2**100) * 1.25 + 1.25).hex()],
        'bound-0': ['float', (13.125).hex()],
        'receiver-0': ['float', (4.6875).hex()],
    }
    for key, value in expected.items():
        assert candidate['results'][key] == [value, 7016, 0, [0, 0]], (key, candidate['results'][key])
    assert candidate['results']['callback'] == [['float', (47.0).hex()], 7016, 0]
    assert candidate['results']['unproved'] == [['float', (12.5).hex()], 7016, 0]
    local_state = {'a': ['float', (3.75).hex()], 'b': ['float', (9.375).hex()],
                   'c': ['float', (0.0).hex()]}
    assert candidate['errors']['float-error'] == ['ZeroDivisionError', 'division by zero', 'outer',
        ['BINARY_OP', 11, 16, local_state], 7016, 0]
    assert candidate['errors']['generator'] == ['ZeroDivisionError', 'division by zero',
        ['BINARY_OP', 11, 16, local_state], 7016, 0]
    assert candidate['errors']['mixed-error'] == ['ZeroDivisionError', 'division by zero', 'outer',
        ['BINARY_OP', 11, 16, {'a': ['float', float(2**70).hex()],
            'b': ['float', (float(2**70) * 1.25).hex()], 'c': ['float', (0.0).hex()]}], 7016, 0]
    assert candidate['events'] == [
        ['divide', 'failure', 'BINARY_OP',
         {'a': ['float', (3.75).hex()], 'b': ['float', (9.375).hex()], 'c': ['Divide', 'Divide']},
         ['float', (3.75).hex()]],
        ['released', 'unproved', {'a': ['float', (5.0).hex()], 'b': ['float', (2.5).hex()]}]], candidate['events']
    for result in (candidate, reference, noopt):
        fault = result['allocation']['1']
        assert fault == ['UNARY_INVERT', 11, 13, {'a': ['int', str(-(2**70) - 1)],
            'b': ['float', (1.25).hex()], 'c': ['NoneType', 'NoneType'],
            'value': ['float', (float(2**70) + 1.25).hex()]}], fault
        assert result['allocation']['16'] in (None, fault)
    assert len(candidate['active']) == 18 and all(candidate['active']), candidate['active']
    assert candidate['allocation_active'] == [True, True]
    float_store = '_STORE_VIRTUAL_ARGUMENT_FLOAT'
    int_store = '_STORE_VIRTUAL_ARGUMENT_INT'
    numeric_store = '_STORE_VIRTUAL_ARGUMENT_NUMERIC'
    stores = {'floats': [float_store] * 3, 'integers': [int_store] * 3,
        'complement': [int_store], 'changing': [int_store], 'bound': [float_store] * 2,
        'float-error': [float_store] * 2, 'mixed-error': [int_store, float_store],
        'generator': [float_store] * 2, 'callback': [float_store] * 2,
        'unproved': [numeric_store]}
    for key, entries in candidate['traces'].items():
        names = [entry[0] for entry in entries]
        if key == 'receiver-0':
            assert '_BEGIN_VIRTUAL_CALL' not in names, names
        else:
            assert {'_BEGIN_VIRTUAL_CALL', '_RETURN_VIRTUAL_CALL'} <= set(names), (key, names)
            begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
            kind = key.rsplit('-', 1)[0] if key[-1].isdigit() else key
            assert [name for name in names[begin:end] if name in (float_store, int_store, numeric_store)] == stores[kind], (key, names)
            assert '_SWAP_VIRTUAL_ARGUMENT' not in names[begin:end], (key, names)
            assert '_MATERIALIZE_VIRTUAL_FRAME' in names, key
    for entries in candidate['allocation_traces']:
        names = [entry[0] for entry in entries]
        assert [name for name in names if name in (float_store, int_store)] == [int_store], names
        assert '_SWAP_VIRTUAL_ARGUMENT' not in names, names
    names = [entry[0] for entry in candidate['traces']['complement-0']]
    assert '_GUARD_NOS_INT' in names[names.index(int_store) + 1:], names
    for entries in noopt['traces'].values():
        assert '_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
    observed = candidate['observed']
    observation = [['float', (15.625).hex()], {'a': ['float', (11.875).hex()],
        'b': ['float', (7.1875).hex()], 'c': ['NoneType', 'NoneType'],
        'alias': ['float', (3.75).hex()]}]
    assert observed['profile'] == observed['trace'] == [observation] * 8
    assert observed['monitor'] == [['float', (15.625).hex()]] * 8
    print('OK    virtual numeric parameters preserve stores, aliases, errors and observers')


if __name__ == '__main__':
    main()
