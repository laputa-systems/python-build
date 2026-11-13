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
import ast
import dis
import json
import os
import sys
import zipfile
from benchmarks.workloads import jit_kernels

original = jit_kernels._load
captured = {}
def load(name):
    result = original(name)
    if name == 'chaos' and sys.argv[1] == 'integer':
        with zipfile.ZipFile(jit_kernels.PYPERFORMANCE_WHEEL) as archive:
            source = archive.read('pyperformance/data-files/benchmarks/bm_chaos/run_benchmark.py')
        tree = ast.parse(source)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Spline')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '__call__')
        assignments = ast.parse('co1 = 0; co2 = 0').body
        for assignment in assignments:
            ast.copy_location(assignment, method.body[1])
        method.body[1:1] = assignments
        exec(compile(ast.fix_missing_locations(tree), result['__file__'], 'exec'), result)
    captured[name] = result
    return result
jit_kernels._load = load
result = jit_kernels.run('chaos', 20)
function = captured['chaos']['Spline'].__call__
store = max(i.offset for i in dis.get_instructions(function)
             if i.opname == 'STORE_FAST' and i.argval == 'co1')
print(json.dumps(dict(pid=os.getpid(), digest=result['digest'], store=store)))
'''


def run(jit, optimize, initial):
    with tempfile.TemporaryDirectory(prefix='jit-local-reentry-') as directory:
        mapping = Path(directory) / 'jit-map.tsv'
        result = subprocess.run([sys.executable, '-S', '-c', PROGRAM, initial],
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
    parser.add_argument('--expect', choices=('candidate', 'nullable', 'previous'), default='candidate')
    args = parser.parse_args()
    for initial in ('unset', 'integer'):
        on, off, noopt = run(True, True, initial), run(False, True, initial), run(True, False, initial)
        assert on[0] == off[0] == noopt[0]
        assert not off[1] and not noopt[1]
        if args.expect == 'candidate':
            expected = 0 if initial == 'unset' else 1
            assert on[1] == expected, ('store-entry traces', initial, on[1])
            assert any(uop.name.startswith('_STORE_FAST_FUSED_FLOAT_OR_NULL_')
                       for trace in on[2] for uop in trace.uops)
        elif args.expect == 'nullable' and initial == 'unset':
            assert on[1] == 0
        else:
            assert on[1] > 1, ('missing predecessor retracing', initial, on[1])
        print('OK fresh-frame local cleanup preserves digest;', initial, 'store-entry traces:', on[1])


if __name__ == '__main__':
    main()
