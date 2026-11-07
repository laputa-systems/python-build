import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jit_sample

PROGRAM = '''
import _opcode
import os

def work(n):
    total = 0
    for i in range(n):
        total += i % 19
    return total

work.__code__ = work.__code__.replace(co_qualname='tracé\\twork')
assert work(50000) == sum(i % 19 for i in range(50000))
for offset in range(0, len(work.__code__.co_code), 2):
    try:
        executor = _opcode.get_executor(work.__code__, offset)
    except ValueError:
        continue
    print((os.getpid(), id(executor), work.__code__.co_qualname, offset,
           [entry[0] for entry in executor], len(executor.get_jit_code())))
    break
else:
    raise AssertionError('no executor')
'''

with tempfile.TemporaryDirectory(prefix='jit-map-test-') as temporary:
    map_path = Path(temporary) / 'map.tsv'
    env = dict(os.environ, PYTHON_JIT_MAP=str(map_path))
    result = subprocess.run([sys.executable, '-c', PROGRAM], env=env,
                            capture_output=True, text=True, check=True)
    pid, executor, name, offset, names, code_size = ast.literal_eval(result.stdout.strip())
    traces = jit_sample.parse_map(map_path.read_text(), pid)
    matches = [trace for trace in traces if trace.executor == executor and trace.name == name]
    assert len(matches) == 1, matches
    trace = matches[0]
    assert trace.offset == offset, (trace.offset, offset)
    assert len(trace.uops) == len(names)
    assert [uop.name.rsplit('_r', 1)[0] for uop in trace.uops] == names
    assert trace.end - trace.start <= code_size
    assert trace.uops[0].start == trace.start
    for left, right in zip(trace.uops, trace.uops[1:]):
        assert left.end == right.start
    stage = Path(sys.executable).resolve().parents[1]
    tag = stage.name.removeprefix('stage-')
    header = stage.parent / 'work' / 'perf' / tag / 'build' / 'jit_stencils-aarch64-apple-darwin.h'
    sizes = {key: int(size) for key, size in re.findall(
        r'\[([A-Za-z0-9_]+)\] = \{emit_[^,]+, (\d+),', header.read_text())}
    assert all(uop.end - uop.start == sizes[uop.name] for uop in trace.uops)
    shared_map = Path(temporary) / 'shared.tsv'
    shared_env = dict(os.environ, PYTHON_JIT_MAP=str(shared_map))
    children = [subprocess.Popen([sys.executable, '-c', PROGRAM], env=shared_env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(3)]
    try:
        for child in children:
            output, error = child.communicate(timeout=30)
            assert child.returncode == 0, error
            child_pid, child_executor, _, _, child_names, _ = ast.literal_eval(output.strip())
            child_traces = jit_sample.parse_map(shared_map.read_text(), child_pid)
            child_trace, = [item for item in child_traces if item.executor == child_executor]
            assert len(child_trace.uops) == len(child_names)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    target = Path(temporary) / 'target'
    target.write_text('unchanged')
    link = Path(temporary) / 'link'
    link.symlink_to(target)
    refused = subprocess.run([sys.executable, '-c', PROGRAM],
                             env=dict(os.environ, PYTHON_JIT_MAP=str(link)),
                             capture_output=True, text=True, check=False)
    assert refused.returncode != 0
    assert 'OSError' in refused.stderr
    assert target.read_text() == 'unchanged'
    print(f'OK {name!r}: {len(names)} uops, {trace.end - trace.start} mapped bytes, symlink refused')
