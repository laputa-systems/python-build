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
import weakref

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

def callback_transition(kind):
    callbacks=[]
    reserves=[]
    def start():
        frame=sys._getframe(1)
        while frame.f_code is not loop.__code__: frame=frame.f_back
        callbacks.append(frame.f_locals.get('entered'))
        tracemalloc.start(16)
        gc.collect(2)
        reserves.append([float(index) for index in range(512)])
    class Start:
        def __init__(self, armed): self.armed=armed
        def __del__(self):
            if self.armed: start()
    namespace={}
    if kind=='physical':
        body='def call(value,a,b,sink):\n    sink.append(None)\n    return None\n'
    elif kind=='weakref':
        body='def call(factory,a,b,sink):\n    first=factory()[0]\n    return (first,sink[0])\n'
    else:
        body='def call(value,a,b,sink):\n    return (a+b)*b\n'
    exec(compile(body,'allocation_'+kind+'_return.py','exec'),namespace)
    fn=namespace['call']
    caller={}
    exec(compile('def loop(n,fn,source,values,sink,active):\n'
                 '    held=[None]*n\n'
                 '    results=[None]*n\n'
                 '    for index in range(n):\n'
                 '        entered=active()\n'
                 '        results[index]=fn(source.pop(),1.25,2.5,sink)\n'
                 '        held[index]=values[0]\n'
                 '    return held,entered\n','allocation_'+kind+'_return_caller.py','exec'),caller)
    loop=caller['loop']
    values=array('d',[12345.25])
    source=[] if kind=='weakref' else [Start(True)]+[Start(False) for _ in range(COUNT-1)]
    sink=[]
    if kind=='weakref':
        arrays=[array('d',[12345.25]) for _ in range(COUNT)]
        refs=[weakref.ref(item) for item in arrays[1:]]
        refs.append(weakref.ref(arrays[0],lambda ref:start()))
        def factory(): return arrays.pop()
        source=[factory]*COUNT
        sink=values
    try:
        held,entered=loop(COUNT,fn,source,values,sink,sys._jit.is_active)
        assert not source and len(callbacks)==1, (kind,callbacks)
        site=tracemalloc.get_object_traceback(held[-1])
        actual=[(frame.filename,frame.lineno) for frame in site] if site else []
        expected=(loop.__code__.co_filename,7)
        missing=[] if expected in actual else [actual]
        entries=[entry[0] for entry in trace(loop)]
    finally:
        tracemalloc.stop()
    if kind=='weakref':
        assert not arrays and all(ref() is None for ref in refs)
        return dict(missing=missing,callbacks=callbacks,entered=entered,resumed=None,traces=entries)
    after,resumed=loop(COUNT,fn,[Start(False) for _ in range(COUNT)],values,sink,sys._jit.is_active)
    assert all(result==12345.25 for result in held+after)
    return dict(missing=missing,callbacks=callbacks,entered=entered,resumed=resumed,traces=entries)

results={dtype+'_'+phase:exercise(dtype,phase) for dtype in ('d','i') for phase in ('before','after','inside')}
results.update({kind:callback_transition(kind) for kind in ('physical','virtual','weakref')})
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
    assert '_RETURN_VALUE' in candidate['physical']['traces'], candidate['physical']
    assert '_RETURN_VIRTUAL_CALL' in candidate['virtual']['traces'], candidate['virtual']
    assert '_BEGIN_VIRTUAL_CALL' in candidate['virtual']['traces'], candidate['virtual']
    failures = {name: row['missing'][:1] for name, row in candidate.items() if row['missing']}
    for name, row in candidate.items():
        if name in ('physical','virtual','weakref'):
            assert row['entered'] and row['resumed'] is not False and row['callbacks']==[True], (name,row)
            continue
        assert row['resumed_active'], (name, row)
        assert row['checked'] == (129 if name.endswith('inside') else 128)
        if not name.endswith('before'):
            assert row['entered'], (name, row)
    if '--incumbent' in sys.argv:
        assert failures, candidate
        print('REPRODUCED incumbent allocation-site mismatch:', json.dumps(failures))
        return
    assert not failures, failures
    assert all(not row['traced_active'] for name,row in candidate.items() if name not in ('physical','virtual','weakref')), candidate
    assert all(candidate[dtype+'_inside']['callback_active'] == [True] for dtype in ('d', 'i'))
    print('OK array allocation sites, cold/warm/callback tracing, progress and JIT resumption')


if __name__ == '__main__':
    main()
