import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
from array import array
import gc
import json
import sys
import tracemalloc

COUNT = 7016

def trace(fn):
    entries=[]
    for offset in range(0,len(fn.__code__.co_code),2):
        try: entries.extend(_opcode.get_executor(fn.__code__,offset))
        except (ValueError,RuntimeError): pass
    return entries

def exercise(dtype, phase):
    label=dtype+'_'+phase
    callee={}
    exec(compile('def arithmetic(values):\n    return values[0]\n', 'allocation_'+label+'_callee.py','exec'),callee)
    fn=callee['arithmetic']
    namespace={}
    exec(compile('def loop(n,fn,source,active):\n'
                 '    held=[None]*n\n'
                 '    for index in range(n):\n'
                 '        entered=active()\n'
                 '        values,transient=source.pop()\n'
                 '        observed=active()\n'
                 '        held[index]=fn(values)\n'
                 '    return held,entered,observed\n', 'allocation_'+label+'_caller.py','exec'),namespace)
    loop=namespace['loop']
    value=12345.25 if dtype=='d' else 12345
    values=array(dtype,[value])
    reserves=[]
    entered_callback=[]
    class Start:
        def __del__(self):
            frame=sys._getframe(1)
            if frame.f_code is not loop.__code__: return
            entered_callback.append(frame.f_locals['entered'])
            tracemalloc.start(16)
            gc.collect(2)
            reserves.append([float(index) for index in range(512)])
    try:
        if phase=='before': tracemalloc.start(16)
        source=[(values,None)]*COUNT
        if phase=='inside': source[1]=(values,Start())
        warm,entered,observed=loop(COUNT,fn,source,sys._jit.is_active)
        assert not source and len(warm)==COUNT and all(item==value for item in warm)
        warm_trace=[entry[0] for entry in trace(loop)]
        if phase=='after': tracemalloc.start(16)
        gc.collect(2)
        reserves.append([float(index) for index in range(512)])
        held,_,traced_active=loop(128,fn,[(values,None)]*128,sys._jit.is_active)
        expected=(fn.__code__.co_filename,2)
        sites=[]
        if phase=='inside':
            allocation=tracemalloc.get_object_traceback(warm[-1])
            actual=[(frame.filename,frame.lineno) for frame in allocation] if allocation else []
            sites.append(actual)
        for result in held:
            assert result==value
            allocation=tracemalloc.get_object_traceback(result)
            actual=[(frame.filename,frame.lineno) for frame in allocation] if allocation else []
            sites.append(actual)
        missing=[site for site in sites if expected not in site]
        if phase=='inside': assert len(entered_callback)==1
        if phase!='inside': assert not entered_callback
    finally:
        tracemalloc.stop()
    after,_,resumed=loop(COUNT,fn,[(values,None)]*COUNT,sys._jit.is_active)
    assert len(after)==COUNT and all(result==value for result in after)
    return dict(expected=expected,missing=missing,checked=len(sites),warm_active=observed,
                entered=entered,callback_active=entered_callback,traced_active=traced_active,
                resumed_active=resumed,traces=warm_trace)

results={dtype+'_'+phase:exercise(dtype,phase) for dtype in ('d','i') for phase in ('before','after','inside')}
print(json.dumps(results))
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
    for control in (off, noopt):
        assert all(not row['missing'] for row in control.values()), control
    failures = {name: row['missing'][:1] for name, row in candidate.items() if row['missing']}
    for name, row in candidate.items():
        assert row['resumed_active'], (name, row)
        assert row['checked'] == (129 if name.endswith('inside') else 128)
        if not name.endswith('before'):
            assert row['entered'], (name, row)
    if '--incumbent' in sys.argv:
        assert failures, candidate
        print('REPRODUCED incumbent allocation-site mismatch:', json.dumps(failures))
        return
    assert not failures, failures
    assert all(not row['traced_active'] for row in candidate.values()), candidate
    assert all(candidate[dtype+'_inside']['callback_active'] == [True] for dtype in ('d', 'i'))
    print('OK array allocation sites, cold/warm/callback tracing, progress and JIT resumption')


if __name__ == '__main__':
    main()
