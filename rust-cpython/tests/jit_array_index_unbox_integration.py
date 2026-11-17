import _opcode
import argparse
import dis
import gc
import json
import os
import struct
import subprocess
import sys
from array import array

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
         '    return left[i+1]*right[j]+left[k]*right[l-1]\n'
         'def int_arithmetic(left,right,i,j,k,l):\n'
         '    return left[i+1]*right[j]+left[k]*right[l-1]\n'
         'def loop(n,fn,source,active):\n'
         '    trace=[]\n    observed=False\n'
         '    for index in range(n):\n        left,right,i,j,k,l=source.pop()\n'
         '        observed=active()\n        value=fn(left,right,i,j,k,l)\n'
         '        if index==6999:\n            trace=entries(loop)\n'
         '    return value,trace,observed,index\n', namespace)
    return namespace['arithmetic'], namespace['int_arithmetic'], namespace['loop']


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


def run_case(label, kind, fault_row):
    float_fn, int_fn, caller = make()
    fn = int_fn if kind == 'int' else float_fn
    code = 'i' if kind == 'int' else 'd'
    left = array(code, [1, 2] if kind == 'int' else [1.25, 2.5])
    right = array(code, [3, 4, 5] if kind == 'int' else [3.5, 4.0])
    warm = (left, right, 0, 1, 1, 0)
    events = []
    fault = fault_row(kind, left, right, events)
    source = [fault] + [warm] * (COUNT - 1)
    before = [sys.getrefcount(left), sys.getrefcount(right)]
    try:
        value, trace, active, progress = caller(COUNT, fn, source, sys._jit.is_active)
    except (IndexError, TypeError, ValueError, OverflowError) as error:
        row = capture(error, fn, caller)
    else:
        row = {'value': value if kind == 'int' else bits(value), 'trace': trace, 'active': active, 'progress': progress}
    gc.collect()
    row.update(events=events, remaining=len(source), refcounts=[sys.getrefcount(left)-before[0], sys.getrefcount(right)-before[1]])
    assert row['progress'] == COUNT - 1 and row['remaining'] == 0, (label, row)
    assert row['refcounts'] == [0, 0], (label, row['refcounts'])
    return row


def faults(label):
    def change(kind, left, right, events):
        row = [left, right, 0, 1, 1, 0]
        if label.startswith('bounds'):
            row[2 + int(label[-1])] = 4
        elif label == 'negative':
            row[2:] = [-1, -1, -1, -1]
        elif label == 'large':
            row[3] = 2**100
        elif label == 'float-index':
            row[3] = 1.0
        elif label == 'wrong-typecode':
            row[1] = array('f' if kind == 'int' else 'i', [3.5, 4.0] if kind == 'int' else [3, 4])
        elif label == 'list':
            row[1] = [3.5, 4.0]
        return tuple(row)
    return change


def program():
    result = {}
    for kind in ('float', 'int'):
        for label in ('normal', 'negative', 'bounds0', 'bounds1', 'bounds2', 'bounds3',
                      'large', 'float-index', 'wrong-typecode', 'list'):
            result[kind + '-' + label] = run_case(kind + '-' + label, kind, faults(label))
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
    if args.expect == 'candidate' and not label.endswith(('wrong-typecode', 'list')):
        if label.startswith('int-'):
            assert '_BINARY_OP_SUBSCR_ARRAY_I_UNBOXED' in names, (label, names)
        elif label.startswith('float-'):
            assert '_BINARY_OP_SUBSCR_ARRAY_D_UNBOXED_INDEX' in names, (label, names)

assert runs[0]['float-normal']['value'] == bits(20.0)
assert runs[0]['float-negative']['value'] == bits(13.75)
assert runs[0]['int-normal']['value'] == 18
assert runs[0]['int-negative']['value'] == 13
for label in ('bounds0', 'bounds1', 'bounds2', 'bounds3'):
    for kind in ('float', 'int'):
        error = runs[0][kind + '-' + label]['error']
        assert error[0] in ('IndexError', 'OverflowError') and len(error[2]) == 1 and error[2][0][1:3] == ['BINARY_OP', '[]'] and error[3], error
print('OK unboxed array indexes and int-array reads', args.expect)
