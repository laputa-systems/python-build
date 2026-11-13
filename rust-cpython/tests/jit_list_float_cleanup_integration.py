import argparse
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

WARM = 7000
EVENTS = []
TRACES = []

def trace(fn):
    result = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            result.extend(list(_opcode.get_executor(fn.__code__, offset)))
        except (ValueError, RuntimeError):
            pass
    return result

def nested():
    return sum((1, 2, 3))

class Old:
    def __del__(self):
        frame = sys._getframe(1)
        instruction = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        target = frame.f_locals['target']
        EVENTS.append([instruction.opname, frame.f_lasti, target[0],
                       frame.f_locals['index'], frame.f_locals['entered'], nested()])
        TRACES.append(trace(assign))
        target[0] = 9.0

class FloatSubclass(float):
    __del__ = Old.__del__

def assign(target, source, a, b, active):
    for index in range(len(source)):
        target[0] = source.pop()
        entered = active()
        target[0] = a + b
    return target[0]

def finalizer(kind):
    EVENTS.clear()
    TRACES.clear()
    assign.__code__ = assign.__code__.replace()
    source = [kind()] + [1.0] * WARM
    result = assign([0.0], source, 1.0, 2.0, sys._jit.is_active)
    pcs = [i.offset for i in dis.get_instructions(assign) if i.opname == 'STORE_SUBSCR']
    assert result == 9.0 and not source
    assert len(EVENTS) == len(TRACES) == 1
    assert EVENTS[0][:4] == ['STORE_SUBSCR', pcs[-1], 3.0, WARM], EVENTS
    assert EVENTS[0][5] == 6
    return [result, list(EVENTS), TRACES[-1]]

def augment(target, position, count, a, active):
    for index in range(count):
        entered = active()
        target[position] += a
    return target[position], entered

def replace(target, count, a, b, active):
    for index in range(count):
        entered = active()
        retained = target[0]
        target[0] = a + b
        del retained
    return target[0], entered

def aliases():
    old = float('3.25')
    target = [old]
    before = sys.getrefcount(old)
    value, entered = replace(target, WARM, 1.0, 2.0, sys._jit.is_active)
    assert old.hex() == '0x1.a000000000000p+1'
    assert sys.getrefcount(old) == before - 1
    assert value == 3.0 and target[0] is not old
    incremented, aug_entered = augment([1.0], 0, WARM, 0.5, sys._jit.is_active)
    assert incremented == 1.0 + 0.5 * WARM
    negative, negative_entered = augment([1.0], -1, 64, 0.5, sys._jit.is_active)
    assert negative == 33.0
    return [value, incremented, entered, aug_entered, trace(replace), trace(augment), [negative, negative_entered]]

def bounds(target, a, b, active):
    for index in range(len(target) - 1):
        entered = active()
        target.pop()
        target[1] = a + b

def bounds_case():
    target = [0.0] * (WARM + 2)
    try:
        bounds(target, 1.0, 2.0, sys._jit.is_active)
    except IndexError as error:
        frame = error.__traceback__.tb_next.tb_frame
        outcome = [str(error), frame.f_locals['index'], frame.f_locals['entered'], frame.f_lasti]
    else:
        raise AssertionError('missing bounds error')
    assert target == [0.0]
    assert outcome[:2] == ['list assignment index out of range', WARM], outcome
    pc = next(i.offset for i in dis.get_instructions(bounds) if i.opname == 'STORE_SUBSCR')
    assert outcome[3] == pc
    return [outcome, trace(bounds)]

class Owned:
    def __del__(self):
        frame = sys._getframe(1)
        instruction = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        EVENTS.append([instruction.opname, frame.f_locals['index']])

def own(source, a, active):
    for index in range(len(source)):
        entered = active()
        source.pop()[0] += a
    return entered

def owners():
    EVENTS.clear()
    source = [[1.0, Owned()]] + [[1.0, None] for _ in range(WARM)]
    entered = own(source, 0.5, sys._jit.is_active)
    assert not source and EVENTS == [['STORE_SUBSCR', WARM]], EVENTS
    return [list(EVENTS), entered, trace(own)]

def reftracer():
    source = [[1.0] for _ in range(WARM)]
    _testcapi.start_counting_list_destroys()
    try:
        own(source, 0.5, sys._jit.is_active)
    finally:
        destroyed = _testcapi.stop_counting_list_destroys()
    assert not source and destroyed >= WARM, destroyed
    return destroyed

def controls():
    values = []
    for old in (None, True, 7, 'old', object()):
        value, _ = replace([old], 20, 1.0, 2.0, sys._jit.is_active)
        values.append(value)
    events = []
    def observer(frame, event, arg):
        if frame.f_code is replace.__code__ and event == 'return':
            events.append([frame.f_locals['target'][0], arg[0]])
        return observer
    for setter in (sys.settrace, sys.setprofile):
        setter(observer)
        try:
            value, _ = replace([0.0], 3, -0.0, -0.0, sys._jit.is_active)
            assert value.hex() == '-0x0.0p+0'
        finally:
            setter(None)
    assert events == [[-0.0, -0.0]] * 2
    gc.collect()
    return [values, events]

print(json.dumps(dict(old=finalizer(Old), subclass=finalizer(FloatSubclass),
                      aliases=aliases(), bounds=bounds_case(), owners=owners(),
                      controls=controls(), reftracer=reftracer())))
'''


def run(jit, optimize):
    result = subprocess.run([sys.executable, '-S', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0', PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (jit, optimize, result.stderr)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect', choices=('candidate', 'baseline'), default='candidate')
    args = parser.parse_args()
    on, off, noopt = run(True, True), run(False, True), run(True, False)
    for result in (on, off, noopt):
        assert result['controls'] == on['controls']
        assert result['reftracer'] >= 7000
        assert result['aliases'][:2] == on['aliases'][:2]
        assert result['bounds'][0][:2] == off['bounds'][0][:2]
        assert result['owners'][0] == off['owners'][0]
        for name in ('old', 'subclass'):
            assert result[name][0] == off[name][0]
            assert result[name][1][0][:4] == off[name][1][0][:4]
            assert result[name][1][0][5:] == off[name][1][0][5:]
    assert all(on[name][1][0][4] for name in ('old', 'subclass'))
    assert all(on['aliases'][2:4]) and on['aliases'][6] == [33.0, True] and on['bounds'][0][2] and on['owners'][1]
    traces = [on[name][2] for name in ('old', 'subclass')]
    traces += on['aliases'][4:6] + [on['bounds'][1], on['owners'][2]]
    for entries in traces:
        selected = sum(entry[0] == '_STORE_SUBSCR_LIST_INT_FLOAT' for entry in entries)
        assert bool(selected) == (args.expect == 'candidate'), (args.expect, selected, entries)
    for result in (off, noopt):
        for entries in ([result[name][2] for name in ('old', 'subclass')] +
                        result['aliases'][4:6] + [result['bounds'][1], result['owners'][2]]):
            assert not any(entry[0] == '_STORE_SUBSCR_LIST_INT_FLOAT' for entry in entries)
    print('OK guarded list float cleanup, finalizer PC, aliases, bounds, owned lists and observers')


if __name__ == '__main__':
    main()
