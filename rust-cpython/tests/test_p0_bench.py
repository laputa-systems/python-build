"""Contract tests for p0-bench's precheck and diagnostic modes."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LANE = Path(__file__).resolve().parents[1]
REPO_ROOT = LANE.parent
SCRIPT = LANE / "tools" / "p0-bench"


class P0BenchTests(unittest.TestCase):
    def run_case(self, *, pre=0, post=0, verdict="ACCEPT", nested_verdict=False, args=()):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            gate = root / "gate-ready"
            perf = root / "perf.py"
            musl_run = root / "musl-run"
            gate_log = root / "gate-calls.jsonl"
            bench_marker = root / "bench-ran"
            perf_args = root / "perf-args.json"
            musl_args = root / "musl-args.json"
            gate.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "log = Path(os.environ['GATE_CALLS'])\n"
                "with log.open('a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "status = os.environ['POST_EXIT'] if sys.argv[1:] == ['-1'] else os.environ['PRE_EXIT']\n"
                "raise SystemExit(int(status))\n",
                encoding="utf-8",
            )
            gate.chmod(0o755)
            perf.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys, time\n"
                "from pathlib import Path\n"
                "Path(os.environ['PERF_ARGS']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                "result = Path(os.environ['P0_BENCH_RESULTS']) / f'run-{time.time_ns()}'\n"
                "result.mkdir(parents=True)\n"
                "verdict = os.environ['PERF_VERDICT']\n"
                "nested = os.environ['PERF_NESTED_VERDICT'] == '1'\n"
                "payload = {'decision': {'decision': verdict, 'gate': True}} if nested else {'verdict': verdict}\n"
                "(result / 'verdict.json').write_text(json.dumps(payload) + '\\n', encoding='utf-8')\n"
                "Path(os.environ['PERF_MARKER']).write_text('ran\\n', encoding='utf-8')\n"
                "print(f'LOG {result / \"verdict.json\"}')\n"
                "raise SystemExit(int(os.environ.get('PERF_EXIT', '0')))\n",
                encoding="utf-8",
            )
            musl_run.write_text(
                "import json, os, subprocess, sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['MUSL_ARGS']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                "raise SystemExit(subprocess.run(sys.argv[1:], check=False).returncode)\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "P0_BENCH_RESULTS": str(results),
                    "PERF_PY": str(perf),
                    "MUSL_RUN": str(musl_run),
                    "PERF_GATE_READY": str(gate),
                    "PRE_EXIT": str(pre),
                    "POST_EXIT": str(post),
                    "PERF_VERDICT": verdict,
                    "PERF_NESTED_VERDICT": "1" if nested_verdict else "0",
                    "PERF_MARKER": str(bench_marker),
                    "PERF_ARGS": str(perf_args),
                    "MUSL_ARGS": str(musl_args),
                    "GATE_CALLS": str(gate_log),
                }
            )
            completed = subprocess.run(
                ["bash", str(SCRIPT), *args],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            ran_records = list(results.glob("*/external-host-checks.json"))
            deferred_records = list(results.glob("external-host-checks-*.json"))
            record_path = (ran_records or deferred_records or [None])[-1]
            record = (
                json.loads(record_path.read_text(encoding="utf-8"))
                if record_path is not None
                else None
            )
            gate_calls = (
                [json.loads(line) for line in gate_log.read_text(encoding="utf-8").splitlines()]
                if gate_log.exists()
                else []
            )
            recorded_perf_args = (
                json.loads(perf_args.read_text(encoding="utf-8"))
                if perf_args.exists()
                else None
            )
            recorded_musl_args = (
                json.loads(musl_args.read_text(encoding="utf-8"))
                if musl_args.exists()
                else None
            )
            return {
                "completed": completed,
                "record": record,
                "bench_ran": bench_marker.exists(),
                "gate_calls": gate_calls,
                "perf_args": recorded_perf_args,
                "perf_path": str(perf),
                "musl_args": recorded_musl_args,
            }

    def test_failed_default_precheck_defers_without_benchmark_or_postcheck(self):
        result = self.run_case(pre=1, args=("--runs", "1"))
        self.assertEqual(result["completed"].returncode, 4)
        self.assertFalse(result["bench_ran"])
        self.assertEqual(result["gate_calls"], [[]])
        self.assertEqual(
            result["completed"].stderr.strip(),
            "DEFERRED: perf-gate-ready precheck exit=1; bench not run",
        )
        self.assertEqual(result["record"]["pre"]["exit"], 1)
        self.assertFalse(result["record"]["bench_run"])
        self.assertEqual(result["record"]["status"], "DEFERRED")
        self.assertFalse(result["record"]["eligible"])
        self.assertFalse(result["record"]["accepted"])
        self.assertEqual(result["record"]["args"], ["--runs", "1"])

    def test_empty_diagnostic_reason_exits_before_precheck_and_benchmark(self):
        result = self.run_case(pre=1, args=("--diagnostic-reason", " \t "))
        self.assertEqual(result["completed"].returncode, 2)
        self.assertFalse(result["bench_ran"])
        self.assertEqual(result["gate_calls"], [])
        self.assertIsNone(result["record"])

    def test_diagnostic_bench_runs_after_failed_precheck_but_is_never_eligible(self):
        result = self.run_case(
            pre=1,
            nested_verdict=True,
            args=("--diagnostic-reason", "owner requested", "--runs", "1"),
        )
        self.assertEqual(result["completed"].returncode, 3)
        self.assertTrue(result["bench_ran"])
        self.assertEqual(result["gate_calls"], [[], ["-1"]])
        self.assertEqual(result["perf_args"], ["bench", "--runs", "1"])
        self.assertEqual(result["record"]["mode"], "diagnostic")
        self.assertEqual(result["record"]["diagnostic_reason"], "owner requested")
        self.assertEqual(result["record"]["verdict"], "ACCEPT")
        self.assertTrue(result["record"]["bench_run"])
        self.assertFalse(result["record"]["eligible"])
        self.assertFalse(result["record"]["accepted"])
        self.assertEqual(result["record"]["args"], ["--runs", "1"])
        self.assertEqual(
            result["musl_args"],
            ["python3", result["perf_path"], "bench", "--runs", "1"],
        )

    def test_accept_verdict_with_both_checks_passing_is_accepted(self):
        result = self.run_case(pre=0, post=0, verdict="ACCEPT", nested_verdict=True)
        self.assertEqual(result["completed"].returncode, 0)
        self.assertTrue(result["bench_ran"])
        self.assertEqual(result["record"]["mode"], "default")
        self.assertEqual(result["record"]["verdict"], "ACCEPT")
        self.assertTrue(result["record"]["bench_run"])
        self.assertTrue(result["record"]["eligible"])
        self.assertTrue(result["record"]["accepted"])

    def test_failed_postcheck_makes_accept_verdict_ineligible(self):
        result = self.run_case(pre=0, post=1, verdict="ACCEPT")
        self.assertEqual(result["completed"].returncode, 3)
        self.assertTrue(result["bench_ran"])
        self.assertEqual(result["record"]["verdict"], "ACCEPT")
        self.assertFalse(result["record"]["eligible"])
        self.assertFalse(result["record"]["accepted"])


if __name__ == "__main__":
    unittest.main()
