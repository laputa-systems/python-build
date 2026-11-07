import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jit_fuzz


class ComparisonTests(unittest.TestCase):
    def test_detects_each_observable_difference(self):
        record = {"program": 0, "result": ["ok", ["float", "0x0.0p+0"]],
                  "events": [], "trace_lines": [], "monitor_lines": [], "jitted": False}
        for field, value in (("result", ["raised", "ValueError", "bad"]),
                             ("events", [["FSub", "0x1.0p+0"]]),
                             ("trace_lines", [["program_0", 1]])):
            with self.subTest(field=field):
                changed = dict(record, **{field: value})
                self.assertEqual(jit_fuzz.compare({0: changed}, {0: record}),
                                 [jit_fuzz.Mismatch(0, changed, record)])

    def test_ignores_only_jit_activity(self):
        self.assertEqual(jit_fuzz.compare({0: {"jitted": True, "result": []}},
                                          {0: {"jitted": False, "result": []}}), [])

    def test_monitoring_lines_are_compared_only_between_jit_off_runs(self):
        record = {"result": [], "monitor_lines": [["program_0", 2]]}
        changed = dict(record, monitor_lines=[["program_0", 3]])
        self.assertEqual(jit_fuzz.compare({0: changed}, {0: record},
                                          ignore=jit_fuzz.JIT_VARIANT_FIELDS), [])
        self.assertEqual(jit_fuzz.compare({0: changed}, {0: record}),
                         [jit_fuzz.Mismatch(0, changed, record)])

    def test_leak_excess_above_the_allowance_is_a_mismatch(self):
        record = {"result": [], "leak_blocks": 5000}
        clean = {"result": [], "leak_blocks": 5200}
        leaky = {"result": [], "leak_blocks": 5000 + jit_fuzz.LEAK_BLOCKS + 1}
        self.assertEqual(jit_fuzz.leak_mismatches(
            {0: clean}, {0: record}, tolerance=jit_fuzz.LEAK_BLOCKS, excess_only=True), [])
        self.assertEqual(jit_fuzz.leak_mismatches(
            {0: leaky}, {0: record}, tolerance=jit_fuzz.LEAK_BLOCKS, excess_only=True),
            [jit_fuzz.Mismatch(0, leaky, record)])

    def test_leak_noise_is_two_sided_between_jit_off_runs(self):
        base = {"result": [], "leak_blocks": 31}
        noisy = {"result": [], "leak_blocks": 32}
        far = {"result": [], "leak_blocks": 31 + jit_fuzz.LEAK_NOISE_BLOCKS + 1}
        self.assertEqual(jit_fuzz.leak_mismatches(
            {0: noisy}, {0: base}, tolerance=jit_fuzz.LEAK_NOISE_BLOCKS, excess_only=False), [])
        self.assertEqual(jit_fuzz.leak_mismatches(
            {0: far}, {0: base}, tolerance=jit_fuzz.LEAK_NOISE_BLOCKS, excess_only=False),
            [jit_fuzz.Mismatch(0, far, base)])

    def test_detects_missing_program_on_either_side(self):
        record = {"program": 0, "result": []}
        self.assertEqual(jit_fuzz.compare({0: record}, {}),
                         [jit_fuzz.Mismatch(0, record, None)])
        self.assertEqual(jit_fuzz.compare({}, {0: record}),
                         [jit_fuzz.Mismatch(0, None, record)])


    def test_batch_command_does_not_ignore_the_jit_environment(self):
        argv, env = jit_fuzz.batch_command(Path("/python"), Path("/scratch/batch.py"), jit=False)
        self.assertNotIn("-I", argv)
        self.assertNotIn("-E", argv)
        self.assertEqual(env["PYTHON_JIT"], "0")
        _, enabled = jit_fuzz.batch_command(Path("/python"), Path("/scratch/batch.py"), jit=True)
        self.assertEqual(enabled["PYTHON_JIT"], "1")


class GeneratedProgramTests(unittest.TestCase):
    def test_host_execution_is_deterministic_and_reports_observations(self):
        programs = [jit_fuzz.generate(seed, index) for seed, index in
                    ((0, index) for index in range(30))]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "programs.py"
            path.write_text(jit_fuzz.batch_source(programs), encoding="utf-8")
            outputs = []
            for _ in range(2):
                completed = subprocess.run(
                    [sys.executable, "-I", str(path)], capture_output=True, text=True,
                    check=False, env=dict(os.environ, PYTHON_JIT="0"), timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                outputs.append(jit_fuzz.parse_reports(completed.stdout))
        self.assertEqual(set(outputs[0]), set(range(30)))
        self.assertEqual(jit_fuzz.compare(*outputs), [])
        for record in outputs[0].values():
            self.assertEqual(record["result"][0], "ok", record)


if __name__ == "__main__":
    unittest.main()
