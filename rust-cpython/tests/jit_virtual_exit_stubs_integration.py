"""Executor jump indices restart at each _START_EXECUTOR entry."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

spec = importlib.util.spec_from_file_location('attributes', Path(__file__).with_name('jit_virtual_argument_attributes_integration.py'))
attributes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attributes)

def run(jit, managed, optimize=True):
    data = attributes.run(jit, managed, optimize)
    for label, entries in data['traces'].items():
        executors = []
        for entry in entries:
            if entry[0] == '_START_EXECUTOR': executors.append([])
            assert executors, (label, entry)
            executors[-1].append(entry)
        data['traces'][label] = executors
    return data

def exits(trace):
    inside = False
    for index, entry in enumerate(trace):
        name, _, target, _ = entry
        if name == '_RETURN_VIRTUAL_CALL': inside = False
        if inside and ('GUARD' in name or name.startswith(('_CHECK_', '_LOAD_ATTR_'))):
            if 0 <= target < len(trace):
                destination = target
                if 'SPILL' in trace[destination][0]: destination += 1
                if destination < len(trace) and trace[destination][0].startswith('_MATERIALIZE_VIRTUAL_FRAME'):
                    yield index, target, trace[destination][3], name
        if name == '_BEGIN_VIRTUAL_CALL': inside = True

TWO_CALLS = r"""
import _opcode, json, sys
class Owner:
    def __init__(self): self.value = 1.5
def callee(owner): return owner.value + 1.0
def loop(n, owner):
    traces = []
    active = False
    for i in range(n):
        active = sys._jit.is_active()
        first = callee(owner)
        second = callee(owner)
        if i == 6999:
            for offset in range(0, len(loop.__code__.co_code), 2):
                try: traces.append(list(_opcode.get_executor(loop.__code__, offset)))
                except (ValueError, RuntimeError): pass
    return first + second, traces, active
value, traces, active = loop(8000, Owner())
print(json.dumps([value, traces, active]))
"""

def check_two_calls():
    result = subprocess.run([sys.executable, '-s', '-c', TWO_CALLS],
        env=dict(os.environ, PYTHON_JIT='1', PYTHON_UOPS_OPTIMIZE='1'),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    value, traces, active = json.loads(result.stdout)
    assert value == 5.0 and active
    found = False
    for trace in traces:
        begins = [i for i, entry in enumerate(trace) if entry[0] == '_BEGIN_VIRTUAL_CALL']
        if len(begins) != 2: continue
        ends = [i for i, entry in enumerate(trace) if entry[0] == '_RETURN_VIRTUAL_CALL']
        assert len(ends) == 2 and begins[0] < ends[0] < begins[1] < ends[1]
        entry_targets = [trace[i][2] for i in begins]
        assert entry_targets[0] != entry_targets[1]
        regions = []
        for begin, end in zip(begins, ends):
            regions.append([(target, pc) for index, target, pc, name in exits(trace) if begin < index < end])
        assert all(regions), regions
        assert {target for target, pc in regions[0]}.isdisjoint(target for target, pc in regions[1])
        assert {pc for target, pc in regions[0]} & {pc for target, pc in regions[1]}
        assert all(target not in entry_targets for region in regions for target, pc in region)
        found = True
    assert found, 'two virtual calls not observed in one executor'

def main():
    incumbent = '--incumbent' in sys.argv
    sharing = False
    count = 0
    for managed in (False, True):
        candidate = run(True, managed)
        reference = run(False, managed)
        noopt = run(True, managed, False)
        assert candidate['results'] == reference['results'] == noopt['results']
        assert candidate['active'] and all(candidate['active'])
        results = reference['results']
        for bound in (False, True):
            prefix = str(bound) + ' '
            assert results[prefix + 'success'][0] == 3.75.hex()
            for scenario in ('missing', 'wrong owner'):
                failure = results[prefix + scenario]
                assert failure[0] == 'AttributeError'
                frame = failure[-1][-1]
                assert frame[1] == 'LOAD_ATTR' and frame[2:4] == results[prefix + scenario + ' position']
                assert results[prefix + scenario + ' payload'] == ['value', True]
            failure = results[prefix + 'zero']
            frame = failure[-1][-1]
            assert failure[0] == 'ZeroDivisionError' and frame[1] == 'BINARY_OP'
            assert frame[2:4] == results[prefix + 'zero position']
            assert frame[-1]['first'] == frame[-1]['alias'] == 3.75.hex()
            assert frame[-1]['alias_is_first'] and frame[-1]['second'] == 0.0.hex()
            if managed:
                assert results[prefix + 'replace dict'][0] == 10.25.hex()
                assert results[prefix + 'clear dict'][0] == 'AttributeError'
        for traces in candidate['traces'].values():
            for trace in traces:
                guards = list(exits(trace))
                if guards:
                    count += len(guards)
                    targets = [item[1] for item in guards]
                    if len(targets) != len(set(targets)): sharing = True
                    if incumbent: assert len(targets) == len(set(targets)), guards
    assert count, 'no virtual guard materializers observed'
    if not incumbent: assert sharing, 'no shared virtual guard target observed'
    check_two_calls()
    print('OK virtual exit stubs', 'incumbent' if incumbent else 'candidate', count)

if __name__ == '__main__': main()
