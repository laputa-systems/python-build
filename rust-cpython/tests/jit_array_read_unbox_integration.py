import argparse
import _opcode
from array import array
import dis
import gc
import itertools
import json
import os
import struct
import subprocess
import sys
import weakref

parser = argparse.ArgumentParser()
parser.add_argument('--expect', choices=('baseline', 'candidate', 'interpreter'), default='candidate')
parser.add_argument('--child', action='store_true')
parser.add_argument('--oom', choices=('result0', 'exit0', 'exit1'))
args = parser.parse_args()
COUNT = 7016


def bits(value):
    return struct.pack('!d', value).hex()


def entries(fn):
    result = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try:
            result.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError):
            pass
    return result


def make(owned=False, transient=False):
    namespace = {'entries': entries}
    expression = 'make()[i]*right[j]+right[k]*right[l]' if owned else 'left[i]*right[j]+left[k]*right[l]'
    first = 'make' if owned else 'left'
    tail = ', transient' if transient else ''
    exec('def arithmetic(' + first + ',right,i,j,k,l):\n    return ' + expression + '\n'
         'def loop(n,fn,source,active):\n    trace=[]\n    observed=False\n'
         '    for index in range(n):\n        left,right,i,j,k,l' + tail + '=source.pop()\n'
         '        observed=active()\n        value=fn(left,right,i,j,k,l)\n'
         '        if index==6999:\n            trace=entries(loop)\n'
         '    return value,trace,observed,index\n', namespace)
    return namespace['arithmetic'], namespace['loop']


def capture(error, fn, caller):
    locations = []
    trace, active, progress = [], False, -1
    tb = error.__traceback__
    while tb:
        frame = tb.tb_frame
        if frame.f_code is fn.__code__:
            ins = next(item for item in dis.get_instructions(fn) if item.offset == tb.tb_lasti)
            locations.append([ins.offset, ins.opname, ins.argrepr, sorted(frame.f_locals)])
        if frame.f_code is caller.__code__:
            trace, active, progress = (frame.f_locals[key] for key in ('trace', 'observed', 'index'))
        tb = tb.tb_next
    return dict(error=[type(error).__name__, str(error), locations, error.__context__ is None],
                trace=trace, active=active, progress=progress)


def run_case(label, change):
    fn, caller = make()
    left, right = array('d', [1.25, 2.5]), array('d', [3.5, 4.0])
    warm = (left, right, 0, 1, 1, 0)
    events = []
    fault = change(fn, left, right, events)
    source = [fault] + [warm] * (COUNT - 1)
    before = [sys.getrefcount(left), sys.getrefcount(right)]
    try:
        value, trace, active, progress = caller(COUNT, fn, source, sys._jit.is_active)
    except (IndexError, TypeError, ValueError) as error:
        row = capture(error, fn, caller)
    else:
        row = dict(value=bits(value), trace=trace, active=active, progress=progress)
    gc.collect()
    row.update(events=events, remaining=len(source), refcounts=[sys.getrefcount(left)-before[0], sys.getrefcount(right)-before[1]])
    assert row['progress'] == COUNT - 1 and row['remaining'] == 0, (label, row)
    assert row['refcounts'] == [0, 0], (label, row['refcounts'])
    return row


def fault(label):
    def change(fn, left, right, events):
        row = [left, right, 0, 1, 1, 0]
        if label.startswith('bounds'):
            row[2 + int(label[-1])] = 2
        elif label == 'negative':
            row[2:] = [-2, -1, -1, -2]
        elif label == 'large':
            row[3] = 2**100
        elif label == 'bool':
            row[3] = True
        elif label == 'float':
            row[3] = 1.0
        elif label == 'list':
            row[1] = [3.5, 4.0]
        elif label == 'int-array':
            row[1] = array('i', [3, 4])
        elif label == 'single-array':
            row[1] = array('f', [3.5, 4.0])
        elif label in ('index', 'index-error', 'index-bad', 'resize'):
            class Index:
                def __index__(self):
                    frame = sys._getframe(1)
                    ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
                    events.append([frame.f_code is fn.__code__, ins.offset, ins.opname, ins.argrepr, sorted(frame.f_locals)])
                    if label == 'index-error':
                        raise ValueError('index probe')
                    if label == 'index-bad':
                        return []
                    if label == 'resize':
                        del right[1:]
                    return 1
            row[3] = Index()
        elif label == 'subclass':
            class SubArray(array):
                def __getitem__(self, index):
                    frame = sys._getframe(1)
                    ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
                    events.append([frame.f_code is fn.__code__, ins.offset, ins.opname, ins.argrepr, sorted(frame.f_locals)])
                    return super().__getitem__(index)
            row[1] = SubArray('d', [3.5, 4.0])
        elif label == 'alias':
            row[1] = left
        elif label == 'resized':
            row[1] = array('d', [3.5])
        return tuple(row)
    return change


def owned_case():
    fn, caller = make(owned=True)
    events, references = [], []
    def cleared(reference):
        frame = sys._getframe(1)
        ins = next(item for item in dis.get_instructions(frame.f_code) if item.offset == frame.f_lasti)
        events.append([frame.f_code is fn.__code__, ins.offset, ins.opname, ins.argrepr, sorted(frame.f_locals)])
    class Factory:
        def __init__(self):
            self.calls = 0
        def __call__(self):
            self.calls += 1
            value = array('d', [1.25, 2.5])
            if self.calls == COUNT:
                references.append(weakref.ref(value, cleared))
            return value
    factory = Factory()
    right = array('d', [3.5, 4.0])
    source = [(factory, right, 0, 1, 0, 1)] * COUNT
    value, trace, active, progress = caller(COUNT, fn, source, sys._jit.is_active)
    assert value == 19.0 and factory.calls == COUNT and not source
    assert len(events) == 1 and events[0][0] and events[0][2:4] == ['BINARY_OP', '[]'], events
    assert len(references) == 1 and references[0]() is None
    return dict(value=bits(value), trace=trace, active=active, progress=progress, events=events,
                remaining=len(source), calls=factory.calls)


def observer(mode, before):
    fn, caller = make()
    left, right = array('d', [1.25, 2.5]), array('d', [3.5, 4.0])
    row = (left, right, 0, 1, 1, 0)
    events = []
    def trace(frame, event, arg):
        if frame.f_code is fn.__code__ and event in ('call', 'return'):
            events.append([event, sorted(frame.f_locals), bits(arg) if event == 'return' else None])
        return trace
    monitoring = sys.monitoring
    if mode == 'trace':
        enable = lambda: sys.settrace(trace)
        disable = lambda: sys.settrace(None)
    elif mode == 'profile':
        enable = lambda: sys.setprofile(trace)
        disable = lambda: sys.setprofile(None)
    else:
        tool = 5
        def started(code, offset):
            if code is fn.__code__:
                events.append(['call', None, None])
        def returned(code, offset, value):
            if code is fn.__code__:
                events.append(['return', None, bits(value)])
        def enable():
            monitoring.use_tool_id(tool, 'array-read-unbox')
            monitoring.register_callback(tool, monitoring.events.PY_START, started)
            monitoring.register_callback(tool, monitoring.events.PY_RETURN, returned)
            monitoring.set_events(tool, monitoring.events.PY_START | monitoring.events.PY_RETURN)
        def disable():
            monitoring.set_events(tool, 0)
            monitoring.register_callback(tool, monitoring.events.PY_START, None)
            monitoring.register_callback(tool, monitoring.events.PY_RETURN, None)
            monitoring.free_tool_id(tool)
    if not before:
        caller(COUNT, fn, [row] * COUNT, sys._jit.is_active)
    enable()
    try:
        value, _, _, _ = caller(4, fn, [row] * 4, sys._jit.is_active)
    finally:
        disable()
    names = None if mode == 'monitoring' else sorted(fn.__code__.co_varnames)
    assert value == 13.75 and events == [['call', names, None], ['return', names, bits(value)]] * 4
    return events


def program():
    result = {}
    for label in ('normal', 'negative', 'alias', 'bounds0', 'bounds1', 'bounds2', 'bounds3', 'large',
                  'bool', 'float', 'list', 'int-array', 'single-array', 'subclass', 'index',
                  'index-error', 'index-bad', 'resize', 'resized'):
        result[label] = run_case(label, fault(label))
    for label, a, b in (
            ('zero', [-0.0, 0.0], [1.0, -1.0]),
            ('infinity', [float('inf'), -1.0], [1.0, 2.0]),
            ('nan', [float('nan'), 2.5], [3.5, 4.0]),
            ('small', [5e-324, -5e-324], [1.0, -1.0]),
            ('large-float', [1e308, 1e-308], [1e308, 1e-308])):
        result[label] = run_case(label, lambda fn, left, right, events, a=a, b=b: (array('d', a), array('d', b), 0, 1, 1, 0))
    result['owned'] = owned_case()
    result['observers'] = [observer(mode, before) for mode, before in itertools.product(('trace', 'profile', 'monitoring'), (False, True))]
    result['allocation_observers'] = [allocation_observer(before) for before in (False, True)]
    return result


def allocation_observer(before):
    import tracemalloc
    namespace = {}
    exec(compile('def arithmetic(left,right,i,j,k,l):\n    return left[i]*right[j]+left[k]*right[l]\n', 'array_allocation_callee.py', 'exec'), namespace)
    fn = namespace['arithmetic']
    caller_namespace = {}
    exec(compile('def loop(n,fn,left,right,active):\n    held=[None]*n\n    for index in range(n):\n        observed=active()\n        held[index]=fn(left,right,0,1,1,0)\n    return held,observed\n', 'array_allocation_caller.py', 'exec'), caller_namespace)
    caller = caller_namespace['loop']
    left, right = array('d', [1.25, 2.5]), array('d', [3.5, 4.0])
    if before:
        tracemalloc.start(16)
    warm, _ = caller(COUNT, fn, left, right, sys._jit.is_active)
    if not before:
        tracemalloc.start(16)
    try:
        gc.collect(2)
        reserve = [float(index) for index in range(512)]
        held, _ = caller(128, fn, left, right, sys._jit.is_active)
        for value in held:
            trace = tracemalloc.get_object_traceback(value)
            actual = [(frame.filename, frame.lineno) for frame in trace] if trace else []
            assert (fn.__code__.co_filename, 2) in actual, (before, actual)
            assert value == 13.75
        assert len(warm) == COUNT and len(reserve) == 512
    finally:
        tracemalloc.stop()
    return [len(held), bits(held[-1]), True]


def oom_program():
    import _testcapi
    fn, caller = make(transient=True)
    left, right = array('d', [1.25, 2.5]), array('d', [3.5, 4.0])
    warm = (left, right, 0, 1, 1, 0, None)
    selected = 1 if args.oom == 'exit1' else 0
    class Arm:
        def __del__(self):
            if sys._getframe(1).f_code is not caller.__code__:
                return
            gc.collect(2)
            _testcapi.set_nomemory(selected, selected + 1)
    fault_row = (left, right, 0, 1, 1, 0 if args.oom == 'result0' else 2, None)
    source = [fault_row] + [warm] * (COUNT - 1)
    source[1] = (*warm[:-1], Arm())
    try:
        caller(COUNT, fn, source, sys._jit.is_active)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        result = capture(error, fn, caller)
    else:
        raise AssertionError('allocation hook did not fail')
    finally:
        _testcapi.remove_mem_hooks()
    locations = result['error'][2]
    assert len(locations) == 1 and result['progress'] == COUNT - 1 and not source, result
    ins = list(dis.get_instructions(fn))
    reads = [item.offset for item in ins if item.opname == 'BINARY_OP' and item.argrepr == '[]']
    addition = next(item.offset for item in ins if item.opname == 'BINARY_OP' and item.argrepr == '+')
    expected = (addition if args.oom == 'result0' else reads[3]) if args.expect == 'candidate' else reads[selected]
    assert locations[0][0] == expected and locations[0][1] == 'BINARY_OP', (args.oom, args.expect, locations, expected)
    assert result['error'][3]
    if args.expect != 'interpreter':
        assert result['active'], result
    return result


if args.oom:
    print(json.dumps(oom_program()))
    raise SystemExit
if args.child:
    print(json.dumps(program()))
    raise SystemExit

runs = []
for jit, optimize in (('1', '1'), ('0', '1'), ('1', '0')):
    run = subprocess.run([sys.executable, '-S', __file__, '--child'], env=dict(os.environ,
        PYTHON_JIT=jit, PYTHON_UOPS_OPTIMIZE=optimize, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, (jit, optimize, run.stdout, run.stderr)
    runs.append(json.loads(run.stdout))
for label, row in runs[0].items():
    if label in ('observers', 'allocation_observers'):
        assert row == runs[1][label] == runs[2][label]
        continue
    semantic = lambda value: {key: item for key, item in value.items() if key not in ('trace', 'active')}
    assert semantic(row) == semantic(runs[1][label]) == semantic(runs[2][label]), (label, row, runs[1][label], runs[2][label])
    if args.expect != 'interpreter':
        assert row['active'], (label, row)
    if label != 'owned':
        names = [entry[0] for entry in row['trace']]
        if args.expect == 'candidate':
            reads = [entry for entry in row['trace'] if entry[0] == '_BINARY_OP_SUBSCR_ARRAY_D_UNBOXED']
            assert len(reads) == 4 and names.count('_BINARY_OP_MULTIPLY_FLOAT_UNBOXED') == 2 and names.count('_BINARY_OP_ADD_FLOAT_UNBOXED') == 1, (label, names)
            assert names.count('_BOX_FLOAT') == 1 and not any(name.startswith('_UNBOX_FLOAT') for name in names), (label, names)
            for ordinal, mask in ((1, 4), (2, 4), (3, 12)):
                destination = row['trace'][reads[ordinal][2]:reads[ordinal][2] + 4]
                boxes = [entry for entry in destination if entry[0] == '_BOX_UNBOXED_SLOTS']
                assert len(boxes) == 1 and boxes[0][3] == mask, (label, ordinal, destination)
        elif args.expect == 'baseline':
            assert names.count('_BINARY_OP_SUBSCR_ARRAY_D') == 4 and '_BINARY_OP_SUBSCR_ARRAY_D_UNBOXED' not in names, (label, names)
    elif args.expect != 'interpreter':
        assert any(entry[0] == '_BINARY_OP_SUBSCR_ARRAY_D' for entry in row['trace']), row['trace']
assert runs[0]['normal']['value'] == bits(13.75)
for ordinal in range(4):
    error = runs[0]['bounds' + str(ordinal)]['error']
    assert error[0] == 'IndexError' and len(error[2]) == 1 and error[2][0][1:3] == ['BINARY_OP', '[]'] and error[3], error
for label in ('index', 'index-error', 'index-bad', 'resize'):
    events = runs[0][label]['events']
    assert len(events) == 1 and events[0][0] and events[0][2:4] == ['BINARY_OP', '[]'], (label, events)
assert len(runs[0]['subclass']['events']) == 2
for case in ('result0', 'exit0', 'exit1'):
    run = subprocess.run([sys.executable, '-S', __file__, '--oom=' + case, '--expect=' + args.expect], env=dict(os.environ,
        PYTHONDONTWRITEBYTECODE='1', PYTHONMALLOC='malloc'), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, (case, run.stdout, run.stderr)
print('OK borrowed double-array unboxing', args.expect, 'bits, masks, fallback, ownership, callbacks, observers and OOM')
