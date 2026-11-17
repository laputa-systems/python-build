import argparse
import dis
import gc
import json
import os
import struct
import subprocess
import sys

import _opcode

parser = argparse.ArgumentParser()
parser.add_argument('--expect', choices=('baseline', 'candidate', 'interpreter'), default='candidate')
parser.add_argument('--child', action='store_true')
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


def make():
    namespace = {'entries': entries}
    exec('def arithmetic(left,right,i,j,k,l):\n'
         '    return left[i]*right[j]+left[k]*right[l]\n'
         'def computed(left,right,i,j,k,l):\n'
         '    return left[i+1]*right[j]+left[k]*right[l-1]\n'
         'def loop(n,fn,source,active):\n'
         '    trace=[]\n    observed=False\n'
         '    for index in range(n):\n        left,right,i,j,k,l=source.pop()\n'
         '        observed=active()\n        value=fn(left,right,i,j,k,l)\n'
         '        if index==6999:\n            trace=entries(loop)\n'
         '    return value,trace,observed,index\n', namespace)
    return namespace['arithmetic'], namespace['computed'], namespace['loop']


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
    return {'error': [type(error).__name__, str(error), locations, error.__context__ is None],
            'trace': trace, 'active': active, 'progress': progress}


def run_case(label, computed, fault_row):
    plain_fn, computed_fn, caller = make()
    fn = computed_fn if computed else plain_fn
    left = [1.25, 2.5]
    right = [3.5, 4.0, 5.5]
    warm = (left, right, 0, 1, 1, 0)
    events = []
    fault = fault_row(left, right, events)
    source = [fault] + [warm] * (COUNT - 1)
    before = [sys.getrefcount(left), sys.getrefcount(right)]
    try:
        value, trace, active, progress = caller(COUNT, fn, source, sys._jit.is_active)
    except (IndexError, TypeError, ValueError, OverflowError) as error:
        row = capture(error, fn, caller)
    else:
        row = {'value': bits(value), 'trace': trace, 'active': active, 'progress': progress}
    gc.collect()
    row.update(events=events, remaining=len(source), refcounts=[sys.getrefcount(left)-before[0], sys.getrefcount(right)-before[1]])
    assert row['progress'] == COUNT - 1 and row['remaining'] == 0, (label, row)
    assert row['refcounts'] == [0, 0], (label, row['refcounts'])
    return row


def faults(label):
    def change(left, right, events):
        row = [left, right, 0, 1, 1, 0]
        if label.startswith('bounds'):
            row[2 + int(label[-1])] = 5
        elif label == 'negative':
            row[2:] = [-1, -1, -1, -1]
        elif label == 'nonfloat':
            row[0] = [1, 2]
        elif label == 'tuple':
            row[1] = (3.5, 4.0, 5.5)
        elif label == 'float-index':
            row[3] = 1.0
        elif label == 'large-index':
            row[3] = 2**100
        return tuple(row)
    return change


def program():
    result = {}
    for computed in ('plain', 'computed'):
        for label in ('normal', 'negative', 'bounds0', 'bounds1', 'bounds2', 'bounds3',
                      'nonfloat', 'tuple', 'float-index', 'large-index'):
            result[computed + '-' + label] = run_case(computed + '-' + label, computed == 'computed', faults(label))
    return result


if args.child:
    print(json.dumps(program(), default=str))
    raise SystemExit

runs = []
for jit, optimize in (('1', '1'), ('0', '1'), ('1', '0')):
    run = subprocess.run([sys.executable, '-S', __file__, '--child'], env=dict(os.environ,
        PYTHON_JIT=jit, PYTHON_UOPS_OPTIMIZE=optimize, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, (jit, optimize, run.stdout, run.stderr)
    runs.append(json.loads(run.stdout))

for label, row in runs[0].items():
    semantic = lambda value: {key: item for key, item in value.items() if key not in ('trace', 'active')}
    assert semantic(row) == semantic(runs[1][label]) == semantic(runs[2][label]), (label, row, runs[1][label], runs[2][label])
    if args.expect != 'interpreter':
        assert row['active'], (label, row)
    names = [entry[0] for entry in row['trace']]
    if args.expect == 'candidate':
        if label.endswith(('nonfloat', 'tuple', 'float-index', 'large-index')):
            continue
        unboxed = [name for name in names
                   if name in ('_BINARY_OP_SUBSCR_LIST_INT_UNBOXED', '_BINARY_OP_SUBSCR_LIST_INT_UNBOXED_INDEX')]
        assert unboxed, (label, names)

assert runs[0]['plain-normal']['value'] == bits(13.75)
assert runs[0]['computed-normal']['value'] == bits(23.75)
for label in ('bounds0', 'bounds1', 'bounds2', 'bounds3'):
    for computed in ('plain', 'computed'):
        row = runs[0][computed + '-' + label]
        assert 'error' in row and row['error'][0] in ('IndexError', 'TypeError', 'OverflowError'), (label, row)
for label in ('float-index', 'large-index'):
    for computed in ('plain', 'computed'):
        row = runs[0][computed + '-' + label]
        assert 'error' in row and row['error'][0] in ('TypeError', 'IndexError', 'OverflowError'), (label, row)
print('OK unboxed list float reads', args.expect)
