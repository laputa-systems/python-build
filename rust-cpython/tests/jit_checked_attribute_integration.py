import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import dis
import gc
import json
import sys
import weakref

WARM = 12000
class Box:
    pass
class Child(Box):
    pass
class Slot:
    __slots__ = ('value',)
class Token:
    pass

def read_loop(obj, n, active_fn=sys._jit.is_active):
    result = None
    active = False
    for _ in range(n):
        active = active_fn()
        result = obj.value
    return result, active

def trace(function):
    result = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            result.extend(list(_opcode.get_executor(function.__code__, offset)))
        except (ValueError, RuntimeError):
            pass
    return result

obj = Box()
token = Token()
obj.value = token
before = sys.getrefcount(token)
result, active = read_loop(obj, WARM)
entries = trace(read_loop)
alias = result is token
result = None
refdelta = sys.getrefcount(token) - before
obj.__dict__
materialized = read_loop(obj, 5)[0] is token
obj.__dict__ = {'value': 'replaced'}
replaced = read_loop(obj, 5)[0]
obj.__dict__.clear()
try:
    read_loop(obj, 1)
except AttributeError:
    missing = True
else:
    missing = False

miss_pcs = []
class Plain:
    pass
plain = Plain()
peer = Plain()
plain.value = 'warm'
peer.value = 'peer'
read_loop(plain, WARM)
plain_trace = trace(read_loop)
del plain.value
missing_pcs = []
for _ in range(300):
    try:
        read_loop(plain, 1)
    except AttributeError as error:
        tb = error.__traceback__
        while tb.tb_next is not None:
            tb = tb.tb_next
        missing_pcs.append(tb.tb_lasti)
    else:
        raise AssertionError('missing value unexpectedly found')
plain.value = 'restored'
restored, resumed = read_loop(plain, WARM)

finalized = []
class Ephemeral:
    def __init__(self, value, report=False):
        self.value = value
        self.report = report
    def __del__(self):
        if self.report:
            frame = sys._getframe(1)
            finalized.append([frame.f_lasti, frame.f_locals['result']])
def pop_loop(source):
    result = 'prior'
    for _ in range(len(source)):
        result = source.pop().value
    return result
pop_loop([Ephemeral('warm') for _ in range(WARM)])
pop_trace = trace(pop_loop)
last = pop_loop([Ephemeral('last', True)])
pop_pc = next(i.offset for i in dis.get_instructions(pop_loop) if i.opname == 'LOAD_ATTR' and i.argval == 'value')
class Fallback:
    def __getattr__(self, name):
        if name == 'value':
            miss_pcs.append(sys._getframe(1).f_lasti)
            return 'fallback'
        raise AttributeError(name)
fallback = Fallback()
fallback.value = 'warm'
read_loop(fallback, WARM)
del fallback.value
miss_result = read_loop(fallback, 300)[0]
load_pc = next(i.offset for i in dis.get_instructions(read_loop) if i.opname == 'LOAD_ATTR' and i.argval == 'value')

class Mutable:
    pass
mutable = Mutable()
mutable.value = 'instance'
read_loop(mutable, WARM)
Mutable.value = property(lambda self: 'property')
property_value = read_loop(mutable, 5)[0]
Mutable.value = property(lambda self: 'changed')
changed = read_loop(mutable, 5)[0]
child = Child()
child.value = 'child'
slot = Slot()
slot.value = 'slot'

observed = []
class Owner:
    def __del__(self):
        observed.append('owner finalized')
owner = Owner()
owner.value = Token()
owner_ref = weakref.ref(owner)
value_ref = weakref.ref(owner.value)
held, _ = read_loop(owner, WARM)
owner = None
gc.collect()
owner_gone = owner_ref() is None
value_alive = value_ref() is held
held = None
gc.collect()
value_gone = value_ref() is None

observations = []
def observer(frame, event, arg):
    if event == 'return' and frame.f_code is read_loop.__code__:
        observations.append(arg[0] is token)
    return observer
obj.value = token
sys.setprofile(observer)
read_loop(obj, 2)
sys.setprofile(None)
sys.settrace(observer)
read_loop(obj, 2)
sys.settrace(None)
print(json.dumps({'results': [alias, refdelta, materialized, replaced, missing,
    len(miss_pcs), miss_result == 'fallback' and all(pc == load_pc for pc in miss_pcs), property_value, changed,
    read_loop(child, 5)[0], read_loop(slot, 5)[0], owner_gone, value_alive,
    value_gone, observed, observations, len(missing_pcs),
    all(pc == load_pc for pc in missing_pcs), restored, last,
    finalized == [[pop_pc, 'prior']]], 'trace': entries, 'plain_trace': plain_trace,
    'pop_trace': pop_trace, 'active': active, 'resumed': resumed,
    'debug': {'miss_pcs': miss_pcs, 'missing_pcs': missing_pcs, 'load_pc': load_pc, 'finalized': finalized, 'pop_pc': pop_pc},
    'load_offsets': [i.offset // 2 for i in dis.get_instructions(read_loop) if i.opname == 'LOAD_ATTR' and i.argval == 'value']}))
'''


def run(jit, optimize=True):
    process = subprocess.run([sys.executable, '-s', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, (jit, optimize, process.returncode, process.stderr)
    return json.loads(process.stdout)


def main():
    previous = sys.argv[1:] == ['--expect', 'previous']
    assert not sys.argv[1:] or previous, sys.argv
    candidate, reference, noopt = run(True), run(False), run(True, False)
    expected = [True, 0, True, 'replaced', True, 300, True, 'property', 'changed',
                'child', 'slot', True, True, True, ['owner finalized'], [True, True],
    300, True, 'restored', 'last', True]
    for result in (candidate, reference, noopt):
        assert result['results'] == expected, (result['results'], result['debug'])
    assert candidate['active'] and candidate['resumed'], candidate
    names = [entry[0] for entry in candidate['trace']]
    if previous:
        assert '_CHECK_MANAGED_OBJECT_HAS_VALUES' in names and '_LOAD_ATTR_INSTANCE_VALUE' in names, names
        assert '_LOAD_ATTR_INSTANCE_VALUE_CHECKED' not in names, names
    else:
        assert '_LOAD_ATTR_INSTANCE_VALUE_CHECKED' in names, names
    for key in ('plain_trace', 'pop_trace'):
        captured = [e[0] for e in candidate[key]]
        if previous:
            assert '_CHECK_MANAGED_OBJECT_HAS_VALUES' in captured and '_LOAD_ATTR_INSTANCE_VALUE' in captured, (key, captured)
        else:
            assert '_LOAD_ATTR_INSTANCE_VALUE_CHECKED' in captured, (key, captured)
    assert '_LOAD_ATTR_INSTANCE_VALUE_CHECKED' not in [e[0] for e in noopt['trace']]
    print('OK checked attribute lookup and miss progress')


if __name__ == '__main__':
    main()
