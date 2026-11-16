"""Memory-only workloads require output and RSS evidence without timing evidence."""

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks import bench
from benchmarks.harness import evidence, models, process, runner
from benchmarks.harness.report import build_summary, render_summary
from benchmarks.harness.statistics import compare_workload
from benchmarks.workloads.registry import Workload


class MemoryOnlyTests(unittest.TestCase):
    def test_identity_launch_alias_retains_actual_executable_hash_and_rejects_wrong_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "stage"
            python = stage / "bin/python3.16"
            python.parent.mkdir(parents=True)
            python.write_text("verified executable")
            home = Path(temp) / "home"
            home.symlink_to(stage, target_is_directory=True)
            launch = home / "bin/python3.16"
            data = {"runtime_prefix": str(home), "runtime_exec_prefix": str(home),
                    "runtime_executable": str(launch), "runtime_base_executable": str(launch)}
            with patch.object(bench.subprocess, "run") as run:
                run.return_value.stdout = json.dumps(data)
                identity = bench._identity(python, "fixture", "fixture", "fixture",
                                           python_home=home, launch_executable=launch)
                self.assertEqual(run.call_args.args[0][0], str(launch))
                self.assertEqual(identity["executable"], str(python))
                self.assertEqual(identity["executable_sha256"], bench._sha256(python))
                self.assertEqual(identity["runtime_launch_executable"], str(launch))
                data["runtime_base_executable"] = str(python)
                run.return_value.stdout = json.dumps(data)
                with self.assertRaisesRegex(ValueError, "requested launch"):
                    bench._identity(python, "fixture", "fixture", "fixture",
                                    python_home=home, launch_executable=launch)

    def test_matched_homes_reach_each_sides_warmup_and_memory_children(self):
        workload = Workload("zipimport_cold", "startup", "zlib", "import", 3, 1)
        completed = SimpleNamespace(cleanup_complete=True, remaining_pids=(), returncode=0,
                                    timed_out=False, stdout=b'{"operation_count":3,"digest":"same"}',
                                    stderr=b"", memory={"peak_rss_bytes": 1000000, "samples": [{}]})
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(runner, "run_command", return_value=completed) as run:
            root = Path(temp)
            stages = [root / name for name in ("baseline", "subject0")]
            aliases = [root / name for name in ("a", "b")]
            for stage, alias in zip(stages, aliases):
                (stage / "bin").mkdir(parents=True)
                (stage / "bin/python").write_text("fixture")
                alias.symlink_to(stage, target_is_directory=True)
            pythons = [stage / "bin/python" for stage in stages]
            homes = dict(zip(map(str, pythons), map(str, aliases)))
            result = runner.run_workload(
                workload, *pythons, profile="quick", site_packages=None,
                output_dir=Path(temp), memory_only=True,
                baseline_python_home=aliases[0], candidate_python_home=aliases[1])
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["env"]["PYTHONHOME"], homes[call.args[0][0]])
            self.assertEqual(result["baseline"]["python_home"], str(aliases[0]))
            self.assertEqual(result["candidate"]["python_home"], str(aliases[1]))
            models.validate_workload_result(result)

    def test_invalid_python_home_pair_fails_before_any_child(self):
        workload = Workload("python_startup", "startup", "extra", "process", 1, 1)
        pairs = [(Path("/one"), None), (None, Path("/two")),
                 (Path("/short"), Path("/longer")), (Path("/ä"), Path("/å")),
                 (Path("relative/a"), Path("relative/b")),
                 (Path("/missing/a"), Path("/missing/b"))]
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "run_command") as run:
            for baseline_home, candidate_home in pairs:
                with self.subTest(homes=(baseline_home, candidate_home)), self.assertRaises(ValueError):
                    runner.run_workload(
                        workload, Path("/base/bin/python"), Path("/cand/bin/python"),
                        profile="quick", site_packages=None, output_dir=Path(temp), memory_only=True,
                        baseline_python_home=baseline_home, candidate_python_home=candidate_home)
            run.assert_not_called()

    def test_identity_uses_selected_home_and_rejects_ignored_home(self):
        home = Path("/matched/home")
        identity = {"runtime_prefix": str(home), "runtime_exec_prefix": str(home)}
        with patch.dict("os.environ", {"PYTHONHOME": "/foreign/home"}), \
                patch.object(bench, "_sha256", return_value="fixture"), \
                patch.object(bench.subprocess, "run") as run:
            run.return_value.stdout = json.dumps(identity)
            bench._identity(Path("/python"), "fixture", "fixture", "fixture", python_home=home)
            self.assertEqual(run.call_args.kwargs["env"]["PYTHONHOME"], str(home))
            bench._identity(Path("/python"), "fixture", "fixture", "fixture")
            self.assertNotIn("PYTHONHOME", run.call_args.kwargs["env"])
            run.return_value.stdout = json.dumps({"runtime_prefix": "/wrong", "runtime_exec_prefix": "/wrong"})
            with self.assertRaisesRegex(ValueError, "did not honor"):
                bench._identity(Path("/python"), "fixture", "fixture", "fixture", python_home=home)

    def test_container_rejects_host_aliases_before_launch(self):
        args = SimpleNamespace(baseline_python_home=Path("/matched/a"), candidate_python_home=Path("/matched/b"))
        with patch.object(bench.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "require --local"):
            bench._run_container(args)
        run.assert_not_called()

    def record(self):
        return {"measurement_mode": "memory-only", "identity": {
            "name": "zipimport_cold", "category": "startup", "operation": "import",
            "operation_count": 3, "digest": "same"},
            **{side: {"memory": {"rounds": [{"peak_rss": 1000000, "samples": [{}]}] * 3}}
               for side in ("baseline", "candidate")}}

    def test_explicit_memory_records_validate_compare_and_render_without_timing(self):
        record = self.record()
        for timing in (None, {"samples": []}, {"samples": [float("nan")], "cpu_rounds": "invalid"},
                       {"samples": ["invalid"], "cpu_rounds": "invalid"}):
            with self.subTest(timing=timing):
                source = deepcopy(record)
                for side in ("baseline", "candidate"):
                    source[side]["memory"]["rounds"][0]["cpu"] = {"user_seconds": float("nan")}
                    if timing is not None:
                        source[side]["timing"] = timing
                compared = compare_workload(source, memory_primary_metric="peak_rss")
                self.assertEqual(compared["measurement_mode"], "memory-only")
                self.assertEqual(compared["comparison"]["timing"]["status"], "not_measured")
                self.assertIsNone(compared["comparison"]["time_ratio"])
                self.assertEqual(compared["comparison"]["memory"]["metrics"]["peak_rss"]["status"], "pass")
                self.assertIn("not_measured", render_summary(build_summary(
                    [compared], baseline="base", candidate="cand")))
                source.pop("measurement_mode")
                with self.assertRaises(models.ResultSchemaError):
                    models.validate_workload_result(source)

    def test_memory_mode_still_requires_rss(self):
        source = self.record()
        source["candidate"]["memory"]["rounds"][0].pop("peak_rss")
        with self.assertRaises(models.ResultSchemaError):
            models.validate_workload_result(source)

    def test_runner_skips_clocks_cpu_ledgers_and_timing_rounds(self):
        workload = Workload("zipimport_cold", "startup", "zlib", "import", 3, 1)
        payload = {"operation_count": 3, "digest": "same", "elapsed_seconds": "invalid",
                   "reaped_child_cpu": {"user_seconds": "invalid"}}
        completed = SimpleNamespace(cleanup_complete=True, remaining_pids=(), returncode=0,
                                    timed_out=False, stdout=json.dumps(payload).encode(), stderr=b"",
                                    memory={"peak_rss_bytes": 1000000, "samples": [{}]})
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(runner, "run_command", return_value=completed) as run, \
                patch.object(runner, "_timing_elapsed", side_effect=AssertionError("clock used")), \
                patch.object(runner, "_cpu_dict", side_effect=AssertionError("CPU used")):
            result = runner.run_workload(workload, Path("/base"), Path("/cand"), profile="standard",
                                         site_packages=None, output_dir=Path(temp), memory_only=True)
            self.assertEqual(run.call_count, 2 + 2 * runner.PROFILE_ROUNDS["standard"][1])
            self.assertEqual(len(list(Path(temp).glob("timing-*.json"))), 0)
            for side in ("baseline", "candidate"):
                self.assertEqual(len(result[side]["memory"]["rounds"]),
                                 runner.PROFILE_ROUNDS["standard"][1])
                self.assertNotIn("cpu", result[side]["memory"]["rounds"][0])
            models.validate_workload_result(result)

    def test_explicit_null_mode_is_invalid_at_every_schema_boundary(self):
        record = self.record()
        record["measurement_mode"] = None
        for side in ("baseline", "candidate"):
            record[side]["timing"] = {"samples": [1.0, 1.0]}
        with self.assertRaisesRegex(models.ResultSchemaError, "measurement_mode"):
            models.validate_workload_result(record)
        compared = compare_workload(self.record(), memory_primary_metric="peak_rss")
        compared["measurement_mode"] = None
        summary = {"schema_version": 1, "baseline": "base", "candidate": "cand",
                   "suite": "realworld", "profile": "standard", "workloads": [compared]}
        with self.assertRaisesRegex(models.ResultSchemaError, "measurement_mode"):
            models.validate_summary(summary)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "summary.json").write_text(json.dumps(summary))
            provenance = {key: "fixture" for key in (
                "run_order", "offline_boundary", "bytecode_policy", "benchmark_lock_sha256")}
            provenance.update(measurement_mode="memory-only", memory_sampling_interval_seconds=0.01,
                              python_environment={}, benchmark_packages=[], baseline={}, candidate={},
                              installed_size={"baseline": {}, "candidate": {}}, host={})
            (root / "provenance.json").write_text(json.dumps(provenance))
            with self.assertRaisesRegex(ValueError, "measurement_mode"):
                evidence.compact_evidence(root, root / "evidence.json")

    def test_real_process_rss_collection_does_not_require_cpu_usage(self):
        original_wait = process._wait4
        def wait_without_cpu(*args, **kwargs):
            return replace(original_wait(*args, **kwargs), usage=None)
        with patch.object(process, "_wait4", side_effect=wait_without_cpu):
            result = process.run_command([
                sys.executable, "-c", "import time; data=bytearray(1048576); time.sleep(0.15); print('ok')"],
                timeout=5, sample_interval_seconds=0.01)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.cleanup_complete)
        self.assertEqual(result.stdout, b"ok\n")
        self.assertIsNone(result.cpu_user_seconds)
        self.assertIsNone(result.cpu_system_seconds)
        self.assertIsNotNone(result.memory)
        self.assertGreater(result.memory.peak_rss_bytes, 0)
        self.assertTrue(result.memory.samples)

    def test_unknown_mode_and_incomplete_tree_samples_fail_closed(self):
        record = self.record()
        record["measurement_mode"] = "unknown"
        with self.assertRaisesRegex(models.ResultSchemaError, "measurement_mode"):
            models.validate_workload_result(record)
        for incomplete in ({"peak_rss": 1000000, "samples": []},
                           {"peak_rss": 1000000, "samples": [{}], "sampling_errors": ["child lost"]}):
            record = self.record()
            record["candidate"]["memory"]["rounds"] = [incomplete]
            with self.assertRaisesRegex(models.ResultSchemaError, "process-tree"):
                models.validate_workload_result(record)

    def test_output_mismatch_still_fails_memory_runner(self):
        workload = Workload("zipimport_cold", "startup", "zlib", "import", 3, 1)
        def completed(command, **kwargs):
            payload = {"operation_count": 3, "digest": command[0]}
            return SimpleNamespace(cleanup_complete=True, remaining_pids=(), returncode=0,
                                   timed_out=False, stdout=json.dumps(payload).encode(), stderr=b"",
                                   memory={"peak_rss_bytes": 1000000, "samples": [{}]})
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "run_command", side_effect=completed), self.assertRaisesRegex(RuntimeError, "correctness digest differs"):
            runner.run_workload(workload, Path("/base"), Path("/cand"), profile="quick",
                                site_packages=None, output_dir=Path(temp), memory_only=True)

    def test_noisy_memory_rounds_remain_unchanged(self):
        workload = Workload("zipimport_cold", "startup", "zlib", "import", 3, 1, noise_class="noisy")
        completed = SimpleNamespace(cleanup_complete=True, remaining_pids=(), returncode=0,
                                    timed_out=False, stdout=b'{"operation_count":3,"digest":"same"}',
                                    stderr=b"", memory={"peak_rss_bytes": 1000000, "samples": [{}]})
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "run_command", return_value=completed) as run:
            result = runner.run_workload(workload, Path("/base"), Path("/cand"), profile="quick",
                                         site_packages=None, output_dir=Path(temp), memory_only=True)
            self.assertEqual(run.call_count, 2 + 2 * 5)
            self.assertEqual(len(result["baseline"]["memory"]["rounds"]), 5)

    def test_cli_exposes_memory_only_execution_mode(self):
        with patch.object(bench, "_run_internal", return_value=Path("/result")) as run, \
                patch.object(evidence, "compact_evidence", return_value=Path("/evidence")):
            self.assertEqual(bench.main(["run", "--local", "--baseline", "/base", "--candidate", "/cand",
                                        "--memory-only", "--evidence", "/evidence"]), 0)
            self.assertTrue(run.call_args.args[0].memory_only)

    def test_compact_memory_evidence_requires_every_memory_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self.record()
            compared = compare_workload(record, memory_primary_metric="peak_rss")
            (root / "summary.json").write_text(json.dumps({"suite": "realworld", "profile": "standard",
                                                          "workloads": [compared]}))
            provenance = {key: "fixture" for key in (
                "run_order", "offline_boundary", "bytecode_policy", "benchmark_lock_sha256")}
            provenance.update(measurement_mode="memory-only", memory_sampling_interval_seconds=0.01,
                              python_environment={}, benchmark_packages=[], baseline={}, candidate={},
                              installed_size={"baseline": {}, "candidate": {}}, host={})
            for side, name in (("baseline", "a"), ("candidate", "b")):
                home = f"/matched/{name}"
                provenance[side].update(runtime_python_home=home, runtime_prefix=home,
                                        runtime_exec_prefix=home, stdlib_json_file=f"{home}/lib/json/__init__.py",
                                        runtime_launch_executable=f"{home}/bin/python3.16",
                                        runtime_executable=f"{home}/bin/python3.16",
                                        runtime_base_executable=f"{home}/bin/python3.16")
            (root / "provenance.json").write_text(json.dumps(provenance))
            directory = root / "realworld" / "zipimport_cold"
            directory.mkdir(parents=True)
            for side in ("baseline", "candidate"):
                for index in range(3):
                    (directory / f"memory-{index:02d}-{side}.json").write_text(json.dumps({
                        "payload": {"digest": "same", "operation_count": 3},
                        "memory": {"peak_rss": 1000000, "samples": [{}]}}))
            destination = root / "evidence.json"
            evidence.compact_evidence(root, destination)
            document = json.loads(destination.read_text())
            for side in ("baseline", "candidate"):
                for key in ("runtime_python_home", "runtime_prefix", "runtime_exec_prefix", "stdlib_json_file",
                            "runtime_launch_executable", "runtime_executable", "runtime_base_executable"):
                    self.assertEqual(document["inputs"][side][key], provenance[side][key])
            self.assertEqual(document["workloads"][0]["measurement_mode"], "memory-only")
            self.assertEqual(len(document["workloads"][0]["attempts"]), 6)
            self.assertTrue(all(item["kind"] == "memory" for item in document["workloads"][0]["attempts"]))
            (directory / "memory-00-candidate.json").unlink()
            with self.assertRaisesRegex(ValueError, "memory observations are incomplete"):
                evidence.compact_evidence(root, root / "incomplete.json")

    def test_compact_memory_attempt_does_not_require_cpu_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory-00-baseline.json"
            path.write_text(json.dumps({"payload": {"digest": "same", "operation_count": 3,
                                                     "elapsed_seconds": "invalid"},
                                        "memory": {"peak_rss": 1000000, "samples": [{}]}}))
            observation = evidence._observation(path, memory_only=True)
            self.assertEqual(observation["peak_rss_bytes"], 1000000)
            self.assertNotIn("user_seconds", observation)
            with self.assertRaisesRegex(ValueError, "CPU"):
                evidence._observation(path)


if __name__ == "__main__":
    unittest.main()
