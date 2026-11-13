import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

PROGRAM = r'''
import _opcode, json, sys
class Owner:
    def __init__(self, value): self.value = value
def arithmetic(a, left, right):
    return a*a + left.value*right.value
def loop(n, left, right):
    traces = []
    active = False
    for i in range(n):
        active = sys._jit.is_active()
        first = arithmetic(1.25, left, right)
        result = arithmetic(1.25, left, right)
        if i == 6999:
            for offset in range(0, len(loop.__code__.co_code), 2):
                try: traces.append(list(_opcode.get_executor(loop.__code__, offset)))
                except (ValueError, RuntimeError): pass
    return result, traces, active
print(json.dumps(loop(8000, Owner(2.0), Owner(3.0))))
'''

FAULT = r'''
import _opcode, dis, json, sys
layout, scenario = sys.argv[1:]
if layout == 'slots':
    class Owner:
        __slots__ = ('value',)
        def __init__(self, value): self.value = value
else:
    class Owner:
        def __init__(self, value): self.value = value

events = []
class Property:
    @property
    def value(self):
        frame = sys._getframe(1)
        ins = next(i for i in dis.get_instructions(frame.f_code) if i.offset == frame.f_lasti)
        events.append([frame.f_code.co_name, ins.offset, ins.opname, sorted(frame.f_locals), frame.f_locals['right'] is self])
        if scenario == 'property error': raise ValueError('probe')
        return 7.0

namespace = {'_opcode': _opcode}
exec("""def arithmetic(a, left, right):
    return a*a + left.value*right.value
def loop(n, fn, left, source, active):
    entries = []
    was_active = False
    for index in range(n):
        left, right = source.pop()
        was_active = active()
        value = fn(1.25, left, right)
        if index == 6999:
            for offset in range(0, len(loop.__code__.co_code), 2):
                try: entries.append(list(_opcode.get_executor(loop.__code__, offset)))
                except (ValueError, RuntimeError): pass
    return value, entries, was_active
""", namespace)
fn, loop = namespace['arithmetic'], namespace['loop']
left, warm = Owner(2.0), Owner(3.0)
warm_left = left
if scenario in ('replace', 'clear'):
    fault = Owner(4.0)
    if scenario == 'replace': fault.__dict__ = {'value': 7.0}
    else: fault.__dict__.clear()
elif scenario == 'wrong owner': fault = object()
elif scenario in ('property', 'property error'): fault = Property()
elif scenario == 'wrong type': fault = Owner('wrong')
elif scenario in ('left replace', 'left clear', 'left wrong type'):
    left = Owner('wrong' if scenario == 'left wrong type' else 4.0)
    if scenario == 'left replace': left.__dict__ = {'value': 7.0}
    elif scenario == 'left clear': left.__dict__.clear()
    fault = warm
else: fault = Owner(7.0)
culprit = left if scenario.startswith('left ') else fault
source = [(left, fault)] + [(warm_left, warm)] * 7015
try:
    value, traces, active = loop(7016, fn, left, source, sys._jit.is_active)
    result = ['value', value.hex()]
except (AttributeError, ValueError, TypeError) as error:
    frames = []
    traces = []
    active = False
    tb = error.__traceback__
    while tb:
        frame = tb.tb_frame
        if frame.f_code is loop.__code__:
            active = frame.f_locals['was_active']
            traces = frame.f_locals['entries']
            index = frame.f_locals['index']
        if frame.f_code is fn.__code__:
            ins = next(i for i in dis.get_instructions(fn) if i.offset == tb.tb_lasti)
            frames.append([ins.offset, ins.opname, ins.positions.col_offset, ins.positions.end_col_offset,
                           frame.f_locals['a'].hex(), frame.f_locals['left'] is left,
                           frame.f_locals['right'] is fault, sorted(frame.f_locals)])
        tb = tb.tb_next
    result = [type(error).__name__, str(error), frames,
              [error.name, error.obj is culprit] if isinstance(error, AttributeError) else None]
else:
    index = 7015
print(json.dumps(dict(result=result, traces=traces, active=active, progress=[index, len(source)], events=events)))
'''

def run_fault(layout, scenario, jit, optimize=True):
    run = subprocess.run([sys.executable, '-s', '-c', FAULT, layout, scenario],
        env=dict(os.environ, PYTHON_JIT='1' if jit else '0', PYTHON_UOPS_OPTIMIZE='1' if optimize else '0'),
        capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, (layout, scenario, run.stderr)
    return json.loads(run.stdout)

def check_faults():
    for layout in ('slots', 'managed'):
        scenarios = ['success', 'wrong owner', 'wrong type', 'left wrong type', 'property', 'property error']
        if layout == 'managed': scenarios += ['replace', 'clear', 'left replace', 'left clear']
        for scenario in scenarios:
            jit = run_fault(layout, scenario, True)
            off = run_fault(layout, scenario, False)
            noopt = run_fault(layout, scenario, True, False)
            for key in ('result', 'progress', 'events'):
                assert jit[key] == off[key] == noopt[key], (layout, scenario, key, jit[key], off[key], noopt[key])
            assert jit['active'] and jit['progress'] == [7015, 0], (layout, scenario, jit)
            if scenario in ('success', 'replace', 'property'):
                assert jit['result'] == ['value', 15.5625.hex()], (layout, scenario, jit['result'])
            elif scenario == 'left replace':
                assert jit['result'] == ['value', 22.5625.hex()], (layout, scenario, jit['result'])
            elif scenario in ('clear', 'left clear', 'wrong owner'):
                assert jit['result'][0] == 'AttributeError' and jit['result'][3] == ['value', True], jit['result']
            elif scenario == 'property error':
                assert jit['result'][:2] == ['ValueError', 'probe'], jit['result']
            else:
                assert jit['result'][0] == 'TypeError', jit['result']
                frames = jit['result'][2]
                assert len(frames) == 1 and frames[0][1:4] == ['BINARY_OP', 17, 39], frames
                assert frames[0][4:] == [1.25.hex(), True, True, ['a', 'left', 'right']], frames
            if scenario in ('clear', 'left clear', 'wrong owner', 'property error'):
                frames = jit['result'][2]
                position = [17, 27] if scenario == 'left clear' else [28, 39]
                assert len(frames) == 1 and frames[0][1:4] == ['LOAD_ATTR', *position], frames
                assert frames[0][4:] == [1.25.hex(), True, True, ['a', 'left', 'right']], frames
            if scenario in ('property', 'property error'):
                assert len(jit['events']) == 1 and jit['events'][0][0] == 'arithmetic'
                assert jit['events'][0][2:] == ['LOAD_ATTR', ['a', 'left', 'right'], True]
            else:
                assert not jit['events']
            assert any(any(e[0] == '_BOX_UNBOXED_SLOTS' for e in trace) for trace in jit['traces']), (layout, scenario)
    print('OK masked fallback controls: slots and managed')

def main():
    incumbent = '--incumbent' in sys.argv
    run = subprocess.run([sys.executable, '-s', '-c', PROGRAM], env=dict(os.environ, PYTHON_JIT='1', PYTHON_UOPS_OPTIMIZE='1'), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    result, traces, active = json.loads(run.stdout)
    assert result == 7.5625 and active
    groups = []
    paired = False
    for trace in traces:
        regions = []
        region = None
        entry_targets = []
        for name, _, target, operand in trace:
            if name == '_BEGIN_VIRTUAL_CALL':
                assert region is None
                region = []
                groups.append(region)
                regions.append(region)
                entry_targets.append(target)
            elif name == '_RETURN_VIRTUAL_CALL':
                assert region is not None
                region = None
            elif region is not None and 0 < target < len(trace):
                dest = target
                if 'SPILL' in trace[dest][0]: dest += 1
                if dest + 2 < len(trace) and trace[dest][0] == '_BOX_UNBOXED_SLOTS':
                    assert trace[dest + 1][0] == trace[dest + 2][0] == '_MATERIALIZE_VIRTUAL_FRAME'
                    region.append((target, trace[dest][3], trace[dest + 1][3], name))
        assert region is None
        for region in regions:
            assert all(target not in entry_targets for target, *_ in region)
        if len(regions) == 2 and all(regions):
            assert len(set(entry_targets)) == 2
            shared = {x[1:3] for x in regions[0] if x[1]} & {x[1:3] for x in regions[1] if x[1]}
            if shared:
                paired = True
        for i, left in enumerate(regions):
            for right in regions[i + 1:]:
                assert {x[0] for x in left}.isdisjoint(x[0] for x in right)
    print('masked exits', groups)
    assert paired, 'no two masked regions with a shared visible recipe'
    assert any(sum(1 for x in region if x[1:3] == item[1:3]) >= 2
               for region in groups for item in region if item[1]), groups
    if incumbent:
        assert all(len({x[0] for x in region}) == len(region) for region in groups)
    else:
        assert any(a[0] == b[0] and a[1] != 0 and a[1:3] == b[1:3]
                   for region in groups for i, a in enumerate(region) for b in region[i + 1:])
    check_faults()
    for fixture in ('jit_virtual_exit_stubs_integration', 'jit_virtual_body_unbox_integration'):
        spec = importlib.util.spec_from_file_location(fixture, Path(__file__).with_name(fixture + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        saved = sys.argv
        try:
            sys.argv = [fixture]
            module.main()
        finally:
            sys.argv = saved
    print('OK masked exit stubs', 'incumbent' if incumbent else 'candidate')

if __name__ == '__main__': main()
