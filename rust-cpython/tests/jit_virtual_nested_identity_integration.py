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
    behavior_main(baseline)
    print('OK nested identity results, recursion, and executor shape')


FAULT = 7016

BEHAVIOR = r'''

import _opcode
import _testcapi
import dis
import gc
import json
import sys

WARM = 7000
FAULT = WARM + 16
RESULTS, TRACES, ACTIVE, EVENTS, FINALIZERS = {}, {}, [], [], []
IDENTITY_CALLBACKS, IDENTITY_OBSERVED = [], []

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
        def mustBeVector(self): return self
        def calculate(self, other, divisor, unused):
            prefix = self.value + self.value
            other.mustBeVector()
            first = self.value + other.value
            alias = first
            second = first * divisor
            return second / divisor
        def sum_fields(self, other, unused, tail):
            other.mustBeVector()
            return self.value + other.value
        def replace_fields(self, first_old, second_old, unused):
            self.mustBeVector()
            first = self.value + self.value
            first_old = first + first
            second_old = first_old + first
            return second_old + first
    class Peer(Owner): __slots__ = ()
    Owner.Peer = Peer
    def calculate(left, right, divisor, unused):
        prefix = left.value + left.value
        right.mustBeVector()
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

def identity_event(owner, kind):
    frame = sys._getframe(2)
    ins = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
    argument = frame.f_locals.get('other', frame.f_locals.get('right'))
    IDENTITY_CALLBACKS.append([kind, frame.f_code.co_name, ins.opname,
                               frame.f_locals['prefix'].hex(), argument is owner])

def identity_callback(self):
    identity_event(self, 'callback')
    self.value += 1.0
    return self

def identity_raise(self):
    identity_event(self, 'raise')
    raise RuntimeError('identity failure')

def identity_descriptor(self):
    identity_event(self, 'descriptor')
    return lambda: self

for bound in (False, True):
    for scenario in ('success', 'zero', 'missing', 'wrong owner', 'wrong', 'subclass', 'descriptor', 'class',
                     'identity callback', 'identity code', 'identity raise', 'identity descriptor',
                     *(('replace dict', 'clear dict') if __MANAGED__ else ())):
        Owner, standalone = make()
        fn = Owner.calculate if bound else standalone
        if scenario in ('missing', 'wrong owner'):
            name = 'mustBeVector' if scenario == 'wrong owner' else 'value'
            ins = [i for i in dis.get_instructions(fn) if i.opname == 'LOAD_ATTR' and i.argval == name][-1]
            RESULTS[str(bound) + ' ' + scenario + ' position'] = [ins.positions.col_offset, ins.positions.end_col_offset]
        if scenario == 'identity raise':
            ins = next(i for i in dis.get_instructions(fn) if i.opname == 'CALL')
            RESULTS[str(bound) + ' identity call position'] = [ins.positions.col_offset, ins.positions.end_col_offset]
        if scenario == 'zero':
            ins = [i for i in dis.get_instructions(fn) if i.opname == 'BINARY_OP'][-1]
            RESULTS[str(bound) + ' zero position'] = [ins.positions.col_offset, ins.positions.end_col_offset]
        caller = make_loop(bound)
        left, right = Owner(1.25), Owner.Peer(2.5)
        unused = object()
        source = Source(right)
        caller(WARM, fn, left, source, sys._jit.is_active, unused)
        action = None
        divisor = 0.0 if scenario == 'zero' else 2.0
        if scenario == 'missing': action = lambda: delattr(right, 'value')
        elif scenario == 'wrong': action = lambda: setattr(right, 'value', Add())
        elif scenario == 'subclass': action = lambda: setattr(right, 'value', AddFloat(2.5))
        elif scenario == 'identity callback': action = lambda: setattr(Owner.Peer, 'mustBeVector', identity_callback)
        elif scenario == 'identity code': action = lambda: setattr(Owner.mustBeVector, '__code__', identity_callback.__code__)
        elif scenario == 'identity raise': action = lambda: setattr(Owner.Peer, 'mustBeVector', identity_raise)
        elif scenario == 'identity descriptor': action = lambda: setattr(Owner.Peer, 'mustBeVector', property(identity_descriptor))
        elif scenario == 'descriptor': action = lambda: setattr(Owner, 'value', property(lambda self: 9.0))
        elif scenario == 'class':
            class Other(Owner): __slots__ = ()
            action = lambda: setattr(right, '__class__', Other)
        elif scenario == 'replace dict': action = lambda: setattr(right, '__dict__', {'value': 9.0})
        elif scenario == 'clear dict': action = lambda: right.__dict__.clear()
        source = None
        before = sys.getrefcount(right)
        source = Source(right, divisor, action)
        if scenario == 'wrong owner':
            right = object()
            source.values[0] = (right, divisor, None)
        label = str(bound) + ' ' + scenario
        try:
            value, entries, active = caller(FAULT, fn, left, source, sys._jit.is_active, unused)
        except (AttributeError, ZeroDivisionError, RuntimeError) as error:
            RESULTS[label] = capture(error, fn, caller, label)
            if isinstance(error, AttributeError):
                RESULTS[label + ' payload'] = [error.name, error.obj is right]
        else:
            ACTIVE.append(active)
            TRACES[label] = entries
            RESULTS[label] = [value.hex(), sys.getrefcount(right) - before]
        RESULTS[label + ' progress'] = [source.calls, len(source.values)]
RESULTS['events'] = EVENTS
RESULTS['finalizers'] = FINALIZERS
RESULTS['identity callbacks'] = IDENTITY_CALLBACKS

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

for bound in (False, True):
    for first_old, second_old in ((1.25, 2**100), (None, True), (True, None)):
        Owner, _ = make()
        left = Owner(1.25)
        fn = Owner.replace_fields
        caller = make_loop(bound, method='replace_fields')
        source = Source(first_old, second_old)
        source.values = [(first_old, second_old, None)] + [(1.25, 2.5, None)] * (FAULT - 1)
        value, entries, active = caller(FAULT, fn, left, source, sys._jit.is_active, object())
        label = f'replacement {bound} {type(first_old).__name__} {type(second_old).__name__}'
        RESULTS[label] = [value.hex(), source.calls, len(source.values)]
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
    if event == 'return' and frame.f_code.co_name == 'mustBeVector':
        IDENTITY_OBSERVED.append(frame.f_back.f_code.co_name)
    if event == 'return' and frame.f_code is fn.__code__:
        observed.append([arg.hex(), frame.f_locals['alias'] is frame.f_locals['first'], frame.f_locals['prefix'].hex()])
def tracer(frame, event, arg):
    observer(frame, event, arg)
    return tracer
sys.setprofile(observer)
for _ in range(8): fn(left, right, 2.0, unused)
sys.setprofile(None)
RESULTS['profile'] = observed[:]
RESULTS['identity profile'] = IDENTITY_OBSERVED[:]
IDENTITY_OBSERVED.clear()
observed.clear()
sys.settrace(tracer)
for _ in range(8): fn(left, right, 2.0, unused)
sys.settrace(None)
RESULTS['trace'] = observed[:]
RESULTS['identity trace'] = IDENTITY_OBSERVED[:]
IDENTITY_OBSERVED.clear()
observed.clear()
monitor = sys.monitoring
monitor.use_tool_id(monitor.DEBUGGER_ID, 'argument-attributes-test')
def on_return(code, offset, value):
    if code is fn.__code__: observed.append(value.hex())
    if code.co_name == 'mustBeVector': IDENTITY_OBSERVED.append(code.co_name)
monitor.register_callback(monitor.DEBUGGER_ID, monitor.events.PY_RETURN, on_return)
monitor.set_events(monitor.DEBUGGER_ID, monitor.events.PY_RETURN)
for _ in range(8): fn(left, right, 2.0, unused)
monitor.set_events(monitor.DEBUGGER_ID, 0)
monitor.free_tool_id(monitor.DEBUGGER_ID)
RESULTS['monitor'] = observed
RESULTS['identity monitor'] = IDENTITY_OBSERVED
print(json.dumps({'results': RESULTS, 'traces': TRACES, 'active': ACTIVE}, sort_keys=True))

'''

def behavior_run(jit, managed, optimize=True):
    layout = 'pass' if managed else "__slots__ = ('value',)"
    program = BEHAVIOR.replace('__LAYOUT__', layout).replace('__MANAGED__', repr(managed))
    result = subprocess.run([sys.executable, '-s', '-c', program],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (jit, managed, optimize, result.returncode, result.stderr)
    return json.loads(result.stdout)

def behavior_main(baseline):
    for managed in (False, True):
        candidate = behavior_run(jit=True, managed=managed)
        reference = behavior_run(jit=False, managed=managed)
        noopt = behavior_run(jit=True, managed=managed, optimize=False)
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
            assert results[prefix + 'missing payload'] == ['value', True]
            missing_frame = results[prefix + 'missing'][-1][-1]
            assert missing_frame[1] == 'LOAD_ATTR'
            assert missing_frame[2:4] == results[prefix + 'missing position']
            assert results[prefix + 'wrong owner'][0] == 'AttributeError'
            assert results[prefix + 'wrong owner payload'] == ['mustBeVector', True]
            owner_frame = results[prefix + 'wrong owner'][-1][-1]
            assert owner_frame[1] == 'LOAD_ATTR'
            assert owner_frame[2:4] == results[prefix + 'wrong owner position']
            assert results[prefix + 'zero'][0] == 'ZeroDivisionError'
            zero_frame = results[prefix + 'zero'][-1][-1]
            assert zero_frame[1] == 'BINARY_OP' and zero_frame[2:4] == results[prefix + 'zero position']
            local = zero_frame[-1]
            assert local['first'] == local['alias'] == 3.75.hex() and local['alias_is_first']
            assert local['second'] == 0.0.hex() and local['divisor'] == 0.0.hex()
            assert local['prefix'] == 2.5.hex()
            for name in ('identity callback', 'identity code'):
                assert results[prefix + name][0] == 4.75.hex()
            assert results[prefix + 'identity descriptor'][0] == 3.75.hex()
            assert results[prefix + 'identity raise'][0] == 'RuntimeError'
            method_frame = next(frame for frame in results[prefix + 'identity raise'][-1] if frame[0] == 'calculate')
            assert method_frame[1] == 'CALL'
            assert method_frame[2:4] == results[prefix + 'identity call position']
            assert method_frame[-1]['prefix'] == 2.5.hex()
            assert 'first' not in method_frame[-1] and 'alias' not in method_frame[-1]
            if managed:
                assert results[prefix + 'replace dict'][0] == 10.25.hex()
                assert results[prefix + 'clear dict'][0] == 'AttributeError'
                assert results[prefix + 'clear dict payload'] == ['value', True]
        for key, value in results.items():
            if key.endswith(' progress'): assert value == [FAULT, 0], (key, value)
        for bound in (False, True):
            for shared in (False, True):
                label = f'extent {bound} {shared}'
                assert results[label] == [(2.5 if shared else 3.75).hex(), FAULT, 0, 4]
                if baseline: continue
                begin = next(entry for entry in candidate['traces'][label]
                             if entry[0] == '_BEGIN_VIRTUAL_CALL')
                assert begin[3] & (1 << 31), (label, begin)
                assert begin[3] & 0x7fffffff == (3 if bound else 4), (label, begin)
        assert results['generator'] == [FAULT, 3.75.hex(), FAULT]
        for bound in (False, True):
            for first_type, second_type in (('float', 'int'), ('NoneType', 'bool'), ('bool', 'NoneType')):
                label = f'replacement {bound} {first_type} {second_type}'
                assert results[label] == [10.0.hex(), FAULT, 0]
                if baseline: continue
                entries = candidate['traces'][label]
                names = [entry[0] for entry in entries]
                begin = next(entry for entry in entries if entry[0] == '_BEGIN_VIRTUAL_CALL')
                assert bool(begin[3] & (1 << 31)) is bound, (label, begin)
                assert names.count('_STORE_VIRTUAL_ARGUMENT_NUMERIC') == 2, (label, names)
        assert results['generator error'][0] == 'ZeroDivisionError'
        assert results['generator error'][-1][-1][-1]['prefix'] == 2.5.hex()
        assert results['oom'][0] == 'MemoryError'
        assert results['oom'][-1][-1][1] == 'BINARY_OP'
        assert results['oom'][-1][-1][-1]['first'] == 3.75.hex()
        assert results['oom'][-1][-1][-1]['prefix'] == 2.5.hex()
        assert results['profile'] == results['trace'] == [[3.75.hex(), True, 2.5.hex()]] * 8
        assert results['monitor'] == [3.75.hex()] * 8
        assert results['identity profile'] == results['identity trace'] == ['calculate'] * 8
        assert results['identity monitor'] == ['mustBeVector'] * 8
        callbacks = results['identity callbacks']
        assert len(callbacks) == 8
        assert all(row[1] == 'calculate' and row[3:] == [2.5.hex(), True] for row in callbacks)
        assert all(row[2] == ('LOAD_ATTR' if row[0] == 'descriptor' else 'CALL') for row in callbacks)
        assert sum(row[0] == 'callback' for row in callbacks) == 4
        assert all(event[1] == 'BINARY_OP' and event[-1] == 1.25.hex() for event in results['events'])
        assert len(results['events']) == 4
        assert all(event[0] == 'calculate' and 'divisor' in event[2] for event in results['events'])
        assert results['finalizers'] == ['loop'] * (22 if managed else 18)
        assert candidate['active'] and all(candidate['active'])
        for label, entries in candidate['traces'].items():
            names = [entry[0] for entry in entries]
            if baseline:
                assert '_BEGIN_VIRTUAL_CALL' not in names, (label, names)
                continue
            begin, end = names.index('_BEGIN_VIRTUAL_CALL'), names.index('_RETURN_VIRTUAL_CALL')
            body = names[begin:end]
            assert '_CALL_PY_LEAF' in body and '_PUSH_FRAME' not in body, (label, body)
            guards = body
            if label.startswith('replacement True '):
                caller = max((index for index, name in enumerate(names[:begin])
                              if name == '_RETURN_VALUE'), default=0)
                guards = names[caller:end]
            assert any('TYPE_VERSION' in name for name in guards), (label, guards)
            assert ('_LOAD_ATTR_INSTANCE_VALUE' if managed else '_LOAD_ATTR_SLOT') in body, (label, body)
            if managed: assert '_CHECK_MANAGED_OBJECT_HAS_VALUES' in body, (label, body)
        assert all('_BEGIN_VIRTUAL_CALL' not in [entry[0] for entry in entries]
                   for entries in noopt['traces'].values())
    print('OK nested identity callbacks, errors, aliases, generators, OOM and observers')



if __name__ == '__main__':
    main()
