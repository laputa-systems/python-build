import argparse
import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import dis
import json
import sys

WARM = 7000
EVENTS = []
TRACES = []

def trace(fn):
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            entries = list(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            continue
        if any(entry[0] == '_JUMP_TO_TOP' for entry in entries):
            return entries
    return []

def nested():
    return sum((1, 2, 3))

class Old:
    def __del__(self):
        frame = sys._getframe(1)
        instruction = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        EVENTS.append([instruction.opname, instruction.argval, frame.f_locals['x'],
                       frame.f_locals['index'], frame.f_locals['active'], nested(), frame.f_lasti])
        TRACES.append(trace(loop))
        frame.f_locals['x'] = 9.0

class FloatSubclass(float):
    __del__ = Old.__del__

def loop(source, a, b):
    for index in range(len(source)):
        active = sys._jit.is_active()
        x = source.pop()
        x = a + b
    return x

def null_loop(source, active):
    for index in range(len(source) + 1):
        was_active = active()
        if index:
            x = source.pop()
        remaining = len(source)
    return x, was_active, remaining

def null_case():
    warm, _, remaining = null_loop([1.0] * WARM, sys._jit.is_active)
    assert remaining == 0
    snapshot = trace(null_loop)
    source = [3.25]
    value, entered, remaining = null_loop(source, sys._jit.is_active)
    assert remaining == len(source)
    return [warm, value, entered, len(source), snapshot, null_loop.__code__.co_varnames.index('x')]

def run(kind):
    global EVENTS
    EVENTS = []
    TRACES.clear()
    loop.__code__ = loop.__code__.replace()
    source = [kind()] + [1.0] * WARM
    result = loop(source, 1.0, 2.0)
    return [result, EVENTS, TRACES[-1], len(source)]

old = run(Old)
subclass = run(FloatSubclass)
alias = 4.0
before = sys.getrefcount(alias)
for _ in range(4):
    assert loop([alias] * 20, 1.0, 2.0) == 3.0
refdelta = sys.getrefcount(alias) - before
nonfloat = loop([object()] * 20, 1.0, 2.0)
observers = []
def observer(frame, event, arg):
    if frame.f_code is loop.__code__ and event == 'return':
        observers.append([frame.f_locals['x'], arg])
    return observer
for setter in (sys.settrace, sys.setprofile):
    setter(observer)
    loop([1.0] * 3, 1.0, 2.0)
    setter(None)
print(json.dumps({'old': old, 'subclass': subclass, 'refdelta': refdelta,
                  'null': null_case(),
                  'nonfloat': nonfloat, 'observers': observers,
                  'store_pc': [i.offset for i in dis.get_instructions(loop) if i.opname == 'STORE_FAST' and i.argval == 'x'][-1],
                  'local': loop.__code__.co_varnames.index('x')}))
'''


def run(jit, optimize):
    result = subprocess.run([sys.executable, '-s', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, (result.returncode, result.stderr)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect', choices=('baseline', 'previous', 'candidate'), default='candidate')
    args = parser.parse_args()
    candidate, off, noopt = run(True, True), run(False, True), run(True, False)
    for result in (candidate, off, noopt):
        assert result['null'][:2] == [1.0, 3.25] and result['null'][3] == 0, result['null']
        for name in ('old', 'subclass'):
            value, events, _, remaining = result[name]
            assert value == 9.0 and remaining == 0, (name, result[name])
            assert len(events) == 1 and events[0][:4] == ['STORE_FAST', 'x', 3.0, 7000], events
            assert events[0][5] == 6, events
            assert events[0][6] == result['store_pc'], events
        assert result['refdelta'] == 0 and result['nonfloat'] == 3.0
        assert result['observers'] == [[3.0, 3.0], [3.0, 3.0]], result['observers']
    for name in ('old', 'subclass'):
        assert candidate[name][1][0][4], candidate[name]
        entries = candidate[name][2]
        local = candidate['local']
        pairs = [(first, second) for first, second in zip(entries, entries[1:])
                 if first[0] == '_GUARD_LOCAL_FLOAT' and first[1] == local and
                    second[0] == '_STORE_FAST_FUSED_FLOAT' and second[1] == local]
        fused = [entry for entry in entries if entry[0] == '_STORE_FAST_FUSED_FLOAT_OR_NULL' and entry[1] == local]
        if args.expect == 'candidate':
            assert len(fused) == 2 and not pairs, entries
        elif args.expect == 'previous':
            assert len(pairs) == 2 and not fused, entries
        else:
            assert not pairs and not fused, entries
            assert any(entry[0] == '_STORE_FAST_FUSED' and entry[1] == local for entry in entries), entries
    assert candidate['null'][2], candidate['null']
    null_entries, null_local = candidate['null'][4:]
    null_pair = any(first[0] == '_GUARD_LOCAL_FLOAT' and first[1] == null_local and
                    second[0] == '_STORE_FAST_FUSED_FLOAT' and second[1] == null_local
                    for first, second in zip(null_entries, null_entries[1:]))
    null_fused = any(entry[0] == '_STORE_FAST_FUSED_FLOAT_OR_NULL' and entry[1] == null_local for entry in null_entries)
    assert null_pair == (args.expect == 'previous'), null_entries
    assert null_fused == (args.expect == 'candidate'), null_entries
    print('OK guarded local cleanup and interpreter controls')


if __name__ == '__main__':
    main()
