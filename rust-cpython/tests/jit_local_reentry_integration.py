import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jit_sample import parse_map

PROGRAM = r'''
import dis
import json
import os
from benchmarks.workloads import jit_kernels

original = jit_kernels._load
captured = {}
def load(name):
    result = original(name)
    captured[name] = result
    return result
jit_kernels._load = load
result = jit_kernels.run('chaos', 20)
function = captured['chaos']['Spline'].__call__
store = next(i.offset for i in dis.get_instructions(function)
             if i.opname == 'STORE_FAST' and i.argval == 'co1')
print(json.dumps(dict(pid=os.getpid(), digest=result['digest'], store=store)))
'''


def run(jit, optimize):
    with tempfile.TemporaryDirectory(prefix='jit-local-reentry-') as directory:
        mapping = Path(directory) / 'jit-map.tsv'
        result = subprocess.run([sys.executable, '-S', '-c', PROGRAM],
            env=dict(os.environ, PYTHON_JIT='1' if jit else '0',
                     PYTHON_UOPS_OPTIMIZE='1' if optimize else '0',
                     PYTHON_JIT_MAP=str(mapping), PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (jit, optimize, result.stderr)
        output = json.loads(result.stdout)
        traces = parse_map(mapping.read_text(), output['pid']) if mapping.exists() else []
        selected = [trace for trace in traces if trace.name == 'Spline.__call__' and
                    trace.offset == output['store']]
        return output['digest'], len(selected), traces


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect', choices=('candidate', 'previous'), default='candidate')
    args = parser.parse_args()
    on, off, noopt = run(True, True), run(False, True), run(True, False)
    assert on[0] == off[0] == noopt[0]
    assert not off[1] and not noopt[1]
    if args.expect == 'candidate':
        assert on[1] == 0, ('fresh local store retraced', on[1])
        assert any(uop.name.startswith('_STORE_FAST_FUSED_FLOAT_OR_NULL_')
                   for trace in on[2] for uop in trace.uops)
    else:
        assert on[1] > 1, ('missing predecessor retracing', on[1])
    print('OK fresh-frame local cleanup preserves digest; store-entry traces:', on[1])


if __name__ == '__main__':
    main()
