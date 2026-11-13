"""Full GC clears float freelists; malloc gives one failmalloc callback per slot."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def fixture(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


floats = fixture('jit_virtual_float_integration')
integers = fixture('jit_virtual_int_integration')


def bodies(entries):
    body = None
    for entry in entries:
        if entry[0] == '_BEGIN_VIRTUAL_CALL':
            assert body is None
            body = []
        elif entry[0] == '_RETURN_VIRTUAL_CALL':
            assert body is not None
            yield body
            body = None
        elif body is not None:
            body.append(entry)
    assert body is None


def check_body(entries, domain, incumbent):
    regions = list(bodies(entries))
    assert regions
    for region in regions:
        names = [entry[0] for entry in region]
        if incumbent:
            assert not any('UNBOXED' in name or name.startswith('_UNBOX_') for name in names), names
        else:
            assert any(name.startswith('_BINARY_OP_') and name.endswith(domain + '_UNBOXED') for name in names), names
            assert '_BOX_' + domain in names, names


def check_boxing_exits(entries, domain, incumbent):
    executors = []
    for entry in entries:
        if entry[0] == '_START_EXECUTOR':
            executors.append([])
        assert executors
        executors[-1].append(entry)
    found = []
    for trace in executors:
        for region in bodies(trace):
            for name, _, target, _ in region:
                if not ((name.startswith('_BINARY_OP_') and name.endswith(domain + '_UNBOXED')) or name.startswith('_GUARD_')):
                    continue
                if not 0 < target < len(trace):
                    continue
                if 'SPILL' in trace[target][0]:
                    target += 1
                if trace[target][0] != '_BOX_UNBOXED_SLOTS':
                    continue
                assert trace[target][1] > 0
                if domain == 'FLOAT':
                    assert trace[target][3] != 0
                assert trace[target + 1][0] == trace[target + 2][0] == '_MATERIALIZE_VIRTUAL_FRAME'
                found.append((trace[target][1], trace[target][3]))
    if not incumbent:
        assert found, (domain, entries)
    return found


INTEGER = r'''
import _opcode, json, sys
namespace = dict(_opcode=_opcode)
exec("""def arithmetic(a, b):
    return (a + b) * (a - b)
def loop(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        a, b = source.pop()
        was_active = active()
        result = fn(a, b)
        if index == 6999:
            for offset in range(0, len(loop.__code__.co_code), 2):
                try: entries.extend(_opcode.get_executor(loop.__code__, offset))
                except (ValueError, RuntimeError): pass
    return str(result), entries, was_active
""", namespace)
fn, loop = namespace['arithmetic'], namespace['loop']
class Subclass(int): pass
results, traces, active = {}, {}, []
for label, pair in [('warm', (12, 15)), ('multiply overflow', (2**29, 2)),
                    ('add overflow', (2**29, 2**29)), ('large', (2**70, 2)),
                    ('negative', (-2**29, 2)), ('bool', (True, 2)),
                    ('subclass', (Subclass(17), 3))]:
    source = [pair] + [(12, 15)] * 7015
    result, entries, observed = loop(7016, fn, source, sys._jit.is_active)
    results[label] = [result, len(source)]
    traces[label] = entries
    active.append(observed)
print(json.dumps(dict(results=results, traces=traces, active=active)))
'''


def run_program(program, *, jit, optimize=True, arguments=(), environment=None):
    env = dict(os.environ, PYTHON_JIT='1' if jit else '0',
               PYTHON_UOPS_OPTIMIZE='1' if optimize else '0')
    if environment:
        env.update(environment)
    completed = subprocess.run([sys.executable, '-s', '-c', program, *arguments],
        env=env,
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


PREFIX = r'''
import _opcode, json, sys
events = []
class Number:
    def __mul__(self, other):
        frame = sys._getframe(1)
        events.append(['mul', frame.f_code.co_name, frame.f_locals['b'] is self,
                       other is self, sorted(frame.f_locals)])
        return 4.0
    def __truediv__(self, other):
        frame = sys._getframe(1)
        events.append(['div', frame.f_code.co_name, frame.f_locals['b'] is self,
                       other.hex(), sorted(frame.f_locals)])
        return 2.0
namespace = dict(_opcode=_opcode)
exec("""def arithmetic(a, b):
    return (a*a+b*b)+(b/a)
def loop(n, fn, source, active):
    entries = []
    was_active = False
    for index in range(n):
        a, b = source.pop()
        was_active = active()
        result = fn(a, b)
        if index == 6999:
            for offset in range(0, len(loop.__code__.co_code), 2):
                try: entries.extend(_opcode.get_executor(loop.__code__, offset))
                except (ValueError, RuntimeError): pass
    return result.hex(), entries, was_active
""", namespace)
fn, loop = namespace['arithmetic'], namespace['loop']
source = [(1.25, Number())] + [(1.25, 2.5)] * 7015
value, entries, active = loop(7016, fn, source, sys._jit.is_active)
print(json.dumps(dict(result=[value, len(source), events], traces=entries, active=active)))
'''


FAULT = r'''
import _opcode, _testcapi, dis, gc, json, sys
WARM = 7000
events = []
class Arm:
    def __init__(self, start): self.start = start
    def __del__(self):
        events.append('armed')
        gc.collect(2)
        _testcapi.set_nomemory(self.start, self.start + 1)
def trace(fn):
    entries = []
    for offset in range(0, len(fn.__code__.co_code), 2):
        try: entries.extend(_opcode.get_executor(fn.__code__, offset))
        except (ValueError, RuntimeError): pass
    return entries
namespace = dict(WARM=WARM, trace=trace)
exec("""def arithmetic(a, b):
    return (a / b) + (b / a)
def loop(n, fn, values, arms, active):
    held = [None] * n
    alias = held
    transient = None
    entries = []
    was_active = False
    for index in range(n):
        a, b = values.pop()
        transient = arms.pop()
        was_active = active()
        held[index] = fn(a, b)
        if index == WARM - 1: entries = trace(loop)
    return held
""", namespace)
fn, loop = namespace['arithmetic'], namespace['loop']
values = [(0.0, 2.5)] + [(1.25, 2.5)] * (WARM + 1)
arms = [None, Arm(int(sys.argv[1]))] + [None] * WARM
try:
    raise ValueError('outer')
except ValueError:
    try: loop(WARM + 2, fn, values, arms, sys._jit.is_active)
    except MemoryError as error:
        _testcapi.remove_mem_hooks()
        frames = []
        active = False
        entries = []
        tb = error.__traceback__
        while tb:
            frame = tb.tb_frame
            instruction = next(i for i in dis.get_instructions(frame.f_code) if i.offset == tb.tb_lasti)
            if frame.f_code is loop.__code__:
                active = frame.f_locals['was_active']
                entries = frame.f_locals['entries']
                assert frame.f_locals['alias'] is frame.f_locals['held']
                assert frame.f_locals['index'] == WARM + 1
            elif frame.f_code is fn.__code__:
                frames.append([instruction.opname, instruction.positions.col_offset,
                    instruction.positions.end_col_offset, {k: v.hex() for k, v in frame.f_locals.items()}])
            tb = tb.tb_next
        gc.collect()
        print(json.dumps(dict(result=[type(error).__name__, str(error), str(error.__context__),
                                     frames, len(values), len(arms), events], active=active, traces=entries)))
    else: raise AssertionError('boxing or division exception allocation did not fail')
    finally: _testcapi.remove_mem_hooks()
'''


def fault(jit, start, optimize=True):
    return run_program(FAULT, jit=jit, optimize=optimize, arguments=(str(start),),
                       environment={'PYTHONMALLOC': 'malloc'})


def main():
    incumbent = '--incumbent' in sys.argv
    candidate, off, noopt = floats.run(True), floats.run(False), floats.run(True, False)
    assert candidate['results'] == off['results'] == noopt['results']
    assert candidate['active'] == [True] * 7
    for label in ('value 0', 'value 1', 'value 2', 'first', 'second', 'allocation'):
        check_body(candidate['traces'][label], 'FLOAT', incumbent)
    for label in ('value 2', 'first', 'second'):
        check_boxing_exits(candidate['traces'][label], 'FLOAT', incumbent)
    for managed in (False, True):
        candidate, off, noopt = integers.run(True, managed), integers.run(False, managed), integers.run(True, managed, False)
        assert candidate['results'] == off['results'] == noopt['results']
        assert candidate['active'] == [True] * 34
        assert candidate['results']['overflow callback'][0] == ['int', str((2**29 + 2) * 2 * 4)]
    candidate, off, noopt = run_program(INTEGER, jit=True), run_program(INTEGER, jit=False), run_program(INTEGER, jit=True, optimize=False)
    assert candidate['results'] == off['results'] == noopt['results']
    assert candidate['active'] == [True] * 7
    for label, pair in [('warm', (12, 15)), ('multiply overflow', (2**29, 2)),
                        ('add overflow', (2**29, 2**29)), ('large', (2**70, 2)),
                        ('negative', (-2**29, 2)), ('bool', (True, 2)), ('subclass', (17, 3))]:
        a, b = pair
        assert candidate['results'][label] == [str((a + b) * (a - b)), 0]
        check_body(candidate['traces'][label], 'INT', incumbent)
        check_boxing_exits(candidate['traces'][label], 'INT', incumbent)
    candidate, off, noopt = run_program(PREFIX, jit=True), run_program(PREFIX, jit=False), run_program(PREFIX, jit=True, optimize=False)
    assert candidate['result'] == off['result'] == noopt['result']
    assert candidate['result'] == [7.5625.hex(), 0, [
        ['mul', 'arithmetic', True, True, ['a', 'b']],
        ['div', 'arithmetic', True, 1.25.hex(), ['a', 'b']]]]
    assert candidate['active']
    check_body(candidate['traces'], 'FLOAT', incumbent)
    prefix_masks = check_boxing_exits(candidate['traces'], 'FLOAT', incumbent)
    if not incumbent:
        assert (3, 0b100) in prefix_masks, prefix_masks
    for start in (0, 1, 2):
        candidate, off, noopt = fault(True, start), fault(False, start), fault(True, start, False)
        assert candidate['active']
        for mode, data in (('jit', candidate), ('off', off), ('noopt', noopt)):
            result = data['result']
            assert result[:3] == ['MemoryError', '', 'None'], (start, mode, result)
            position = [12, 17] if start == 0 else [22, 27]
            assert result[3] == [['BINARY_OP', *position, {'a': 0.0.hex(), 'b': 2.5.hex()}]], (start, mode, result)
            assert result[4:] == [0, 0, ['armed']]
        check_body(candidate['traces'], 'FLOAT', incumbent)
        depths = check_boxing_exits(candidate['traces'], 'FLOAT', incumbent)
        if not incumbent:
            assert (2, 0b11) in depths, depths
            math = [entry[0] for region in bodies(candidate['traces']) for entry in region
                    if entry[0].startswith('_BINARY_OP_')]
            assert math == ['_BINARY_OP_TRUEDIV_FLOAT', '_BINARY_OP_TRUEDIV_FLOAT_UNBOXED', '_BINARY_OP_ADD_FLOAT_UNBOXED'], math
    print('OK virtual body unboxing, raw resume guards, overflow, OOM and partial boxing', 'incumbent' if incumbent else 'candidate')


if __name__ == '__main__':
    main()
