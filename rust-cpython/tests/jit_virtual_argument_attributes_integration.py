import json
import os
import subprocess
import sys

FAULT = 7016

PROGRAM = r'''
import _opcode
import _testcapi
import dis
import gc
import json
import sys

WARM = 7000
FAULT = WARM + 16
RESULTS, TRACES, ACTIVE, EVENTS, FINALIZERS = {}, {}, [], [], []

def trace(fn):
    entries = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try: entries.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError): pass
    return entries

def make():
    class Owner:
        __LAYOUT__
        def __init__(self, value): self.value = value
        def calculate(self, other, divisor, unused):
            first = self.value + other.value
            alias = first
            second = first * divisor
            return second / divisor
        def sum_fields(self, other, unused, tail):
            return self.value + other.value
    def calculate(left, right, divisor, unused):
        first = left.value + right.value
        alias = first
        second = first * divisor
        return second / divisor
    return Owner, calculate

def make_loop(bound, generator=False, method='calculate'):
    call = f'left.{method}(right, divisor, unused)' if bound else 'fn(left, right, divisor, unused)'
    output = '        yield value\n' if generator else ''
    namespace = {'trace': trace, 'WARM': WARM}
    exec('def loop(n, fn, left, source, active, unused):\n'
         '    entries = []\n    was_active = False\n    transient = None\n'
         '    for index in range(n):\n        was_active = active()\n'
         '        right, divisor, transient = source.pop()\n'
         '        value = ' + call + '\n'
         '        if index == WARM - 1:\n            entries = trace(loop)\n' + output +
         ('    return value, entries, was_active\n' if not generator else ''), namespace)
    return namespace['loop']

class Mutate:
    def __init__(self, action): self.action = action
    def __del__(self):
        FINALIZERS.append(sys._getframe(1).f_code.co_name)
        self.action()

class Source:
    def __init__(self, right, divisor=2.0, action=None):
        self.values = [(right, divisor, None)] + [(right, 2.0, None)] * (FAULT - 1)
        if action is not None: self.values[1] = (right, 2.0, Mutate(action))
        self.calls = 0
    def pop(self):
        self.calls += 1
        return self.values.pop()

def snapshot(frame, fn):
    if frame.f_code is not fn.__code__: return {}
    result = {}
    for name, value in frame.f_locals.items():
        if name in ('self', 'left', 'right', 'other'): result[name] = 'owner'
        elif name == 'unused': result[name] = 'unused'
        else: result[name] = value.hex() if isinstance(value, float) else type(value).__name__
    if 'alias' in frame.f_locals:
        result['alias_is_first'] = frame.f_locals['alias'] is frame.f_locals['first']
    return result

def capture(error, fn, caller, label):
    frames = []
    tb = error.__traceback__
    while tb:
        frame = tb.tb_frame
        ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == tb.tb_lasti)
        if frame.f_code is caller.__code__:
            ACTIVE.append(frame.f_locals['was_active'])
            TRACES[label] = frame.f_locals['entries']
        frames.append([frame.f_code.co_name, ins.opname, ins.positions.col_offset,
                       ins.positions.end_col_offset, snapshot(frame, fn)])
        tb = tb.tb_next
    return [type(error).__name__, str(error), frames]

class Add:
    def __radd__(self, other):
        frame = sys._getframe(1)
        ins = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        EVENTS.append([frame.f_code.co_name, ins.opname, sorted(frame.f_locals), other.hex()])
        return 7.0
class AddFloat(float):
    __radd__ = Add.__radd__

for bound in (False, True):
    for scenario in ('success', 'zero', 'missing', 'wrong', 'subclass', 'descriptor', 'class',
                     *(('replace dict', 'clear dict') if __MANAGED__ else ())):
        Owner, standalone = make()
        fn = Owner.calculate if bound else standalone
        if scenario == 'missing':
            ins = [i for i in dis.get_instructions(fn) if i.opname == 'LOAD_ATTR'][-1]
            RESULTS[str(bound) + ' missing position'] = [ins.positions.col_offset, ins.positions.end_col_offset]
        if scenario == 'zero':
            ins = [i for i in dis.get_instructions(fn) if i.opname == 'BINARY_OP'][-1]
            RESULTS[str(bound) + ' zero position'] = [ins.positions.col_offset, ins.positions.end_col_offset]
        caller = make_loop(bound)
        left, right = Owner(1.25), Owner(2.5)
        unused = object()
        source = Source(right)
        caller(WARM, fn, left, source, sys._jit.is_active, unused)
        action = None
        divisor = 0.0 if scenario == 'zero' else 2.0
        if scenario == 'missing': action = lambda: delattr(right, 'value')
        elif scenario == 'wrong': action = lambda: setattr(right, 'value', Add())
        elif scenario == 'subclass': action = lambda: setattr(right, 'value', AddFloat(2.5))
        elif scenario == 'descriptor': action = lambda: setattr(Owner, 'value', property(lambda self: 9.0))
        elif scenario == 'class':
            class Other(Owner): __slots__ = ()
            action = lambda: setattr(right, '__class__', Other)
        elif scenario == 'replace dict': action = lambda: setattr(right, '__dict__', {'value': 9.0})
        elif scenario == 'clear dict': action = lambda: right.__dict__.clear()
        source = None
        before = sys.getrefcount(right)
        source = Source(right, divisor, action)
        label = str(bound) + ' ' + scenario
        try:
            value, entries, active = caller(FAULT, fn, left, source, sys._jit.is_active, unused)
        except (AttributeError, ZeroDivisionError) as error:
            RESULTS[label] = capture(error, fn, caller, label)
        else:
            ACTIVE.append(active)
            TRACES[label] = entries
            RESULTS[label] = [value.hex(), sys.getrefcount(right) - before]
        RESULTS[label + ' progress'] = [source.calls, len(source.values)]
RESULTS['events'] = EVENTS
RESULTS['finalizers'] = FINALIZERS

for bound in (False, True):
    for shared in (False, True):
        Owner, _ = make()
        left = Owner(1.25)
        right = left if shared else Owner(2.5)
        fn = Owner.sum_fields
        caller = make_loop(bound, method='sum_fields')
        source = Source(right)
        value, entries, active = caller(FAULT, fn, left, source, sys._jit.is_active, object())
        label = f'extent {bound} {shared}'
        RESULTS[label] = [value.hex(), source.calls, len(source.values), fn.__code__.co_nlocals]
        TRACES[label] = entries
        ACTIVE.append(active)

Owner, fn = make()
left, right, unused = Owner(1.25), Owner(2.5), object()
generator = make_loop(bound=False, generator=True)
source = Source(right)
values = list(generator(FAULT, fn, left, source, sys._jit.is_active, unused))
RESULTS['generator'] = [len(values), values[-1].hex(), source.calls]
source = Source(right, 0.0)
try:
    list(generator(FAULT, fn, left, source, sys._jit.is_active, unused))
except ZeroDivisionError as error:
    RESULTS['generator error'] = capture(error, fn, generator, 'generator')
    RESULTS['generator progress'] = [source.calls, len(source.values)]
else: raise AssertionError('generator did not fail')

class Arm:
    def __del__(self): _testcapi.set_nomemory(1, 2)

def allocating(n, fn, left, right, unused, source, active):
    held = [None] * n
    entries = []
    was_active = False
    transient = None
    for index in range(n):
        was_active = active()
        transient = source.pop()
        held[index] = fn(left, right, 2.0, unused)
        if index == WARM - 1: entries = trace(allocating)
    return held

try:
    allocating(FAULT + 3, fn, left, right, unused, [None, None, Arm()] + [None] * FAULT, sys._jit.is_active)
except MemoryError as error:
    _testcapi.remove_mem_hooks()
    RESULTS['oom'] = capture(error, fn, allocating, 'oom')
else: raise AssertionError('allocation did not fail')
finally: _testcapi.remove_mem_hooks()

observed = []
def observer(frame, event, arg):
    if event == 'return' and frame.f_code is fn.__code__:
        observed.append([arg.hex(), frame.f_locals['alias'] is frame.f_locals['first']])
def tracer(frame, event, arg):
    observer(frame, event, arg)
    return tracer
sys.setprofile(observer)
for _ in range(8): fn(left, right, 2.0, unused)
sys.setprofile(None)
RESULTS['profile'] = observed[:]
observed.clear()
sys.settrace(tracer)
for _ in range(8): fn(left, right, 2.0, unused)
sys.settrace(None)
RESULTS['trace'] = observed[:]
observed.clear()
monitor = sys.monitoring
monitor.use_tool_id(monitor.DEBUGGER_ID, 'argument-attributes-test')
def on_return(code, offset, value):
    if code is fn.__code__: observed.append(value.hex())
monitor.register_callback(monitor.DEBUGGER_ID, monitor.events.PY_RETURN, on_return)
monitor.set_events(monitor.DEBUGGER_ID, monitor.events.PY_RETURN)
for _ in range(8): fn(left, right, 2.0, unused)
monitor.set_events(monitor.DEBUGGER_ID, 0)
monitor.free_tool_id(monitor.DEBUGGER_ID)
RESULTS['monitor'] = observed
print(json.dumps({'results': RESULTS, 'traces': TRACES, 'active': ACTIVE}, sort_keys=True))
'''


def run(jit, managed, optimize=True):
    program = PROGRAM.replace('__LAYOUT__', 'pass' if managed else "__slots__ = ('value',)")
    program = program.replace('__MANAGED__', repr(managed))
    result = subprocess.run([sys.executable, '-s', '-c', program],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, (jit, managed, optimize, result.returncode, result.stderr)
    return json.loads(result.stdout)


def main():
    for managed in (False, True):
        candidate = run(jit=True, managed=managed)
        reference = run(jit=False, managed=managed)
        noopt = run(jit=True, managed=managed, optimize=False)
        assert candidate['results'] == reference['results'] == noopt['results']
        results = reference['results']
        for bound in (False, True):
            prefix = str(bound) + ' '
            assert results[prefix + 'success'] == [3.75.hex(), 0]
            assert results[prefix + 'wrong'][0] == 7.0.hex()
            assert results[prefix + 'subclass'][0] == 7.0.hex()
            assert results[prefix + 'descriptor'][0] == 18.0.hex()
            assert results[prefix + 'class'][0] == 3.75.hex()
            assert results[prefix + 'missing'][0] == 'AttributeError'
            missing_frame = results[prefix + 'missing'][-1][-1]
            assert missing_frame[1] == 'LOAD_ATTR'
            assert missing_frame[2:4] == results[prefix + 'missing position']
            assert results[prefix + 'zero'][0] == 'ZeroDivisionError'
            zero_frame = results[prefix + 'zero'][-1][-1]
            assert zero_frame[1] == 'BINARY_OP' and zero_frame[2:4] == results[prefix + 'zero position']
            local = zero_frame[-1]
            assert local['first'] == local['alias'] == 3.75.hex() and local['alias_is_first']
            assert local['second'] == 0.0.hex() and local['divisor'] == 0.0.hex()
            if managed:
                assert results[prefix + 'replace dict'][0] == 10.25.hex()
                assert results[prefix + 'clear dict'][0] == 'AttributeError'
        for key, value in results.items():
            if key.endswith(' progress'): assert value == [FAULT, 0], (key, value)
        for bound in (False, True):
            for shared in (False, True):
                label = f'extent {bound} {shared}'
                assert results[label] == [(2.5 if shared else 3.75).hex(), FAULT, 0, 4]
                begin = next(entry for entry in candidate['traces'][label]
                             if entry[0] == '_BEGIN_VIRTUAL_CALL')
                assert begin[3] == 4, (label, begin)
        assert results['generator'] == [FAULT, 3.75.hex(), FAULT]
        assert results['generator error'][0] == 'ZeroDivisionError'
        assert results['oom'][0] == 'MemoryError'
        assert results['oom'][-1][-1][1] == 'BINARY_OP'
        assert results['oom'][-1][-1][-1]['first'] == 3.75.hex()
        assert results['profile'] == results['trace'] == [[3.75.hex(), True]] * 8
        assert results['monitor'] == [3.75.hex()] * 8
        assert all(event[1] == 'BINARY_OP' and event[-1] == 1.25.hex() for event in results['events'])
        assert len(results['events']) == 4
        assert all(event[0] == 'calculate' and 'divisor' in event[2] for event in results['events'])
        assert results['finalizers'] == ['loop'] * (14 if managed else 10)
        assert candidate['active'] and all(candidate['active'])
        for label, entries in candidate['traces'].items():
            names = [entry[0] for entry in entries]
            begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
            body = names[begin:end]
            assert any('TYPE_VERSION' in name for name in body), (label, body)
            assert ('_LOAD_ATTR_INSTANCE_VALUE' if managed else '_LOAD_ATTR_SLOT') in body, (label, body)
            if managed: assert '_CHECK_MANAGED_OBJECT_HAS_VALUES' in body, (label, body)
        assert all('_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
                   for entries in noopt['traces'].values())
    print('OK virtual argument attributes')


if __name__ == '__main__':
    main()
