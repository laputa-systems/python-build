import json
import os
import subprocess
import sys


PROGRAM = r'''
import _opcode
import json
import sys
import tracemalloc

WARM = 10000
RESULTS = {}
TRACES = {}
ACTIVE = {}

def executors(function):
    entries = []
    for offset in range(0, len(function.__code__.co_code), 2):
        try:
            entries.extend(entry[0] for entry in _opcode.get_executor(function.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return entries

def build(label, expression):
    callee = {}
    exec(compile('def arithmetic(a, b):\n    return ' + expression + '\n',
                 'tracemalloc_' + label + '_callee.py', 'exec'), callee)
    caller = {}
    exec(compile('def loop(n, fn, source, active):\n'
                 '    held = [None] * n\n'
                 '    was_active = False\n'
                 '    for index in range(n):\n'
                 '        a, b = source.pop()\n'
                 '        was_active = active()\n'
                 '        held[index] = fn(a, b)\n'
                 '    return held, was_active\n',
                 'tracemalloc_' + label + '_caller.py', 'exec'), caller)
    return callee['arithmetic'], caller['loop']

def source(count):
    return [(1.25 + (i % 7), 2.5 + (i % 5)) for i in range(count)]

def exercise(label, expression, before):
    fn, loop = build(label, expression)
    expected = (lambda a, b: a + b) if expression == 'a + b' else (lambda a, b: (a + b) * b)
    if before:
        tracemalloc.start(8)
    pairs = source(WARM)
    warm_inputs = pairs.copy()
    values = [expected(a, b) for a, b in reversed(pairs)]
    held, active = loop(WARM, fn, pairs, sys._jit.is_active)
    assert not pairs and held == values, (label, 'warm progress')
    ACTIVE[label] = active
    TRACES[label] = executors(loop)
    if not before:
        tracemalloc.start(8)
    pairs = source(128)
    traced_inputs = pairs.copy()
    values = [expected(a, b) for a, b in reversed(pairs)]
    # Float allocation can reuse objects freed while preparing inputs and expectations.
    reserve = [float(i) for i in range(512)]
    traced, _ = loop(128, fn, pairs, sys._jit.is_active)
    assert not pairs and traced == values, (label, 'traced progress')
    filename = fn.__code__.co_filename
    frames = []
    for value in traced:
        allocation = tracemalloc.get_object_traceback(value)
        frame = (filename, 2)
        actual = [(item.filename, item.lineno) for item in allocation] if allocation else []
        assert frame in actual, (label, frame, actual)
        frames.append(frame in actual)

    observed = []
    def observer(frame, event, arg):
        if frame.f_code is fn.__code__ and event == 'return':
            observed.append([frame.f_locals['a'].hex(), frame.f_locals['b'].hex(), arg.hex()])
        return observer
    observers = {}
    for kind, setter in [('profile', sys.setprofile), ('trace', sys.settrace)]:
        observed.clear()
        setter(observer)
        observed_pairs = source(8)
        try:
            loop(8, fn, observed_pairs, sys._jit.is_active)
        finally:
            setter(None)
        assert not observed_pairs
        observers[kind] = observed.copy()
    observed.clear()
    def returned(code, offset, value):
        if code is fn.__code__:
            frame = sys._getframe(1)
            observed.append([frame.f_locals['a'].hex(), frame.f_locals['b'].hex(), value.hex()])
    sys.monitoring.use_tool_id(4, 'tracemalloc integration')
    sys.monitoring.register_callback(4, sys.monitoring.events.PY_RETURN, returned)
    sys.monitoring.set_events(4, sys.monitoring.events.PY_RETURN)
    observed_pairs = source(8)
    try:
        loop(8, fn, observed_pairs, sys._jit.is_active)
    finally:
        sys.monitoring.set_events(4, 0)
        sys.monitoring.free_tool_id(4)
    assert not observed_pairs
    observers['monitor'] = observed.copy()
    expected_events = [[a.hex(), b.hex(), expected(a, b).hex()] for a, b in reversed(source(8))]
    assert all(events == expected_events for events in observers.values()), (label, observers)
    tracemalloc.stop()
    pairs = source(128)
    values = [expected(a, b) for a, b in reversed(pairs)]
    after, _ = loop(128, fn, pairs, sys._jit.is_active)
    assert not pairs and after == values, (label, 'post-tracing progress')
    RESULTS[label] = [len(held), len(warm_inputs), len(traced_inputs),
                      len(reserve), len(frames), len(after), observers]

for shape, expression in [('leaf', 'a + b'), ('virtual', '(a + b) * b')]:
    for timing in ('before', 'after'):
        exercise(shape + '_' + timing, expression, timing == 'before')
print(json.dumps({'results': RESULTS, 'traces': TRACES, 'active': ACTIVE}, sort_keys=True))
'''


def run(jit, optimize=True):
    completed = subprocess.run([sys.executable, '-s', '-c', PROGRAM],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                 PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, (jit, optimize, completed.returncode, completed.stderr)
    return json.loads(completed.stdout)


def main():
    candidate, reference, noopt = run(True), run(False), run(True, False)
    assert candidate['results'] == reference['results'] == noopt['results']
    for shape, op in [('leaf', '_CALL_PY_FLOAT_LEAF'), ('virtual', '_BEGIN_VIRTUAL_CALL')]:
        label = shape + '_after'
        assert candidate['active'][label], (label, candidate['active'])
        assert op in candidate['traces'][label], (label, candidate['traces'][label])
        assert op not in noopt['traces'][label], (label, noopt['traces'][label])
    print('OK    tracemalloc preserves fused callee allocation frames and observers')
    return 0


if __name__ == '__main__':
    sys.exit(main())
