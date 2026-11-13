import json
import os
import subprocess
import sys


PROGRAM = r'''
from array import array
import json
import struct
import sys

WARM = 7016

class FloatSubclass(float):
    def __float__(self):
        raise AssertionError('the float C API reads a float subclass directly')

def assign(target, values, active):
    for index in range(len(values)):
        entered = active()
        target[0] = values.pop()
    return entered

def exact_values():
    result = []
    activity = []
    patterns = [0, 1 << 63, 0x3ff4000000000000, 0xbfe0000000000000,
                1, 0x7fefffffffffffff, 0x7ff0000000000000,
                0xfff0000000000000, 0x7ff8000000000001, 0xfff80000000000ab]
    for pattern in patterns:
        value = struct.unpack('=d', struct.pack('=Q', pattern))[0]
        expected = struct.pack('=d', value).hex()
        observed = []
        for kind in (float, FloatSubclass):
            item = value if kind is float else kind(value)
            target = array('d', [0.0])
            source = [item] * WARM
            before = sys.getrefcount(item)
            activity.append(assign(target, source, sys._jit.is_active))
            assert not source and sys.getrefcount(item) == before - WARM
            assert target.tobytes().hex() == expected, (pattern, kind, target.tobytes().hex())
            view = memoryview(target)
            try:
                target[0] = item
                assert view.tobytes().hex() == expected
            finally:
                view.release()
            observed.append(target.tobytes().hex())
        result.append(observed)
    return result, activity

def conversion(mode):
    target = array('d', [1.0, 2.0])
    events = []
    class Converted:
        def __float__(self):
            events.append('float')
            if mode == 'shrink':
                del target[:]
            elif mode == 'grow':
                target.extend([8.0] * 4096)
            elif mode == 'raise':
                raise ValueError('conversion failed')
            elif mode == 'bad':
                return 4
            return 3.5
    value = Converted()
    try:
        target[1] = value
    except (IndexError, TypeError, ValueError) as error:
        outcome = [type(error).__name__, str(error)]
    else:
        outcome = ['value', target[1]]
    assert events == ['float'], (mode, events)
    if mode == 'shrink':
        assert outcome == ['IndexError', 'array assignment index out of range'] and not target
    elif mode == 'grow':
        assert outcome == ['value', 3.5] and len(target) == 4098 and target[-1] == 8.0
    elif mode == 'raise':
        assert outcome == ['ValueError', 'conversion failed'] and target.tolist() == [1.0, 2.0]
    elif mode == 'bad':
        assert outcome[0] == 'TypeError' and target.tolist() == [1.0, 2.0]
    else:
        assert outcome == ['value', 3.5] and target.tolist() == [1.0, 3.5]
    return [outcome, events, len(target)]

def fallbacks():
    target = array('d', [0.0])
    events = []
    class Index:
        def __index__(self):
            events.append('index')
            return 7
    for value, expected in ((True, 1.0), (False, 0.0), (12345, 12345.0), (Index(), 7.0)):
        target[0] = value
        assert target[0] == expected
    errors = []
    for value in (None, '3.5', object(), 1 << 20000):
        before = target.tobytes()
        try:
            target[0] = value
        except (TypeError, OverflowError) as error:
            errors.append([type(error).__name__, str(error)])
        else:
            raise AssertionError(type(value))
        assert target.tobytes() == before
    assert events == ['index']
    assert [item[0] for item in errors] == ['TypeError'] * 3 + ['OverflowError']
    return [events, errors]

def observe():
    events = []
    def store(target, value):
        target[-1] = value
        return target[-1]
    def callback(frame, event, arg):
        if frame.f_code is store.__code__ and event == 'return':
            events.append([arg.hex(), frame.f_locals['value'].hex()])
        return callback
    for setter in (sys.settrace, sys.setprofile):
        setter(callback)
        try:
            assert store(array('d', [0.0]), -0.0).hex() == '-0x0.0p+0'
        finally:
            setter(None)
    assert events == [['-0x0.0p+0', '-0x0.0p+0']] * 2
    return events

def main():
    values, activity = exact_values()
    results = [values, [conversion(mode) for mode in ('normal', 'shrink', 'grow', 'raise', 'bad')],
               fallbacks(), observe()]
    print(json.dumps(dict(results=results, activity=activity)))

main()
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, '-S', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0', PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, (jit, optimize, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, off, noopt = run(True), run(False), run(True, False)
    assert candidate['results'] == off['results'] == noopt['results']
    assert all(candidate['activity']), candidate['activity']
    print('OK double-array assignment bits, parser controls, callbacks, bounds, references and observers')


if __name__ == '__main__':
    main()
