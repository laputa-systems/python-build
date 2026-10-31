"""Contract tests for the lane's perf harness: verdicts, sync plans, leases.

These tests use synthetic summaries and scratch trees. They build nothing,
run no benchmarks, and never touch the real host lease.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest import mock

LANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LANE))

import builtin_modules
import perf
import perf_verdict as pv


def summary(wall_ci, *, wall_ratios=(1.0, 1.0, 1.0), cpu=None, status="pass", memory=None):
    timing = {"paired_ratio_samples": list(wall_ratios), "median_ratio_ci95": wall_ci,
              "status": status}
    if cpu is not None:
        base, cand = cpu
        timing["cpu"] = {"status": "compared", "metrics": {"total_seconds_per_operation": {
            "baseline_samples": list(base), "candidate_samples": list(cand)}}}
    return {"comparison": {"timing": timing, "memory": {"metrics": memory or {}}}}


OUTCOMES = {"worse": "regressed", "better": "improved", "neutral": "within budget"}


def verdict(*runs):
    return pv.entity_verdict([pv.run_observation(item) for item in runs])


def sample(cpu, load=0, peak=0, digest="d"):
    return {"cpu_seconds_per_iteration": cpu, "load_footprint_bytes": load,
            "working_peak_bytes": peak, "digest": digest}


def module(base, cand):
    return pv.module_observation(base, cand)


class ExploratoryEarlyRejectionTests(unittest.TestCase):
    def measure(self, classes=("worse", "worse"), *, bad_module=False, mismatch=False,
                guard_error=None, harness_changed=False, sampling_error=None, expected_events=None, **options):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root = Path(temp)
            sides = {ref: {"ref": ref, "name": ref[1:], "stage": root / ref[1:],
                           "python": Path("/python"), "report": {}}
                     for ref in ("@control", "@incumbent")}
            calls = []
            events = []
            counts = {}
            def observation(name, kind):
                counts[name] = counts.get(name, 0) + 1
                bad = name == ("json" if bad_module else "first")
                cls = classes[counts[name] - 1] if bad else "neutral"
                ratio = {"worse": 1.2, "better": 0.8, "neutral": 1.0}[cls]
                metrics = {metric: {"class": OUTCOMES[cls], "ratios": [ratio] * 3,
                                    "ci95": [ratio, ratio]}
                           for metric in (("load_footprint", "working_peak")
                                          if kind == "module" else ("peak_rss",))}
                metrics["cpu"] = {"class": "regressed", "ratios": [2.0] * 3,
                                  "ci95": [2.0, 2.0]}
                return {"kind": kind, "metrics": metrics,
                        "mismatch": mismatch and name == "tail"}
            def workload(baseline, candidate, name, **kwargs):
                calls.append(("workload", name))
                events.append(("workload", name))
                if sampling_error == "workload":
                    raise perf.LaneError("workload failed")
                return None if mismatch and name == "tail" else observation(name, "workload")
            def module(baseline, candidate, name, **kwargs):
                self.assertEqual(kwargs["rounds"], perf.PROFILE_MODULE_ROUNDS[arguments["profile"]])
                self.assertEqual(kwargs["iterations"], 37)
                calls.append(("module", name))
                events.append(("module", name))
                if sampling_error == "module":
                    raise perf.LaneError("module failed")
                return observation(name, "module")
            def patch(name, **kwargs):
                return patches.enter_context(mock.patch.object(perf, name, **kwargs))
            patch("LANE", new=root)
            identity = {"source_sha256": "fixture"}
            patch("_harness_identity", side_effect=[identity, identity,
                  {"source_sha256": "changed"} if harness_changed else identity])
            patch("selection", return_value=(["first", "tail", "json", "csv"],
                                              ["first", "tail"], ["json", "csv"]))
            patch("host_lease", return_value=nullcontext())
            patch("resolve", side_effect=sides.__getitem__)
            checks = patch("_verify_stage", side_effect=guard_error)
            gates = patch("_gate_checks")
            patch("_host_sample", return_value={"quiet": True})
            def calibrate(python, name, scratch, **kwargs):
                events.append(("calibrate", name))
                if sampling_error == "iterations":
                    raise perf.LaneError("iterations failed")
                return 37
            calibration = patch("_module_iterations", side_effect=calibrate)
            patch("_run_bench", side_effect=workload)
            patch("_measure_module", side_effect=module)
            patches.enter_context(mock.patch.object(pv, "run_observation", side_effect=lambda x, **kw: x))
            arguments = {"baseline_ref": "@incumbent", "candidate_ref": "@control",
                             "workloads": ["first", "tail"], "modules": ["json", "csv"], "gate": False,
                             "runs": len(classes), "profile": "standard", "timing_only": False, "min_idle": 0,
                             "self_compare": False, "record_baselines": False, "memory_only": True}
            arguments.update(options)
            try:
                record = perf._measure(**arguments)
            finally:
                self.assertFalse(list(root.glob("results/perf-bench/*/tmp")))
            self.assertEqual(checks.call_count, 2 if arguments["self_compare"] else 4)
            self.assertEqual(gates.call_count, int(arguments["gate"]))
            saved = json.loads((Path(record["directory"]) / "verdict.json").read_text())
            self.assertEqual(saved, record)
            self.assertEqual(calibration.call_count, len(record["module_iterations"]))
            if expected_events is not None:
                self.assertEqual(events, expected_events)
            return record, calls

    def test_replicated_workload_regression_stops_remaining_calls_and_retains_raw(self):
        expected = [("workload", "first"), ("workload", "tail"), ("workload", "first")]
        record, calls = self.measure(mismatch=True, expected_events=expected)
        self.assertEqual(calls, expected)
        self.assertEqual(record["module_iterations"], {})
        self.assertEqual(record["phase_seconds"]["module_calibration"], 0.0)
        self.assertEqual(record["phase_seconds"]["module_sampling"], 0.0)
        self.assertEqual(record["decision"]["decision"], pv.REJECT)
        self.assertFalse(record["sampling_complete"])
        self.assertEqual(record["incomplete_entities"], ["tail", "json", "csv"])
        self.assertEqual(record["stopped_after"], {"run": 2, "entity": "first"})
        self.assertEqual(list(record["entities"]), ["first"])
        self.assertEqual(len(record["raw"]["first"]), 2)
        self.assertTrue(record["raw"]["tail"][0]["mismatch"])
        self.assertEqual(record["decision"]["mismatches"], ["tail"])

    def test_control_comparison_omits_incomplete_absolute_goals(self):
        record, _calls = self.measure(baseline_ref="@control", candidate_ref="@incumbent")
        self.assertEqual(record["decision"]["decision"], pv.REJECT)
        self.assertEqual(record["goals"], {})
        self.assertEqual(record["incomplete_entities"], ["tail", "json", "csv"])
        record, _calls = self.measure(bad_module=True, baseline_ref="@control",
                                     candidate_ref="@incumbent")
        self.assertEqual(record["decision"]["decision"], pv.REJECT)
        self.assertEqual(set(record["goals"]), {"json"})

    def test_output_mismatch_without_regression_retains_complete_checks(self):
        record, calls = self.measure(("neutral", "neutral"), mismatch=True)
        self.assertEqual(len(calls), 8)
        self.assertTrue(record["sampling_complete"])
        self.assertEqual(record["decision"]["decision"], pv.REJECT)
        self.assertEqual(record["decision"]["mismatches"], ["tail"])

    def test_replicated_module_regression_stops_module_tail(self):
        record, calls = self.measure(bad_module=True)
        self.assertEqual(calls[-1], ("module", "json"))
        self.assertEqual(len(calls), 7)
        self.assertEqual(record["incomplete_entities"], ["csv"])

    def test_survivor_completes_workload_replication_before_module_calibration(self):
        expected = [("workload", "first"), ("workload", "tail")] * 2
        expected += [("calibrate", "json"), ("calibrate", "csv")]
        expected += [("module", "json"), ("module", "csv")] * 2
        record, _calls = self.measure(("neutral", "neutral"), expected_events=expected)
        self.assertTrue(record["sampling_complete"])
        self.assertEqual(record["incomplete_entities"], [])
        self.assertEqual(record["module_iterations"], {"json": 37, "csv": 37})
        self.assertEqual(record["module_rounds"], 5)
        self.assertEqual(set(record["entities"]), {"first", "tail", "json", "csv"})
        for observations in record["raw"].values():
            self.assertEqual(len(observations), 2)
        self.assertEqual(record["decision"]["decision"], pv.WITHIN_BUDGET)

    def test_rigorous_survivor_preserves_all_selected_rounds_and_replicates(self):
        record, calls = self.measure(("neutral", "neutral", "neutral"), profile="rigorous")
        self.assertEqual(len(calls), 12)
        self.assertEqual(record["module_rounds"], 10)
        self.assertEqual(record["module_iterations"], {"json": 37, "csv": 37})
        self.assertEqual(record["workload_profile"], "standard")
        self.assertTrue(record["sampling_complete"])
        for observations in record["raw"].values():
            self.assertEqual(len(observations), 3)
        self.assertEqual(record["decision"]["decision"], pv.WITHIN_BUDGET)

    def test_one_worse_then_neutral_keeps_complete_evidence(self):
        record, calls = self.measure(("worse", "neutral"))
        self.assertEqual(len(calls), 8)
        self.assertTrue(record["sampling_complete"])
        self.assertEqual(record["decision"]["regressions"], [])
        self.assertIsNone(record["stopped_after"])

    def test_three_runs_require_all_three_regressions(self):
        record, calls = self.measure(("worse", "worse", "worse"))
        self.assertEqual(len(calls), 5)
        self.assertEqual(record["module_iterations"], {})
        self.assertEqual(record["stopped_after"]["run"], 3)
        record, calls = self.measure(("worse", "worse", "neutral"))
        self.assertEqual(len(calls), 12)
        self.assertTrue(record["sampling_complete"])

    def test_timing_regression_does_not_stop_memory_survivors(self):
        for classes in (("neutral", "neutral"), ("better", "better")):
            with self.subTest(classes=classes):
                record, calls = self.measure(classes)
                self.assertEqual(len(calls), 8)
                self.assertTrue(record["sampling_complete"])
                self.assertEqual(record["decision"]["regressions"], [])

    def test_nonexploratory_modes_and_single_run_keep_full_sampling(self):
        for options in ({"gate": True}, {"self_compare": True}, {"slug_prefix": "goals-"},
                        {"record_baselines": True}, {"memory_only": False}):
            with self.subTest(options=options):
                expected = [("calibrate", "json"), ("calibrate", "csv")]
                expected += [("workload", "first"), ("workload", "tail"),
                             ("module", "json"), ("module", "csv")] * 2
                record, calls = self.measure(expected_events=expected, **options)
                self.assertEqual(len(calls), 8)
                self.assertTrue(record["sampling_complete"])
        expected = [("calibrate", "json"), ("calibrate", "csv"),
                    ("workload", "first"), ("workload", "tail"),
                    ("module", "json"), ("module", "csv")]
        record, calls = self.measure(("worse",), expected_events=expected)
        self.assertEqual(len(calls), 4)
        self.assertTrue(record["sampling_complete"])

    def test_iteration_and_sampling_errors_remove_scratch(self):
        for operation in ("iterations", "workload", "module"):
            with self.subTest(operation=operation), self.assertRaisesRegex(
                    perf.LaneError, operation + " failed"):
                self.measure(("neutral", "neutral"), sampling_error=operation)

    def test_final_stage_and_harness_errors_are_not_hidden_by_early_rejection(self):
        with self.assertRaisesRegex(perf.LaneError, "stage changed"):
            self.measure(guard_error=[None, None, perf.LaneError("stage changed")])
        with self.assertRaisesRegex(perf.LaneError, "harness changed"):
            self.measure(harness_changed=True)


class ReceiptTimingTests(unittest.TestCase):
    def test_build_receipt_keeps_total_and_records_lock_wait(self):
        clock = [0.0]
        @perf.contextlib.contextmanager
        def lease(kind, what, *, timings):
            clock[0] += 7
            timings["lease_wait"] = 7.0
            yield
        def build(*args, **kwargs):
            clock[0] += 23
            return {"interpreter": {"version": "3.16 fixture"}, "rust_extensions_sha256": {}}
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            report = Path(temp) / "report.json"
            fixtures = {"host_lease": lease, "_build_locked": build,
                        "_paths": lambda name: {"report": report, "stage": Path(temp)},
                        "_check_name": lambda name: name}
            for name, value in fixtures.items():
                patches.enter_context(mock.patch.object(perf, name, value))
            patches.enter_context(mock.patch.object(perf.lb, "IS_LINUX", False))
            patches.enter_context(mock.patch.object(perf.lb, "doctor_report", return_value={"ok": True}))
            patches.enter_context(mock.patch.object(perf.time, "monotonic", side_effect=lambda: clock[0]))
            self.assertEqual(perf.build(name="fixture", empty_overlay=False), 0)
            saved = json.loads(report.read_text())
            self.assertEqual(saved["build_seconds"], 30.0)
            self.assertEqual(saved["phase_seconds"], {"lease_wait": 7.0})

    def test_phase_receipt_uses_operation_boundaries_and_preserves_sampling(self):
        clock = [0.0]
        calls = []
        def advance(seconds, result=None):
            clock[0] += seconds
            return result
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root = Path(temp)
            sides = {ref: {"ref": ref, "name": ref[1:], "stage": root / ref[1:],
                           "python": Path("/python"), "report": {}}
                     for ref in ("@control", "@incumbent")}
            @perf.contextlib.contextmanager
            def lease(kind, what, *, timings):
                advance(7)
                timings["lease_wait"] = 7.0
                yield
            def workload(*args, **kwargs):
                calls.append("workload")
                return advance(11, {"kind": "workload", "metrics": {}, "mismatch": False})
            def module(*args, **kwargs):
                calls.append("module")
                return advance(13, {"kind": "module", "metrics": {}, "mismatch": False})
            fixtures = {
                "LANE": root, "host_lease": lease,
                "_harness_identity": lambda: {"source_sha256": "fixture"},
                "selection": lambda **kw: (["workload", "json"], ["workload"], ["json"]),
                "resolve": sides.__getitem__,
                "_verify_stage": lambda side: advance(2),
                "_module_iterations": lambda *a, **kw: advance(3, 37),
                "_run_bench": workload, "_measure_module": module,
                "_host_sample": lambda *a, **kw: advance(17, {"quiet": True}),
            }
            for name, value in fixtures.items():
                patches.enter_context(mock.patch.object(perf, name, value))
            patches.enter_context(mock.patch.object(perf.time, "monotonic", side_effect=lambda: clock[0]))
            patches.enter_context(mock.patch.object(pv, "run_observation", side_effect=lambda x, **kw: x))
            record = perf._measure(
                baseline_ref="@incumbent", candidate_ref="@control", workloads=["workload"],
                modules=["json"], gate=False, runs=2, profile="standard", timing_only=False,
                min_idle=0, self_compare=False, record_baselines=False)
            self.assertEqual(calls, ["workload", "module", "workload", "module"])
            self.assertEqual(record["phase_seconds"], {
                "lease_wait": 7.0, "stage_verification_before": 4.0,
                "controller_preparation": 0.0, "module_calibration": 3.0,
                "workload_runs": 22.0, "module_sampling": 26.0,
                "stage_verification_after": 4.0,
            })
            self.assertEqual(record["seconds"], 117.0)
            self.assertEqual(record["phase_seconds"], json.loads(
                (Path(record["directory"]) / "verdict.json").read_text())["phase_seconds"])


class WorkloadProfileTests(unittest.TestCase):
    def test_matched_executable_requires_matched_homes_before_resolving(self):
        with mock.patch.object(perf, "resolve") as resolve, self.assertRaisesRegex(
                perf.LaneError, "requires --matched-prefix"):
            perf.bench(baseline_ref="@control", candidate_ref="@incumbent", workloads=[],
                       gate=False, runs=2, profile="standard", timing_only=False,
                       min_idle=0, memory_only=True, matched_executable=True)
        resolve.assert_not_called()

    def test_module_launch_alias_preserves_verified_identity_and_checks_observed_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "stage"
            python = stage / "bin/python3.16"
            python.parent.mkdir(parents=True)
            python.write_text("fixture")
            home = Path(temp) / "home"
            home.symlink_to(stage, target_is_directory=True)
            launch = home / "bin/python3.16"
            observation = {"digest": "same", "runtime_executable": str(launch),
                           "runtime_base_executable": str(launch),
                           "runtime_prefix": str(home), "runtime_exec_prefix": str(home)}
            with mock.patch.object(perf.subprocess, "run", return_value=mock.Mock(
                    returncode=0, stdout=json.dumps(observation), stderr="")) as run:
                self.assertEqual(perf._module_sample(
                    python, "collections", 56, Path(temp), runtime_home=home,
                    runtime_executable=launch), observation)
                self.assertEqual(run.call_args.args[0][0], str(launch))
                observation["runtime_base_executable"] = str(python)
                run.return_value.stdout = json.dumps(observation)
                with self.assertRaisesRegex(perf.LaneError, "requested launch"):
                    perf._module_sample(python, "collections", 56, Path(temp),
                                        runtime_home=home, runtime_executable=launch)
            wrong_home = Path(temp) / "other-home"
            wrong_home.symlink_to(stage, target_is_directory=True)
            with mock.patch.object(perf.subprocess, "run") as run, self.assertRaises(perf.LaneError):
                perf._module_sample(python, "collections", 56, Path(temp),
                                    runtime_home=wrong_home, runtime_executable=launch)
            run.assert_not_called()
            home.unlink()
            home.symlink_to(Path(temp) / "other", target_is_directory=True)
            with mock.patch.object(perf.subprocess, "run") as run, self.assertRaises(perf.LaneError):
                perf._module_sample(python, "collections", 56, Path(temp),
                                    runtime_home=home, runtime_executable=launch)
            run.assert_not_called()

    def test_harness_identity_changes_with_source_and_reports_harness_dirty_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            lane = repo / "rust-cpython"
            harness = repo / "benchmarks/harness"
            lane.mkdir()
            harness.mkdir(parents=True)
            for name in ("perf.py", "perf_verdict.py", "perf_modules.py"):
                (lane / name).write_text("fixture")
            (repo / "benchmarks/bench.py").write_text("fixture")
            source = harness / "runner.py"
            source.write_text("fixture")
            with mock.patch.object(perf, "LANE", lane), mock.patch.object(perf, "REPO", repo), \
                    mock.patch.object(perf, "_git", side_effect=lambda *args: "commit" if args[0] == "rev-parse" else " M ../benchmarks/harness/runner.py"):
                first = perf._harness_identity()
                source.write_text("changed")
                second = perf._harness_identity()
            self.assertTrue(first["dirty"])
            self.assertNotEqual(first["source_sha256"], second["source_sha256"])
            self.assertNotEqual(first["files"]["benchmarks/harness/runner.py"], second["files"]["benchmarks/harness/runner.py"])

    def test_harness_change_while_waiting_aborts_before_stage_resolution(self):
        with mock.patch.object(perf, "selection", return_value=([], [], [])), \
                mock.patch.object(perf, "host_lease", return_value=nullcontext()), \
                mock.patch.object(perf, "_harness_identity", side_effect=[{"source_sha256": "first"}, {"source_sha256": "changed"}]), \
                mock.patch.object(perf, "resolve") as resolve, \
                self.assertRaisesRegex(perf.LaneError, "changed while waiting"):
            perf._measure(baseline_ref="@control", candidate_ref="@incumbent", workloads=[], modules=[],
                          gate=False, runs=2, profile="quick", timing_only=False, min_idle=0,
                          self_compare=False, record_baselines=False, memory_only=True)
        resolve.assert_not_called()

    def test_matched_calibration_uses_two_homes_for_one_verified_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "stage"
            stage.mkdir()
            side = {"ref": "@control", "name": "control", "stage": stage,
                    "python": stage / "bin/python", "report": {"commit": "fixture"}}
            with mock.patch.object(perf, "LANE", Path(temp)), \
                    mock.patch.object(perf, "_harness_identity", return_value={"source_sha256": "fixture"}), \
                    mock.patch.object(perf, "selection", return_value=(["json"], [], ["json"])), \
                    mock.patch.object(perf, "host_lease", return_value=nullcontext()), \
                    mock.patch.object(perf, "resolve", return_value=side), \
                    mock.patch.object(perf, "_verify_stage"), \
                    mock.patch.object(perf, "_module_iterations", return_value=37), \
                    mock.patch.object(perf, "_module_sample", return_value=sample(2.0, 1 << 20, 1 << 20)) as measure:
                record = perf._measure(
                    baseline_ref="@control", candidate_ref="@control", workloads=[], modules=["json"],
                    gate=False, runs=2, profile="quick", timing_only=False, min_idle=0,
                    self_compare=True, record_baselines=False, memory_only=True, matched_prefix=True)
            context = record["runtime_prefix_context"]
            homes = [Path(context[key]) for key in ("baseline_home", "candidate_home")]
            self.assertEqual(context["kind"], "matched-python-home")
            self.assertEqual(len(str(homes[0])), len(str(homes[1])))
            self.assertNotEqual(homes[0], homes[1])
            self.assertEqual({call.kwargs["runtime_home"] for call in measure.call_args_list}, set(homes))
            self.assertTrue(all(not home.exists() for home in homes))
            self.assertTrue(stage.exists())

    def test_matched_executable_calibration_keeps_original_identity_and_launches_both_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "stage"
            python = stage / "bin/python3.16"
            python.parent.mkdir(parents=True)
            python.write_text("fixture")
            side = {"ref": "@control", "name": "control", "stage": stage,
                    "python": python, "report": {"commit": "fixture"}}
            with mock.patch.object(perf, "LANE", Path(temp)), \
                    mock.patch.object(perf, "_harness_identity", return_value={"source_sha256": "fixture"}), \
                    mock.patch.object(perf, "selection", return_value=(["json"], [], ["json"])), \
                    mock.patch.object(perf, "host_lease", return_value=nullcontext()), \
                    mock.patch.object(perf, "resolve", return_value=side), \
                    mock.patch.object(perf, "_verify_stage"), \
                    mock.patch.object(perf, "_module_iterations", return_value=37) as setup, \
                    mock.patch.object(perf, "_module_sample", return_value=sample(2.0, 1 << 20, 1 << 20)) as measure:
                record = perf._measure(
                    baseline_ref="@control", candidate_ref="@control", workloads=[], modules=["json"],
                    gate=False, runs=2, profile="quick", timing_only=False, min_idle=0,
                    self_compare=True, record_baselines=False, memory_only=True,
                    matched_prefix=True, matched_executable=True)
            context = record["runtime_prefix_context"]
            launches = [Path(context[key]) for key in ("baseline_executable", "candidate_executable")]
            self.assertEqual(context["kind"], "matched-python-home-and-executable")
            self.assertEqual(len(str(launches[0])), len(str(launches[1])))
            self.assertNotEqual(launches[0], launches[1])
            self.assertEqual(setup.call_args.kwargs["runtime_executable"], launches[0])
            self.assertEqual({call.kwargs["runtime_executable"] for call in measure.call_args_list}, set(launches))
            self.assertTrue(all(call.args[0] == python for call in measure.call_args_list))
            self.assertEqual(side["python"], python)
            self.assertTrue(all(not launch.exists() for launch in launches))
            self.assertTrue(python.exists())

    def test_module_runtime_home_changes_only_explicit_launch_environment(self):
        with mock.patch.object(perf.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout='{"digest":"same"}', stderr="")) as run:
            self.assertEqual(perf._module_sample(
                Path("/stage/bin/python"), "collections", 56, Path("/scratch"),
                runtime_home=Path("/matched/home")), {"digest": "same"})
            self.assertEqual(run.call_args.kwargs["env"]["PYTHONHOME"], "/matched/home")
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["/stage/bin/python", "-s", "-P"])
            self.assertEqual(command[-4:], ["measure", "collections", "--iterations", "56"])

    def test_matched_prefix_requires_memory_only_before_resolving_stages(self):
        with mock.patch.object(perf, "resolve") as resolve, self.assertRaisesRegex(
                perf.LaneError, "requires --memory-only"):
            perf.bench(baseline_ref="@control", candidate_ref="@incumbent", workloads=[],
                       gate=False, runs=2, profile="standard", timing_only=False,
                       min_idle=0, matched_prefix=True)
        resolve.assert_not_called()

    def test_runtime_prefix_aliases_preserve_trees_and_cleanup_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            stages = [Path(temp) / "short", Path(temp) / ("long" * 20)]
            for stage in stages:
                stage.mkdir()
                (stage / "marker").write_text("same")
            with self.assertRaisesRegex(RuntimeError, "stop"), perf._runtime_prefix_aliases(*stages) as homes:
                self.assertEqual(homes[0].parent.parent, perf.SCRATCH)
                self.assertEqual(len(str(homes[0])), len(str(homes[1])))
                self.assertNotEqual(homes[0], homes[1])
                for home, stage in zip(homes, stages):
                    self.assertEqual(home.resolve(), stage.resolve())
                    self.assertEqual((home / "marker").stat().st_ino,
                                     (stage / "marker").stat().st_ino)
                raise RuntimeError("stop")
            self.assertTrue(all(not home.exists() for home in homes))
            self.assertTrue(all(stage.exists() for stage in stages))

    def test_importtime_executes_the_selected_workload(self):
        for workload, module in [('serialization_roundtrip', 'extra'),
                                 ('catalog_json_export', 'catalog_json')]:
            with self.subTest(workload=workload), tempfile.TemporaryDirectory() as temp:
                side = {'python': Path('/python'), 'name': 'fixture'}
                with mock.patch.object(perf, 'LANE', Path(temp)), \
                        mock.patch.object(perf, 'resolve', return_value=side), \
                        mock.patch.object(perf, 'host_lease', return_value=nullcontext()), \
                        mock.patch.object(perf.subprocess, 'run') as run:
                    perf.profile(ref='candidate', workload=workload, module=None,
                                 tool='importtime', iterations=1, seconds=1)
                self.assertEqual(run.call_args.args[0], [
                    '/python', '-X', 'importtime', '-m', f'benchmarks.workloads.{module}',
                    workload, '--iterations', '1'])

    def test_workload_memory_only_flag_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "python_startup"
            output.mkdir()
            result = {"identity": {"name": "python_startup"}}
            (output / "summary.json").write_text(json.dumps({"workloads": [result]}))
            side = {"python": Path("/python"), "ref": "candidate", "name": "candidate",
                    "report": {"commit": "abcdef"}}
            for memory_only in (False, True):
                with self.subTest(memory_only=memory_only), \
                        mock.patch.object(perf, "_label", return_value="candidate"), \
                        mock.patch.object(perf.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                    perf._run_bench(side, side, "python_startup", output=output, profile="standard",
                                    timing_only=False, self_compare=False, record_baseline=None,
                                    memory_only=memory_only)
                    self.assertEqual("--memory-only" in run.call_args.args[0], memory_only)

    def test_rigorous_modules_keep_supported_workload_evidence_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "python_startup"
            output.mkdir()
            result = {"identity": {"name": "python_startup"}}
            (output / "summary.json").write_text(json.dumps({"workloads": [result]}))
            side = {"python": Path("/python"), "ref": "candidate", "name": "candidate",
                    "report": {"commit": "abcdef"}}

            def evidence_runner(command, **kwargs):
                profile = command[command.index("--profile") + 1]
                return mock.Mock(returncode=0 if profile in {"quick", "standard"} else 2)

            with mock.patch.object(perf, "_label", return_value="candidate"), \
                    mock.patch.object(perf.subprocess, "run", side_effect=evidence_runner) as run:
                self.assertEqual(perf._run_bench(
                    side, side, "python_startup", output=output, profile="rigorous",
                    timing_only=False, self_compare=False, record_baseline=None), result)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--profile") + 1], "standard")
            self.assertIn("--evidence", command)
            self.assertNotIn("--timing-only", command)
            self.assertEqual(perf.PROFILE_MODULE_ROUNDS["rigorous"], 10)


class ClassifyTests(unittest.TestCase):
    def test_interval_must_clear_the_practical_floor(self):
        self.assertEqual(pv.classify([0.95, 0.98]), "improved")
        self.assertEqual(pv.classify([0.97, 0.995]), "within budget")
        self.assertEqual(pv.classify([1.02, 1.05]), "regressed")
        self.assertEqual(pv.classify([1.001, 1.009]), "within budget")
        self.assertEqual(pv.classify(None), "inconclusive")

    def test_claims_need_every_run_to_agree(self):
        self.assertEqual(pv.replicate(["improved", "improved"]), "improved")
        self.assertEqual(pv.replicate(["improved", "within budget"]), "within budget")
        self.assertEqual(pv.replicate(["regressed", "regressed"]), "regressed")
        self.assertEqual(pv.replicate(["improved", "regressed"]), "inconclusive")
        self.assertEqual(pv.replicate(["improved", "inconclusive"]), "inconclusive")

    def test_harness_timing_failure_counts_as_worse(self):
        observation = pv.run_observation(summary([0.99, 1.009], status="fail"))
        self.assertEqual(observation["metrics"]["wall"]["class"], "regressed")

    def test_cpu_uses_paired_bootstrap(self):
        observation = pv.run_observation(summary(
            [0.99, 1.0], cpu=([1.0, 1.0, 1.0, 1.0, 1.0], [0.9, 0.91, 0.9, 0.89, 0.9])))
        self.assertEqual(observation["metrics"]["cpu"]["class"], "improved")

    def test_memory_needs_floor_and_noise(self):
        grown = {"peak_rss": {"ratio_candidate_over_baseline": 1.05, "absolute_change": 5e6,
                              "noise_allowance": 1e6, "baseline": {"median": 1e8}, "status": "fail"}}
        within = {"peak_rss": {"ratio_candidate_over_baseline": 1.05, "absolute_change": 5e5,
                               "noise_allowance": 1e6, "baseline": {"median": 1e8}, "status": "pass"}}
        self.assertEqual(pv.run_observation(summary([0.99, 1.0], memory=grown))["metrics"]["peak_rss"]["class"],
                         "regressed")
        self.assertEqual(pv.run_observation(summary([0.99, 1.0], memory=within))["metrics"]["peak_rss"]["class"],
                         "inconclusive")


class VerdictPolicyTests(unittest.TestCase):
    def memory(self, change, noise, baseline=1e8):
        return {"peak_rss": {"ratio_candidate_over_baseline": 1 + change / baseline,
                             "absolute_change": change, "noise_allowance": noise,
                             "baseline": {"median": baseline}, "status": "pass"}}

    def test_one_wide_interval_is_inconclusive_not_neutral(self):
        run = summary([0.8, 1.3])
        self.assertEqual(pv.run_observation(run)["metrics"]["wall"]["class"], "inconclusive")
        decision = pv.decide({"w": verdict(run)}, targets=["w"], runs=1, quiet=True, gate=False)
        self.assertEqual(decision["decision"], pv.INCONCLUSIVE)

    def test_disagreeing_runs_are_inconclusive_not_neutral(self):
        entity = verdict(summary([1.02, 1.08]), summary([0.99, 1.03]))
        self.assertEqual(entity["metrics"]["wall"]["per_run"], ["regressed", "inconclusive"])
        self.assertEqual(entity["metrics"]["wall"]["verdict"], "inconclusive")
        decision = pv.decide({"w": entity}, targets=["w"], runs=2, quiet=True, gate=True)
        self.assertEqual(decision["decision"], pv.INCONCLUSIVE)

    def test_wide_intervals_around_parity_are_unclear_not_met(self):
        def observed(interval, ratios):
            return {"ratios": ratios, "ci95": interval, "class": pv.classify(interval)}
        runs = [{"kind": "module", "mismatch": False, "metrics": {
                    name: observed(interval, ratios) for name in pv.MODULE_MEMORY_METRICS}}
                for interval, ratios in (([0.95, 1.6], [0.95, 1.0, 1.6]), ([0.9, 1.5], [0.9, 1.0, 1.5]))]
        entity = pv.entity_verdict(runs)
        self.assertEqual(entity["metrics"]["load_footprint"]["pooled"]["median"], 1.0)
        goal = pv.goal_status(entity)
        self.assertEqual(goal["status"], "UNCLEAR")
        self.assertEqual(goal["metrics"], {"load_footprint": "UNCLEAR", "working_peak": "UNCLEAR"})

    def test_bench_memory_is_within_budget_up_to_the_allowance(self):
        def reads(change, noise):
            observation = pv.run_observation(summary([0.99, 1.0], memory=self.memory(change, noise)))
            return observation["metrics"]["peak_rss"]["class"]
        self.assertEqual(reads(5e5, 5e5), "within budget")
        self.assertEqual(reads(5e5, 6e5), "inconclusive")
        self.assertEqual(reads(-2e6, 5e5), "improved")
        self.assertEqual(reads(3e6, 5e5), "regressed")


class DecideTests(unittest.TestCase):
    def test_replicated_workload_rss_win_is_accepted_with_neutral_timing(self):
        memory = {'peak_rss': {'ratio_candidate_over_baseline': 0.90,
                              'absolute_change': -2e6, 'noise_allowance': 1e6,
                              'baseline': {'median': 2e7}, 'status': 'pass'}}
        run = summary([0.99, 1.005], cpu=([1.0] * 5, [1.0] * 5), memory=memory)
        entities = {'python_startup': verdict(run, run)}
        decision = pv.decide(entities, targets=['python_startup'], runs=2,
                             quiet=True, gate=True)
        self.assertEqual(decision['decision'], pv.ACCEPT)
        self.assertEqual(decision['improved'], ['python_startup peak_rss'])

    def test_replicated_target_win_is_accepted(self):
        workloads = {"zlib_decode_1m": verdict(summary([0.90, 0.95]), summary([0.91, 0.96])),
                     "python_startup": verdict(summary([0.99, 1.01]), summary([0.99, 1.005]))}
        decision = pv.decide(workloads, targets=["zlib_decode_1m"], runs=2, quiet=True, gate=True)
        self.assertEqual(decision["decision"], pv.ACCEPT)
        self.assertEqual(decision["improved"], ["zlib_decode_1m wall"])

    def test_replicated_guard_regression_rejects_a_win(self):
        workloads = {"zlib_decode_1m": verdict(summary([0.90, 0.95]), summary([0.91, 0.96])),
                     "python_startup": verdict(summary([1.03, 1.05]), summary([1.02, 1.04]))}
        decision = pv.decide(workloads, targets=["zlib_decode_1m"], runs=2, quiet=True, gate=True)
        self.assertEqual(decision["decision"], pv.REJECT)
        self.assertEqual(decision["regressions"], ["python_startup wall"])

    def test_single_run_regression_does_not_reject(self):
        workloads = {"zlib_decode_1m": verdict(summary([0.90, 0.95]), summary([0.91, 0.96])),
                     "python_startup": verdict(summary([1.03, 1.05]), summary([0.99, 1.01]))}
        decision = pv.decide(workloads, targets=["zlib_decode_1m"], runs=2, quiet=True, gate=True)
        self.assertEqual(decision["decision"], pv.INCONCLUSIVE)

    def test_gate_needs_two_runs_and_a_quiet_host(self):
        one = {"zlib_decode_1m": verdict(summary([0.90, 0.95]))}
        self.assertEqual(pv.decide(one, targets=["zlib_decode_1m"], runs=1, quiet=True,
                                   gate=True)["decision"], pv.INCONCLUSIVE)
        self.assertEqual(pv.decide(one, targets=["zlib_decode_1m"], runs=1, quiet=True,
                                   gate=False)["decision"], pv.ACCEPT)
        self.assertEqual(pv.decide(one, targets=["zlib_decode_1m"], runs=1, quiet=False,
                                   gate=False)["decision"], pv.INCONCLUSIVE)

    def test_unstable_and_within_budget(self):
        unstable = {"w": verdict(summary([0.90, 0.95]), summary([1.03, 1.05]))}
        self.assertEqual(pv.decide(unstable, targets=["w"], runs=2, quiet=True, gate=True)["decision"],
                         pv.INCONCLUSIVE)
        within = {"w": verdict(summary([0.98, 1.01]), summary([0.985, 1.0]))}
        self.assertEqual(pv.decide(within, targets=["w"], runs=2, quiet=True, gate=True)["decision"],
                         pv.WITHIN_BUDGET)

    def test_calibration_flags_any_difference(self):
        clean = {"w": verdict(summary([0.99, 1.01]), summary([0.995, 1.004]))}
        self.assertEqual(pv.calibration(clean, runs=2, quiet=True, gate=True)["decision"], "CALIBRATION-OK")
        biased = {"w": verdict(summary([0.95, 0.98]), summary([0.96, 0.98]))}
        self.assertEqual(pv.calibration(biased, runs=2, quiet=True, gate=True)["decision"],
                         "CALIBRATION-FAILED")
        self.assertEqual(pv.calibration(clean, runs=2, quiet=False, gate=True)["decision"],
                         "CALIBRATION-INCONCLUSIVE")

    def test_calibration_needs_every_metric_within_budget(self):
        wide = {"w": verdict(summary([0.97, 1.03]), summary([0.98, 1.02]))}
        self.assertEqual(pv.calibration(wide, runs=2, quiet=True, gate=True)["decision"],
                         "CALIBRATION-INCONCLUSIVE")
        split = {"w": verdict(summary([0.95, 0.98]), summary([1.03, 1.05]))}
        calibration = pv.calibration(split, runs=2, quiet=True, gate=True)
        self.assertEqual(calibration["decision"], "CALIBRATION-FAILED")
        self.assertEqual(calibration["differences"], ["w wall: unstable"])

    def test_inconclusive_guard_blocks_a_target_win(self):
        workloads = {"zlib_decode_1m": verdict(summary([0.90, 0.95]), summary([0.91, 0.96])),
                     "python_startup": verdict(summary([0.9, 1.3]), summary([0.99, 1.005]))}
        decision = pv.decide(workloads, targets=["zlib_decode_1m"], runs=2, quiet=True, gate=True)
        self.assertEqual(decision["decision"], pv.INCONCLUSIVE)
        self.assertEqual(decision["inconclusive"], ["python_startup wall"])

    def test_render_marks_targets_and_decision(self):
        workloads = {"w": verdict(summary([0.90, 0.95], wall_ratios=(0.92, 0.93, 0.94)))}
        text = pv.render(workloads, pv.decide(workloads, targets=["w"], runs=1, quiet=True, gate=False))
        self.assertIn("*w ", text)
        self.assertIn("DECISION: ACCEPT (explore", text)


class ModuleTests(unittest.TestCase):
    def test_module_metrics_pair_by_round_with_memory_floors(self):
        base = [sample(1.0, 100_000, 0), sample(1.0, 100_000, 0), sample(1.0, 100_000, 0)]
        cand = [sample(2.0, 400_000, 10_000), sample(2.1, 400_000, 10_000), sample(1.9, 400_000, 10_000)]
        observation = module(base, cand)
        self.assertEqual(observation["metrics"]["cpu"]["class"], "regressed")
        self.assertEqual(observation["metrics"]["load_footprint"]["class"], "regressed")
        # Both working peaks sit below the 256 KiB floor: equal, not infinite.
        self.assertEqual(observation["metrics"]["working_peak"]["ratios"], [1.0, 1.0, 1.0])
        self.assertFalse(observation["mismatch"])

    def test_digest_disagreement_is_a_mismatch_and_rejects(self):
        observation = module([sample(1.0, digest="a")], [sample(1.0, digest="b")])
        self.assertTrue(observation["mismatch"])
        entities = {"json": pv.entity_verdict([observation])}
        decision = pv.decide(entities, targets=["json"], runs=1, quiet=True, gate=False)
        self.assertEqual(decision["decision"], pv.REJECT)
        self.assertEqual(pv.goal_status(entities["json"])["status"], "MISMATCH")

    def test_known_mismatch_is_reported_not_rejected(self):
        entities = {"compileall_source": pv.entity_verdict([pv.mismatch_observation("workload")]),
                    "w": verdict(summary([0.90, 0.95]))}
        decision = pv.decide(entities, targets=["w"], runs=1, quiet=True, gate=False,
                             known_mismatches=["compileall_source"])
        self.assertEqual(decision["decision"], pv.ACCEPT)
        self.assertIn("documented output differences", " ".join(decision["reasons"]))

    def test_module_target_improves_on_cpu(self):
        runs = [module([sample(1.0)] * 5, [sample(0.8), sample(0.82), sample(0.79), sample(0.81), sample(0.8)])
                for _ in range(2)]
        entities = {"json": pv.entity_verdict(runs)}
        decision = pv.decide(entities, targets=["json"], runs=2, quiet=True, gate=True)
        self.assertEqual(decision["decision"], pv.ACCEPT)
        self.assertEqual(decision["improved"], ["json cpu"])


class GoalTests(unittest.TestCase):
    def _goal(self, ratios):
        runs = [module([sample(1.0, 1 << 20, 1 << 20)] * 5,
                       [sample(value, 1 << 20, 1 << 20) for value in ratios]) for _ in range(2)]
        return pv.goal_status(pv.entity_verdict(runs))

    def test_band(self):
        self.assertEqual(self._goal([2.0, 2.1, 1.9, 2.0, 2.05])["status"], "OVER")
        self.assertEqual(self._goal([0.95, 0.96, 0.94, 0.95, 0.97])["status"], "MET")
        self.assertEqual(self._goal([1.0, 1.0, 1.0, 1.0, 1.0])["status"], "MET")
        # CPU provably under 0.9x, but memory at parity: the module is MET.
        self.assertEqual(self._goal([0.5, 0.52, 0.49, 0.5, 0.51])["status"], "MET")
        self.assertEqual(self._goal([0.5, 0.52, 0.49, 0.5, 0.51])["metrics"]["cpu"], "BEYOND")
        self.assertEqual(self._goal([1.02, 1.3, 0.99, 1.2, 1.01])["status"], "UNCLEAR")


class MemoryOnlyTests(unittest.TestCase):
    def entities(self, *, cpu=2.0, load=0.8, peak=1.0, digest="d"):
        runs = [module([sample(1.0, 1 << 20, 1 << 20)] * 5,
                       [sample(cpu, int(load * (1 << 20)), int(peak * (1 << 20)), digest)] * 5)
                for _ in range(2)]
        return {"json": pv.entity_verdict(runs)}

    def decision(self, entities, runs=2):
        return pv.decide(entities, targets=["json"], runs=runs, quiet=False,
                         gate=True, memory_only=True)

    def test_memory_win_ignores_cpu_regression_and_busy_host(self):
        decision = self.decision(self.entities())
        self.assertEqual(decision["decision"], pv.ACCEPT)
        self.assertEqual(decision["improved"], ["json load_footprint"])
        self.assertEqual(decision["regressions"], [])
        self.assertEqual(decision["acceptance_policy"], "memory-only")

    def test_cpu_win_cannot_qualify_memory_within_budget(self):
        self.assertEqual(self.decision(self.entities(cpu=0.5, load=1.0))["decision"], pv.WITHIN_BUDGET)

    def test_memory_guards_and_mismatches_still_reject(self):
        entities = self.entities()
        entities["guard"] = self.entities(load=1.2)["json"]
        self.assertEqual(self.decision(entities)["decision"], pv.REJECT)
        self.assertEqual(self.decision(self.entities(digest="different"))["decision"], pv.REJECT)

    def test_missing_memory_on_guard_and_single_run_are_inconclusive(self):
        entities = self.entities()
        guard = self.entities(load=1.0)["json"]
        del guard["metrics"]["working_peak"]
        entities["guard"] = guard
        self.assertEqual(self.decision(entities)["decision"], pv.INCONCLUSIVE)
        self.assertEqual(self.decision(self.entities(), runs=1)["decision"], pv.INCONCLUSIVE)

    def test_unstable_memory_is_inconclusive(self):
        entities = self.entities()
        entities["json"]["metrics"]["working_peak"]["verdict"] = "inconclusive"
        self.assertEqual(self.decision(entities)["decision"], pv.INCONCLUSIVE)

    def test_goals_exclude_cpu_and_require_both_memory_metrics(self):
        entity = self.entities()["json"]
        goal = pv.goal_status(entity, memory_only=True)
        self.assertEqual(goal["status"], "MET")
        self.assertEqual(set(goal["metrics"]), {"load_footprint", "working_peak"})
        del entity["metrics"]["working_peak"]
        self.assertEqual(pv.goal_status(entity, memory_only=True)["status"], "UNCLEAR")

    def test_calibration_ignores_timing_but_requires_memory_and_replication(self):
        entities = self.entities(load=1.0)
        self.assertEqual(pv.calibration(entities, runs=2, quiet=False, gate=True,
                                        memory_only=True)["decision"], "CALIBRATION-OK")
        del entities["json"]["metrics"]["working_peak"]
        self.assertEqual(pv.calibration(entities, runs=2, quiet=False, gate=True,
                                        memory_only=True)["decision"], "CALIBRATION-INCONCLUSIVE")

    def test_workload_observation_accepts_memory_without_timing(self):
        memory = {"peak_rss": {"ratio_candidate_over_baseline": 0.8,
                               "absolute_change": -2e6, "noise_allowance": 1e6,
                               "baseline": {"median": 1e7}, "status": "pass"}}
        observation = pv.run_observation({"comparison": {"memory": {"metrics": memory}}},
                                         memory_only=True)
        self.assertEqual(set(observation["metrics"]), {"peak_rss"})
        entities = {"w": pv.entity_verdict([observation] * 2)}
        decision = pv.decide(entities, targets=["w"], runs=2, quiet=False, gate=True,
                             memory_only=True)
        self.assertEqual(decision["decision"], pv.ACCEPT)

    def test_measure_records_policy_and_preserves_sampling_without_host_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            sides = {ref: {"ref": ref, "name": ref[1:], "stage": Path(temp) / ref[1:],
                           "python": Path("/python"), "report": {"commit": "fixture"}}
                     for ref in ("@control", "@incumbent")}
            with mock.patch.object(perf, "LANE", Path(temp)), \
                    mock.patch.object(perf, "_harness_identity", return_value={"source_sha256": "fixture"}), \
                    mock.patch.object(perf, "selection", return_value=(["json"], [], ["json"])), \
                    mock.patch.object(perf, "host_lease", return_value=nullcontext()), \
                    mock.patch.object(perf, "resolve", side_effect=sides.__getitem__), \
                    mock.patch.object(perf, "_verify_stage"), \
                    mock.patch.object(perf, "_host_sample") as host, \
                    mock.patch.object(perf, "_module_iterations", return_value=37) as iterations, \
                    mock.patch.object(perf, "_module_sample", return_value=sample(2.0, 1 << 20, 1 << 20)) as measure:
                record = perf._measure(
                    baseline_ref="@control", candidate_ref="@incumbent", workloads=[], modules=["json"],
                    gate=False, runs=2, profile="quick", timing_only=False, min_idle=90,
                    self_compare=False, record_baselines=False, memory_only=True)
            host.assert_not_called()
            iterations.assert_called_once()
            self.assertEqual(measure.call_count, 2 * 2 * perf.PROFILE_MODULE_ROUNDS["quick"])
            self.assertTrue(all(call.args[2] == 37 for call in measure.call_args_list))
            self.assertEqual(record["host_samples"], [])
            self.assertIsNone(record["decision"]["quiet"])
            self.assertEqual(record["acceptance_policy"], "memory-only")
            self.assertEqual(set(record["entities"]["json"]["metrics"]),
                             {"load_footprint", "working_peak"})
            saved = json.loads((Path(record["directory"]) / "verdict.json").read_text())
            self.assertEqual(saved["decision"]["acceptance_policy"], "memory-only")
            self.assertEqual(saved["goals"]["json"]["status"], "MET")

    def test_known_control_output_difference_remains_exempt_in_memory_only(self):
        entities = self.entities()
        entities["known"] = pv.entity_verdict([pv.mismatch_observation("workload")] * 2)
        decision = pv.decide(entities, targets=["json"], runs=2, quiet=False, gate=True,
                             known_mismatches=["known"], memory_only=True)
        self.assertEqual(decision["decision"], pv.ACCEPT)
        self.assertIn("documented output differences", " ".join(decision["reasons"]))
        entities["unknown"] = pv.entity_verdict([pv.mismatch_observation("workload")] * 2)
        decision = pv.decide(entities, targets=["json"], runs=2, quiet=False, gate=True,
                             known_mismatches=["known"], memory_only=True)
        self.assertEqual(decision["decision"], pv.REJECT)
        self.assertEqual(decision["mismatches"], ["unknown"])

    def test_missing_memory_goal_renders_as_unavailable(self):
        entities = self.entities()
        del entities["json"]["metrics"]["working_peak"]
        goals = {"json": pv.goal_status(entities["json"], memory_only=True)}
        rendered = pv.render_goals(goals, entities)
        self.assertIn("working_peak n/a UNCLEAR", rendered)
        self.assertNotIn("working_peak 0.00x", rendered)

    def test_memory_only_and_timing_only_are_incompatible(self):
        with self.assertRaisesRegex(perf.LaneError, "requires the memory pass"):
            perf.bench(baseline_ref="@control", candidate_ref="@incumbent", workloads=[],
                       gate=False, runs=2, profile="quick", timing_only=True, min_idle=90,
                       memory_only=True)

    def test_cli_passes_memory_policy_for_all_commands(self):
        for command, extra, handler in [
                ("bench", ["--baseline", "@control", "--candidate", "@incumbent"], "bench"),
                ("calibrate", ["--ref", "@incumbent"], "bench"),
                ("goals", [], "goals")]:
            with self.subTest(command=command), mock.patch.object(perf, handler, return_value=0) as call:
                self.assertEqual(perf.main([command, *extra, "--memory-only"]), 0)
                self.assertTrue(call.call_args.kwargs["memory_only"])


class IncrementalPlanTests(unittest.TestCase):
    def test_changed_removed_and_clean_only(self):
        plan = perf.incremental_plan(
            {"Modules/_json_rs/src/lib.rs": b"new", "Lib/json/__init__.py": b"same",
             "Modules/Setup.local": b"changed", "Makefile.pre.in": b"same"},
            {"Modules/_json_rs/src/lib.rs": b"old", "Lib/json/__init__.py": b"same",
             "Modules/Setup.local": b"before", "Makefile.pre.in": b"same"},
            ["Modules/_json_rs/src/lib.rs", "Lib/json/__init__.py", "Modules/Setup.local",
             "Makefile.pre.in", "Modules/_gone_rs/src/lib.rs"],
        )
        self.assertEqual(plan["changed"], ["Modules/Setup.local", "Modules/_json_rs/src/lib.rs"])
        self.assertEqual(plan["removed"], ["Modules/_gone_rs/src/lib.rs"])
        self.assertEqual(plan["clean_only"], ["Modules/Setup.local"])

    def test_new_file_is_a_change(self):
        plan = perf.incremental_plan({"Lib/new.py": b"x"}, {"Lib/new.py": None}, [])
        self.assertEqual(plan, {"changed": ["Lib/new.py"], "removed": [], "clean_only": []})


class CPlacementIncrementalTests(unittest.TestCase):
    original = b'*@MODULE_BUILDTYPE@*\n@MODULE__JSON_TRUE@_json _json.c\n'
    builtin = b'*@MODULE_BUILDTYPE@*\n@MODULE__JSON_TRUE@*static*\n@MODULE__JSON_TRUE@_json _json.c\n@MODULE__JSON_TRUE@*@MODULE_BUILDTYPE@*\n'

    def test_both_directions_invalidate_same_object_and_obsolete_extension(self):
        for before, after, static in ((self.original, self.builtin, True),
                                      (self.builtin, self.original, False)):
            with self.subTest(static=static), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build, stage = root / 'build', root / 'stage'
                (build / 'Modules').mkdir(parents=True)
                (build / 'lib.fixture').mkdir()
                stage.mkdir()
                (build / 'pybuilddir.txt').write_text('lib.fixture')
                for relative in ('Modules/_json.o', 'Modules/config.o',
                                 'Modules/_json.fixture.so'):
                    (build / relative).write_text('stale')
                (build / 'lib.fixture/_json.fixture.so').symlink_to('../Modules/_json.fixture.so')
                (stage / 'obsolete.so').write_text('old stage')
                plan = perf.c_placement_plan(before, after, self.original)
                self.assertEqual(plan, {'_json': ('Modules/_json.o',)})
                perf.invalidate_c_placement(build, stage, plan)
                self.assertFalse((build / 'Modules/_json.o').exists())
                self.assertFalse((build / 'Modules/config.o').exists())
                self.assertFalse((build / 'lib.fixture/_json.fixture.so').is_symlink())
                self.assertFalse((build / 'Modules/_json.fixture.so').exists())
                self.assertEqual(list(stage.iterdir()), [])

    def test_changed_sources_flags_rust_rows_or_restore_are_rejected(self):
        for invalid in (self.builtin.replace(b'_json.c', b'other.c'),
                        self.builtin.replace(b'_json.c', b'_json.c -DNEW'),
                        self.builtin.replace(b'_json.c', b'_json/Cargo.toml'),
                        self.builtin.replace(b'*@MODULE_BUILDTYPE@*\n', b'*shared*\n'),
                        self.builtin.replace(b'@MODULE__JSON_TRUE@*static*', b'@MODULE_ARRAY_TRUE@*static*'),
                        self.builtin.replace(b'@MODULE__JSON_TRUE@*@MODULE_BUILDTYPE@*',
                                             b'@MODULE_ARRAY_TRUE@*@MODULE_BUILDTYPE@*')):
            with self.subTest(invalid=invalid), self.assertRaises(perf.LaneError):
                perf.c_placement_plan(self.original, invalid, self.original)

    def test_real_guarded_wrappers_preserve_disabled_and_following_mode(self):
        for guard, name, source, comment in (
                ('@MODULE__STRUCT_TRUE@', '_struct', '_struct.c',
                 '# Keep the original C module in the interpreter and restore the configured mode.\n'),
                ('@MODULE_BINASCII_TRUE@', 'binascii', 'binascii.c',
                 ('# Keep the original C module in the interpreter while preserving its optional\n'
                 '# configuration and restoring the configured mode for subsequent modules.\n'))):
            row = f'{guard}{name} {source}\n'
            original = ('*@MODULE_BUILDTYPE@*\n' + row + '@MODULE_ARRAY_TRUE@array arraymodule.c\n').encode()
            wrapped = ('*@MODULE_BUILDTYPE@*\n' + comment + guard + '*static*\n' + row +
                       guard + '*@MODULE_BUILDTYPE@*\n' + '@MODULE_ARRAY_TRUE@array arraymodule.c\n').encode()
            plan = perf.c_placement_plan(original, wrapped, original)
            self.assertEqual(set(plan), {name})
            self.assertEqual(perf.c_placement_plan(wrapped, original, original), plan)
            for substitution in ('', '#'):
                configured = wrapped.decode().replace(guard, substitution).replace('@MODULE_BUILDTYPE@', 'shared')
                effective = [line for line in configured.splitlines() if line and not line.startswith('#')]
                mode = 'static'
                modes = {}
                for line in effective:
                    if line.startswith('*'):
                        mode = line.strip('*')
                    elif not line.startswith('@'):
                        modes[line.split()[0]] = mode
                self.assertEqual(modes.get(name), 'static' if not substitution else None)
                self.assertEqual(mode, 'shared')

    def test_object_shared_with_unmoved_row_is_rejected(self):
        original = self.original + b'@MODULE_ALIAS_TRUE@alias _json.c\n'
        wrapped = self.builtin + b'@MODULE_ALIAS_TRUE@alias _json.c\n'
        with self.assertRaises(perf.LaneError):
            perf.c_placement_plan(original, wrapped, original)

    def test_generated_registration_and_compile_rule_must_agree(self):
        for static in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                build = Path(tmp)
                (build / 'Modules').mkdir()
                flag = 'PY_BUILTIN_MODULE_CFLAGS' if static else 'PY_STDMODULE_CFLAGS'
                shared = '' if static else '_json'
                (build / 'Makefile').write_text(
                    f'MODSHARED_NAMES= {shared}\nModules/_json.o: source; clang $({flag}) -c source -o Modules/_json.o\n')
                (build / 'Modules/config.c').write_text('{"_json", PyInit__json},' if static else '')
                perf._verify_c_placement_rules(build, {'_json': ('Modules/_json.o',)}, {'_json': static})
                (build / 'Modules/config.c').write_text('' if static else '{"_json", PyInit__json},')
                with self.assertRaises(perf.LaneError):
                    perf._verify_c_placement_rules(build, {'_json': ('Modules/_json.o',)}, {'_json': static})

    def test_mixed_build_system_or_rust_changes_stay_clean_only(self):
        for name in ('Modules/makesetup', 'Modules/Setup.local', 'Modules/_json_rs/src/lib.rs'):
            plan = perf.incremental_plan(
                {'Modules/Setup.stdlib.in': self.builtin, name: b'new'},
                {'Modules/Setup.stdlib.in': self.original, name: b'old'},
                ['Modules/Setup.stdlib.in', name])
            self.assertIn('Modules/Setup.stdlib.in', plan['clean_only'])
            self.assertNotEqual(plan['changed'], ['Modules/Setup.stdlib.in'])

    def test_actual_compiler_flags_required_in_both_directions(self):
        for static in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / 'install.log'
                plan = {'_json': ('Modules/_json.o',)}
                correct = '-DPy_BUILD_CORE_BUILTIN ' if static else ''
                log.write_text(f'clang {correct}-c /source/Modules/_json.c -o Modules/_json.o\n')
                perf.verify_c_placement_compilation(log, plan, {'_json': static})
                wrong = '' if static else '-DPy_BUILD_CORE_BUILTIN '
                log.write_text(f'clang {wrong}-c /source/Modules/_json.c -o Modules/_json.o\n')
                with self.assertRaises(perf.LaneError):
                    perf.verify_c_placement_compilation(log, plan, {'_json': static})


class CargoCommandTests(unittest.TestCase):
    def run_cargo(self, arguments=("metadata",), *, report=None, returncode=0, staged=None,
                  previous_files=("Modules/_fixture_rs/src/lib.rs",), installed=b"old"):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root = Path(temp)
            paths = {name: root / name for name in (
                "source_parent", "overlay_staging", "overlay_manifest", "build", "stage", "report")}
            source = paths["source_parent"] / "cpython-fixture"
            source.mkdir(parents=True)
            (source / "configure").write_text("")
            (source / "Modules/_fixture_rs/src").mkdir(parents=True)
            (source / "Modules/_fixture_rs/src/lib.rs").write_bytes(installed)
            paths["build"].mkdir()
            (paths["build"] / "Makefile").write_text("")
            paths["overlay_manifest"].write_text(json.dumps(list(previous_files)))
            if report is not None:
                paths["report"].write_text(json.dumps(report))
            if staged is None:
                staged = {"Modules/_fixture_rs/src/lib.rs": b"new"}
            self.cargo_runs = 0
            def stage(_paths, _route):
                for name, data in staged.items():
                    target = paths["overlay_staging"] / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                return {"files": len(staged)}, sorted(staged)
            toolchain = mock.Mock(llvm_prefix=root / "llvm")
            sandbox = mock.Mock()
            sandbox.environment.side_effect = lambda env: {**env, "SEALED": "1"}
            leases = []
            def lease(kind, what, **kwargs):
                leases.append(kind)
                return nullcontext()
            patches.enter_context(mock.patch.object(perf, "_paths", return_value=paths))
            patches.enter_context(mock.patch.object(perf, "_stage_overlay", side_effect=stage))
            patches.enter_context(mock.patch.object(perf, "_perf_flags",
                                                    return_value={"CPPFLAGS": "-I", "CFLAGS": "-O2"}))
            patches.enter_context(mock.patch.object(perf, "host_lease", side_effect=lease))
            patches.enter_context(mock.patch.object(perf.lb, "IS_LINUX", False))
            patches.enter_context(mock.patch.object(perf.lb, "_toolchain", return_value=(toolchain, "target")))
            patches.enter_context(mock.patch.object(perf.lb, "_llvm_ready"))
            patches.enter_context(mock.patch.object(perf.lb, "_sealed_sandbox", return_value=sandbox))
            patches.enter_context(mock.patch.object(
                perf.lb, "_environment", return_value={"CARGO_HOME": "/cargo-home", "CPPFLAGS": "-I",
                                                       "CFLAGS": "-O2"}))
            def cargo_process(*args, **kwargs):
                self.cargo_runs += 1
                self.source_seen_by_cargo = (source / "Modules/_fixture_rs/src/lib.rs").read_bytes()
                return mock.Mock(returncode=returncode)
            run = patches.enter_context(mock.patch.object(perf.subprocess, "run", side_effect=cargo_process))
            status = perf.cargo(name="fixture", arguments=list(arguments))
            self.build_environment = perf._build_environment(
                toolchain, mock.Mock(), paths, workers=perf._worker_count(None), sandbox=sandbox)
            return status, run, leases, source, paths

    def test_missing_build_is_refused(self):
        with self.assertRaisesRegex(perf.LaneError, "no perf build report"):
            self.run_cargo()

    def test_empty_overlay_build_is_refused(self):
        with self.assertRaisesRegex(perf.LaneError, "pristine control"):
            self.run_cargo(report={"configured": True, "pristine": True})

    def test_unconfigured_build_is_refused(self):
        with self.assertRaisesRegex(perf.LaneError, "no configured candidate build"):
            self.run_cargo(report={"configured": False, "pristine": False})

    def test_cargo_runs_with_the_build_environment_in_the_assembled_source(self):
        status, run, leases, source, paths = self.run_cargo(
            report={"configured": True, "pristine": False})
        self.assertEqual(status, 0)
        self.assertEqual(leases, ["build"])
        expected = self.build_environment
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], str(perf.lb.CARGO_HOME / "bin" / "cargo"))
        self.assertEqual(run.call_args.kwargs["env"], expected)
        self.assertEqual(run.call_args.kwargs["cwd"], source)
        self.assertEqual(expected["PYTHON_BUILD_DIR"], str(paths["build"]))
        self.assertEqual(expected["SEALED"], "1")

    def test_arguments_are_forwarded_verbatim(self):
        arguments = ["test", "-p", "_bz2_rs", "--offline", "--", "--nocapture", "--"]
        _status, run, *_ = self.run_cargo(arguments, report={"configured": True, "pristine": False})
        self.assertEqual(run.call_args.args[0][1:], arguments)
        with mock.patch.object(perf, "cargo", return_value=0) as call:
            self.assertEqual(perf.main(["cargo", "--name", "fixture", "--", *arguments]), 0)
        self.assertEqual(call.call_args.kwargs, {"name": "fixture", "arguments": arguments})

    def test_exit_status_is_cargo_status(self):
        status, *_ = self.run_cargo(report={"configured": True, "pristine": False}, returncode=101)
        self.assertEqual(status, 101)
        with mock.patch.object(perf, "cargo", return_value=101):
            self.assertEqual(perf.main(["cargo", "--name", "fixture", "--", "test"]), 101)

    def test_changed_overlay_files_sync_before_cargo_runs(self):
        self.run_cargo(report={"configured": True, "pristine": False})
        self.assertEqual(self.source_seen_by_cargo, b"new")

    def test_build_system_changes_and_removals_are_refused_without_running_cargo(self):
        report = {"configured": True, "pristine": False}
        with self.assertRaisesRegex(perf.LaneError, "build-system changes: .*configure.ac"):
            self.run_cargo(report=report, staged={"configure.ac": b"new"}, previous_files=("configure.ac",))
        self.assertEqual(self.cargo_runs, 0)
        with self.assertRaisesRegex(perf.LaneError, "Removed: .*lib.rs"):
            self.run_cargo(report=report, staged={})
        self.assertEqual(self.cargo_runs, 0)


class IncrementalRecipeTests(unittest.TestCase):
    def exercise(self, incremental, *, bad_install=False, failed_install=False, source_std=True,
                 empty_overlay=False, missing_current_member=False, aggregate=True, missing_aggregate=False,
                 late_placement_failure=False, abrupt_placement_failure=False, route="private",
                 linux=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root = Path(temp)
            if linux:
                patches.enter_context(mock.patch.object(perf.lb, "IS_LINUX", True))
                patches.enter_context(mock.patch.object(perf.lb, "_build_mpdecimal", return_value={}))
                patches.enter_context(mock.patch.object(
                    perf.lb.lane_linux, "setup_local_with_shared_ssl_rs", side_effect=lambda text: text))
            else:
                patches.enter_context(mock.patch.object(perf.lb, "IS_LINUX", False))
            patches.enter_context(mock.patch.object(
                perf.lb, "TARGET",
                "x86_64-unknown-linux-gnu" if linux else "aarch64-apple-darwin"))
            source = root / "source"
            source.mkdir()
            (source / "Cargo.lock").write_text("locked fixture")
            aggregate_manifest = source / 'Modules/cpython-rust-source-aggregate356/Cargo.toml'
            (source / 'Cargo.toml').write_text('[workspace]\nmembers = ' +
                ('["Modules/cpython-rust-source-aggregate356"]\n' if aggregate else '[]\n'))
            if aggregate and not missing_aggregate:
                aggregate_manifest.parent.mkdir(parents=True)
                aggregate_manifest.write_text('[package]\nname = "cpython-rust-source-aggregate356"\n')
            (source / "Lib").mkdir()
            (source / "Lib/pickle.py").write_text("old")
            paths = {name: root / name for name in (
                "source_parent", "overlay_staging", "overlay_manifest", "build", "stage",
                "cargo_log", "configure_log", "build_log", "install_log", "compression_log")}
            paths["build"].mkdir()
            (paths["build"] / "Makefile").write_text("CARGO_PROFILE=dev\n")
            paths["overlay_manifest"].write_text('["Lib/pickle.py"]')
            (paths["overlay_staging"] / "Lib").mkdir(parents=True)
            (paths["overlay_staging"] / "Lib/pickle.py").write_text("new")
            paths["build_log"].write_text("stale cargo --profile release")
            toolchain = mock.Mock(make=Path("/make"), llvm_prefix=root / "llvm")
            toolchain.identity.return_value = {"fixture": True}
            sandbox = mock.Mock()
            sandbox.environment.side_effect = lambda env: env
            proof = mock.Mock()
            proof.verify_builtin_artifacts.return_value = {"fixture": "verified"}
            members = {"_base64", "cpython-sys", "_fixture_rs"}
            commands = []
            configure_environments = []
            preparations = []
            def command(argv, *, log, **kwargs):
                commands.append(argv)
                if argv[0].endswith("/configure"):
                    configure_environments.append(kwargs["env"])
                if "fetch" in argv and missing_aggregate:
                    self.assertIn('--locked', argv)
                    self.assertIn('--manifest-path', argv)
                    self.assertIn('cpython-rust-source-aggregate356', (source / 'Cargo.toml').read_text())
                    raise perf.LaneError('Cargo declared aggregate dependency is missing')
                if "install" in argv and failed_install:
                    log.write_text("install failed")
                    raise perf.LaneError("install failed")
                compiled = members - ({"_fixture_rs"} if "install" in argv and missing_current_member else set())
                transcript = "cargo --profile release\n" + "".join(
                    "Compiling " + member + " v0.1.0\n" for member in sorted(compiled))
                log.write_text(transcript + ("cargo --profile dev" if "install" in argv and bad_install else ""))
                if "install" in argv:
                    python = paths["stage"] / "bin/python3.16"
                    python.parent.mkdir(parents=True, exist_ok=True)
                    python.write_text("installed fixture")
            def prepare_std(prepared_paths, env):
                preparations.append(prepared_paths)
                self.assertTrue(source_std and aggregate, "ordinary workspace must not prepare source Std")
                self.assertTrue(prepared_paths["build"].is_dir())
                if not incremental:
                    self.assertTrue(prepared_paths["stage"].is_dir())
                    self.assertFalse((prepared_paths["build"] / "Makefile").exists())
                env["PYTHON_BUILD_RUST_STD_SOURCE"] = "verified source"
                return {"sha256": "fixture"}
            fixtures = {
                "_prepare_csv_source_std": prepare_std,
                "_install_source_aggregate": lambda *a: {"fixture": "verified"},
                "_git_state": lambda: {"commit": "fixture"},
                "_existing_source": lambda parent: source,
                "_stage_overlay": lambda paths, route: ({"sha256": "fixture"}, ["Lib/pickle.py"]),
                "_builtin_artifact_verifier": lambda: (proof, []),
                "_perf_flags": lambda *a, **k: {"CPPFLAGS": "", "CFLAGS": "fixture flags"},
                "_overlay_members": lambda route: {"_fixture_rs"},
                "_build_python": lambda build: build / "python.exe",
            }
            for name, value in fixtures.items():
                patches.enter_context(mock.patch.object(perf, name, value))
            for name, value in {
                "_toolchain": (toolchain, "fixture"), "_environment": {},
                "_sealed_sandbox": sandbox, "_workspace_members": members,
                "_read_lock": ({"commit": "source"}, mock.Mock(sha256="source hash")),
                "_module_report": {"version": "fixture"}, "_rust_identity": {"fixture": True},
            }.items():
                patches.enter_context(mock.patch.object(perf.lb, name, return_value=value))
            patches.enter_context(mock.patch.object(perf.lb, "_llvm_ready"))
            patches.enter_context(mock.patch.object(perf.lb, "_extract_fresh", return_value=source))
            patches.enter_context(mock.patch.object(perf.lb, "_require_command", side_effect=command))
            patches.enter_context(mock.patch.object(perf.lb, "_make_value", return_value="dev"))
            engines = patches.enter_context(mock.patch.object(
                perf.lb, "compression_engine_environment",
                return_value={"ZLIB_LIBS": "/engines/libz.a", "BZIP2_LIBS": "/engines/libbz2.a"}))
            upstream_proof = patches.enter_context(mock.patch.object(perf, "verify_upstream_compression"))
            extensions = patches.enter_context(mock.patch.object(
                perf, "verify_release_artifacts", return_value={str(i): "hash" for i in range(58)}))
            checks = patches.enter_context(mock.patch.object(perf, "_check_logs", wraps=perf._check_logs))
            compiled = patches.enter_context(mock.patch.object(perf, "_built_members", wraps=perf._built_members))
            single_install = incremental or (source_std and aggregate and not empty_overlay)
            if late_placement_failure or abrupt_placement_failure:
                original = CPlacementIncrementalTests.original
                template = 'Modules/Setup.stdlib.in'
                (source / 'Modules').mkdir(exist_ok=True)
                (source / template).write_bytes(original)
                (paths['overlay_staging'] / 'Modules').mkdir()
                (paths['overlay_staging'] / template).write_bytes(CPlacementIncrementalTests.builtin)
                paths['overlay_manifest'].write_text(json.dumps([template]))
                (paths['build'] / 'config.status').write_text('S["MODULE_BUILDTYPE"]="shared"')
                paths['report'] = root / 'report.json'
                paths['report'].write_text(json.dumps({
                    'configured': True, 'pristine': False, 'source_commit': 'source',
                    'source_archive_sha256': 'source hash', 'target': perf.lb.TARGET,
                    'cargo_lock_sha256': perf._sha256_file(source / 'Cargo.lock')}))
                patches.enter_context(mock.patch.object(perf, '_stage_overlay',
                    return_value=({'sha256': 'fixture'}, [template])))
                patches.enter_context(mock.patch.object(perf, '_original_setup_template', return_value=original))
                patches.enter_context(mock.patch.object(perf, '_perf_flags', return_value={
                    'CPPFLAGS': '', 'CFLAGS': 'fixture flags', 'LDFLAGS': ''}))
                def value(path, name):
                    return {'CC': str(toolchain.llvm_prefix / 'bin/clang'),
                            'CONFIGURE_CFLAGS': 'fixture flags', 'CONFIGURE_CPPFLAGS': '',
                            'CONFIGURE_LDFLAGS': '', 'CARGO_PROFILE': 'dev'}[name]
                patches.enter_context(mock.patch.object(perf.lb, '_make_value', side_effect=value))
                def placement_command(argv, **kwargs):
                    command(argv, **kwargs)
                    if 'Makefile' in argv:
                        (paths['build'] / 'Modules').mkdir(exist_ok=True)
                        (paths['build'] / 'Modules/config.c').write_text('generated')
                    if 'install' in argv:
                        with kwargs['log'].open('a') as stream:
                            stream.write('clang -DPy_BUILD_CORE_BUILTIN -c source -o Modules/_json.o\n')
                patches.enter_context(mock.patch.object(perf.lb, '_require_command', side_effect=placement_command))
                patches.enter_context(mock.patch.object(perf, '_verify_c_placement_rules'))
                patches.enter_context(mock.patch.object(perf.lb, '_module_report',
                                                       side_effect=RuntimeError('late metadata failure')))
                patches.enter_context(mock.patch.object(perf.lb, 'doctor_report', return_value={'ok': True}))
                patches.enter_context(mock.patch.object(perf, '_paths', return_value=paths))
                patches.enter_context(mock.patch.object(perf, 'host_lease', return_value=nullcontext()))
                if abrupt_placement_failure:
                    previous = json.loads(paths['report'].read_text())
                    perf._write_json(paths['report'], {'status': 'building', 'configured': True})
                    copy_file = perf._copy_file
                    persisted = []
                    def interrupted_copy(source_file, target_file):
                        persisted.append(json.loads(paths['report'].read_text()))
                        copy_file(source_file, target_file)
                        raise SystemExit('abrupt placement interruption')
                    patches.enter_context(mock.patch.object(perf, '_copy_file', side_effect=interrupted_copy))
                    # Call the inner builder directly: no outer failure handler can
                    # repair the durable state after this simulated process loss.
                    with self.assertRaisesRegex(SystemExit, 'abrupt placement interruption'):
                        perf._build_locked('fixture', paths, empty_overlay=False, incremental=True,
                                           jobs=4, state={'configured': True, 'previous': previous})
                    self.assertFalse(persisted[0]['configured'])
                    self.assertEqual((source / template).read_bytes(), CPlacementIncrementalTests.builtin)
                    self.assertFalse(json.loads(paths['report'].read_text())['configured'])
                    with self.assertRaisesRegex(perf.LaneError, 'no configured candidate'):
                        perf.build(name='fixture', empty_overlay=False, incremental=True, jobs=4)
                    self.assertEqual(commands, [])
                    return
                with self.assertRaisesRegex(RuntimeError, 'late metadata failure'):
                    perf.build(name='fixture', empty_overlay=False, incremental=True, jobs=4)
                failure = json.loads(paths['report'].read_text())
                self.assertEqual(failure['status'], 'failed')
                self.assertEqual(failure['error'], 'late metadata failure')
                self.assertFalse(failure['configured'])
                count = len(commands)
                with self.assertRaisesRegex(perf.LaneError, 'no configured candidate'):
                    perf.build(name='fixture', empty_overlay=False, incremental=True, jobs=4)
                self.assertEqual(len(commands), count)
                return
            if missing_aggregate:
                with self.assertRaisesRegex(perf.LaneError, "Cargo declared aggregate dependency is missing"):
                    perf._build_locked("fixture", paths, empty_overlay=False, jobs=4,
                                       incremental=incremental, state={"configured": True})
                self.assertEqual(preparations, [])
                self.assertFalse(any(argv[0] == "/make" for argv in commands))
                extensions.assert_not_called()
                return
            if bad_install or failed_install or missing_current_member:
                with self.assertRaisesRegex(perf.LaneError, "profile dev|install failed|did not compile Rust members"):
                    perf._build_locked("fixture", paths, empty_overlay=empty_overlay, jobs=4,
                                       incremental=incremental, state={"configured": True})
                extensions.assert_not_called()
                proof.verify_builtin_artifacts.assert_not_called()
                if not missing_current_member:
                    compiled.assert_not_called()
            else:
                report = perf._build_locked("fixture", paths, empty_overlay=empty_overlay, jobs=4,
                                            incremental=incremental, state={"configured": True},
                                            route=route)
                self.assertEqual(report["compression_route"], route)
                self.assertEqual(engines.call_count, int(route == "upstream"))
                self.assertEqual(upstream_proof.call_count, int(route == "upstream"))
                if route == "upstream":
                    self.assertEqual(engines.call_args.kwargs["profile"], "release")
                    self.assertEqual(report["compression_engine_environment"], engines.return_value)
                    self.assertEqual([environment["ZLIB_LIBS"] for environment in configure_environments],
                                     ["/engines/libz.a"])
                else:
                    self.assertIsNone(report["compression_engine_environment"])
                    self.assertFalse(any("ZLIB_LIBS" in environment for environment in configure_environments))
                self.assertEqual(len(report["rust_extensions_sha256"]), 58)
                self.assertEqual(report["rust_builtin_artifacts"], {} if empty_overlay else {"fixture": "verified"})
                self.assertEqual(report["stage_identity"], perf.tree_digest(paths["stage"]))
                self.assertEqual(report["incremental"], incremental)
                self.assertEqual(report["rust_source_aggregate"], {"fixture": "verified"} if source_std and aggregate and not empty_overlay else None)
                self.assertEqual((source / "Lib/pickle.py").read_text(), "old" if empty_overlay else "new")
                self.assertEqual(proof.validate_builtin_source.call_count,
                             int(not empty_overlay and not linux))
                self.assertEqual(proof.verify_builtin_artifacts.call_count, int(not empty_overlay))
                extensions.assert_called_once()
                checks.assert_called_once_with(paths["install_log"] if single_install else paths["build_log"],
                                               paths["install_log"])
            if not incremental and not bad_install and not failed_install:
                compiled.assert_called_once_with(paths["install_log"] if single_install else paths["build_log"], members)
            self.assertEqual(len(preparations), int(source_std and aggregate and not empty_overlay))
            make = [argv for argv in commands if argv[0] == "/make"]
            self.assertEqual(make, ([["/make", "-j4", "install", *perf.MAKE_VARS]] if single_install else
                                   [["/make", "-j4", *perf.MAKE_VARS],
                                    ["/make", "install", *perf.MAKE_VARS]]))
            if single_install:
                self.assertFalse(paths["build_log"].exists())

    def test_abrupt_placement_mutation_persists_nonresumable_state_before_copy(self):
        self.exercise(True, aggregate=False, abrupt_placement_failure=True)

    def test_late_placement_report_failure_keeps_failed_state_and_refuses_retry(self):
        self.exercise(True, aggregate=False, late_placement_failure=True)

    def test_incremental_builds_and_installs_once_then_verifies_artifacts(self):
        self.exercise(True)

    def test_clean_source_std_builds_and_installs_once_then_verifies_current_members(self):
        self.exercise(False)

    def test_clean_pristine_control_keeps_separate_build_and_install(self):
        self.exercise(False, empty_overlay=True)

    def test_clean_rejects_missing_current_member_despite_stale_release_transcript(self):
        self.exercise(False, missing_current_member=True)

    def test_clean_rejects_current_dev_transcript(self):
        self.exercise(False, bad_install=True)

    def test_clean_install_failure_does_not_claim_artifact_proof(self):
        self.exercise(False, failed_install=True)

    def test_linux_keeps_existing_release_route_without_source_std_preparation(self):
        self.exercise(False, source_std=False, linux=True)

    def test_incremental_rejects_current_dev_transcript_despite_stale_release_log(self):
        self.exercise(True, bad_install=True)

    def test_upstream_route_links_the_compression_engines_before_configure(self):
        self.exercise(False, route="upstream")

    def test_incremental_install_failure_does_not_claim_artifact_proof(self):
        self.exercise(True, failed_install=True)


    def test_accepted_nonaggregate_clean_uses_ordinary_release_build(self):
        self.exercise(False, aggregate=False)

    def test_accepted_nonaggregate_incremental_skips_source_std_preparation(self):
        self.exercise(True, aggregate=False)

    def test_declared_missing_aggregate_dependency_fails_locked_fetch(self):
        self.exercise(False, missing_aggregate=True)


class SourceAggregateInstallTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(perf.lb, "IS_LINUX", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preparation_uses_named_input_owner_and_lock_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {"source_parent": root / "source"}
            env = {"CPPFLAGS": "existing"}
            metadata = {"compiler_revision": "revision", "library_cargo_lock_sha256": "lock"}
            with mock.patch.object(perf.lb, "_rust_std_source_environment", create=True,
                                   return_value={"PYTHON_BUILD_RUST_STD_SOURCE": "verified"}) as prepare, \
                 mock.patch.object(perf.lb, "_read_rust_std_lock", create=True,
                                   return_value=(metadata, mock.Mock(sha256="archive"))):
                result = perf._prepare_csv_source_std(paths, env)
            prepare.assert_called_once_with(root / "rust-std-source")
            self.assertEqual(result, {"sha256": "archive", **metadata})
            self.assertEqual(env, {"CPPFLAGS": "existing", "PYTHON_BUILD_RUST_STD_SOURCE": "verified"})

    def exercise(self, defect=None, *, already_finalized=False):
        with tempfile.TemporaryDirectory() as raw, ExitStack() as patches:
            lane = Path(raw).resolve()
            paths = perf._paths('fixture', lane)
            build, stage = paths['build'], paths['stage']
            source = paths['source_parent'] / 'cpython'
            source.mkdir(parents=True)
            names = ('_csv_rs', '_json_rs', '_typing_rs', '_tokenize_rs',
                     '_datetime_rs', '_threading_rs', '_uuid_rs', '_collections_rs', '_sqlite3_rs', '_warnings_rs', '_socket_rs')
            basename = 'libcpython_rust_source_aggregate356.dylib'
            release = build / 'target' / perf.lb.TARGET / 'release' / basename
            final = build / 'source-aggregate356/final' / basename
            canonical = build / 'Modules' / basename
            dynload = stage / 'lib/python3.16/lib-dynload'
            staged = dynload / basename
            def record(path):
                return {'path': str(path), 'sha256': perf._sha256_file(path), 'size': path.stat().st_size}
            for path in (release, final, canonical):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'final signed aggregate')
            dynload.mkdir(parents=True)
            if already_finalized:
                staged.write_bytes(release.read_bytes())
            consumers = {}
            for name in names:
                published = release.parent / ('lib' + name + '.dylib')
                published.symlink_to(basename)
                helper = canonical.parent / (name + '.cpython-316-darwin.so')
                helper.symlink_to(basename)
                installed = dynload / helper.name
                if already_finalized:
                    installed.symlink_to(basename)
                else:
                    installed.write_bytes(release.read_bytes())
                consumers[name] = {**record(published)}
            unrelated = dynload / '_pathlib_rs.cpython-316-darwin.so'
            unrelated.write_bytes(b'unchanged stock pathlib')
            stock_release = release.parent / 'lib_pathlib_rs.dylib'
            stock_release.write_bytes(unrelated.read_bytes())
            layout = {'schema': 2, 'page_size': 16384, 'segments': {
                '__DATA_CONST': {'vmsize': 16384, 'maxprot': 3, 'initprot': 3, 'flags': 16},
                '__DATA': {'vmsize': 16384, 'maxprot': 3, 'initprot': 3, 'flags': 0}},
                'link_surface': {'loads': ['/usr/lib/libSystem.B.dylib'],
                    'exports': {'_PyInit_' + name: 0 for name in names}}}
            receipt = {'schema_version': 2, 'status': 'complete', 'target': perf.lb.TARGET,
                'profile': 'release', 'panic': 'abort', 'allocator': 'System',
                'aggregate': {**record(release), 'final_artifact': record(final),
                    'install_id': '@rpath/' + basename, 'layout': layout}, 'consumers': consumers}
            receipt_path = build / 'source-aggregate356/receipt.json'
            receipt_path.write_text('{"fixture": true}')
            if defect == 'owner':
                paths['source_parent'] = lane / 'foreign-source'
            elif defect == 'canonical-link':
                canonical.unlink()
                canonical.symlink_to(release)
            elif defect == 'canonical-hardlink':
                os.link(canonical, build / 'linked-copy')
            elif defect == 'allocator':
                receipt['allocator'] = 'custom'
            elif defect == 'schema':
                receipt['schema_version'] = 8
            elif defect == 'old-schema':
                receipt['schema_version'] = 1
            elif defect == 'old-layout-schema':
                layout['schema'] = 1
            elif defect == 'roster':
                del consumers['_uuid_rs']
            elif defect == 'old-roster':
                for name in ('_collections_rs', '_sqlite3_rs', '_warnings_rs', '_socket_rs'):
                    del consumers[name]
            elif defect == 'new-installed':
                (dynload / '_socket_rs.cpython-316-darwin.so').write_bytes(b'changed last copy')
            elif defect == 'release':
                release.write_bytes(b'changed')
            elif defect == 'final':
                final.write_bytes(b'changed')
            elif defect == 'installed':
                (dynload / '_csv_rs.cpython-316-darwin.so').write_bytes(b'changed')
            elif defect in ('build-alias', 'release-alias'):
                alias = (canonical.parent / '_csv_rs.cpython-316-darwin.so' if defect == 'build-alias'
                         else release.parent / 'lib_csv_rs.dylib')
                alias.unlink()
                alias.symlink_to(str(canonical if defect == 'build-alias' else release))
            elif defect == 'stage-escape':
                alias = dynload / '_csv_rs.cpython-316-darwin.so'
                alias.unlink()
                alias.symlink_to(release)
            elif defect == 'stage-collision':
                staged.write_bytes(b'foreign')
            elif defect == 'layout':
                layout['segments']['__DATA_CONST']['flags'] = 0
            elif defect == 'page-bound':
                layout['segments']['__DATA']['vmsize'] = 32768
            elif defect == 'initializers':
                del layout['link_surface']['exports']['_PyInit__uuid_rs']
            recipe = mock.Mock()
            recipe.verify_build_receipt.return_value = receipt
            patches.enter_context(mock.patch.object(perf, 'LANE', lane))
            patches.enter_context(mock.patch.object(perf, '_source_aggregate_recipe', return_value=recipe, create=True))
            patches.enter_context(mock.patch.object(perf.lb.macho, 'read_header', return_value=mock.Mock(arch='arm64')))
            patches.enter_context(mock.patch.object(perf.lb.macho, 'signature_status', return_value=(defect != 'signature', 'fixture')))
            patches.enter_context(mock.patch.object(perf.lb.macho, 'dylib_id', return_value=receipt['aggregate']['install_id']))
            patches.enter_context(mock.patch.object(perf.lb.macho, 'rpaths', return_value=[]))
            patches.enter_context(mock.patch.object(perf.lb.macho, 'dependencies', return_value=[
                '/unowned/lib.dylib' if defect == 'load' else '/usr/lib/libSystem.B.dylib']))
            if defect:
                with self.assertRaises(perf.LaneError):
                    perf._install_source_aggregate(source, paths, {'sha256': 'archive'})
                if defect != 'stage-collision':
                    self.assertFalse(staged.exists())
            else:
                result = perf._install_source_aggregate(source, paths, {'sha256': 'archive'})
                self.assertFalse(staged.is_symlink())
                self.assertEqual(staged.read_bytes(), release.read_bytes())
                for name in names:
                    installed = dynload / (name + '.cpython-316-darwin.so')
                    self.assertTrue(installed.is_symlink())
                    self.assertEqual(os.readlink(installed), basename)
                    self.assertEqual(installed.stat().st_ino, staged.stat().st_ino)
                self.assertEqual(unrelated.read_bytes(), b'unchanged stock pathlib')
                self.assertEqual(result['staged_aggregate_sha256'], record(staged)['sha256'])
                self.assertEqual(result['receipt_sha256'], perf._sha256_file(receipt_path))
                recipe.verify_build_receipt.assert_called_once_with(source, build, perf.lb.TARGET, {'sha256': 'archive'})
                found = perf.verify_release_artifacts(build, stage, {*names, '_pathlib_rs'})
                self.assertEqual({found[name] for name in names}, {record(release)['sha256']})

    def test_restores_one_real_stage_image_after_install_dereferences_aliases(self):
        self.exercise()

    def test_exact_existing_alias_graph_can_be_verified_again(self):
        self.exercise(already_finalized=True)

    def test_rejects_invalid_receipt_artifacts_and_aliases_before_publication(self):
        for defect in ('owner', 'canonical-link', 'canonical-hardlink', 'allocator', 'schema', 'old-schema', 'old-layout-schema', 'roster', 'old-roster', 'new-installed', 'release', 'final', 'installed',
                       'build-alias', 'release-alias', 'stage-escape', 'stage-collision',
                       'layout', 'page-bound', 'initializers', 'signature', 'load'):
            with self.subTest(defect=defect):
                self.exercise(defect)


class BuiltinPreflightTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(perf.lb, "IS_LINUX", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        target = mock.patch.object(perf.lb, "TARGET", "aarch64-apple-darwin")
        target.start()
        self.addCleanup(target.stop)

    def source_failure(self, defect, message, *, reaches_fetch=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root = Path(temp).resolve()
            source = root / 'source'
            carrier = source / 'Modules/cpython-rust-staticlib'
            carrier.mkdir(parents=True)
            (source / 'Modules/Setup.local').write_text('*static*\n_collections_rs\n')
            declaration = carrier / 'builtin-helpers.json'
            declaration.write_text(json.dumps({'schema': 1, 'helpers': ['_collections_rs']}))
            manifest = carrier / 'Cargo.toml'
            manifest.write_text('[dependencies]\n_collections_rs = { path = "../_collections_rs" }\n')
            (source / 'Cargo.toml').write_text('[profile.release]\npanic = "abort"\n')
            recipe = ('cpython-rust-staticlib: cpython-sys\n'
                      '\t$(CARGO_HOME)/bin/cargo build --lib --locked '
                      '--package cpython-rust-staticlib --profile $(CARGO_PROFILE) '
                      '--message-format=json >$(abs_builddir)/rust-staticlib-artifacts.jsonl\n')
            if defect == 'declaration':
                declaration.unlink()
            elif defect == 'dependency':
                manifest.write_text('[dependencies]\n_collections_rs = { path = "../wrong" }\n')
            elif defect == 'receipt':
                recipe = recipe.split(' --message-format=')[0] + '\n'
            elif defect == 'panic':
                (source / 'Cargo.toml').write_text('[profile.release]\npanic = "unwind"\n')
            elif defect == 'masked-status':
                recipe = recipe.rstrip() + ' || true\n'
            (source / 'Makefile.pre.in').write_text(recipe)
            paths = {'source_parent': root / 'parent', 'overlay_manifest': root / 'manifest.json',
                     'build': root / 'build', 'cargo_log': root / 'cargo.log'}
            patches.enter_context(mock.patch.object(perf.lb, '_toolchain', return_value=(None, None)))
            patches.enter_context(mock.patch.object(perf.lb, '_llvm_ready'))
            patches.enter_context(mock.patch.object(perf, '_git_state', return_value={}))
            patches.enter_context(mock.patch.object(perf, '_common_dir', return_value=root / '.git'))
            patches.enter_context(mock.patch.object(perf, '_git', return_value=str(root / '.git')))
            patches.enter_context(mock.patch.object(perf.lb, '_extract_fresh', return_value=source))
            patches.enter_context(mock.patch.object(perf, '_stage_overlay', return_value=({}, [])))
            patches.enter_context(mock.patch.object(perf.lb, '_environment', return_value={}))
            command = patches.enter_context(mock.patch.object(
                perf.lb, '_require_command', side_effect=AssertionError('external command reached')))
            error = AssertionError if reaches_fetch else perf.LaneError
            with self.assertRaisesRegex(error, message):
                perf._build_locked('fixture', paths, empty_overlay=False, jobs=1,
                                   incremental=False, state={'configured': False})
            if reaches_fetch:
                command.assert_called_once()
                self.assertIn('fetch', command.call_args.args[0])
            else:
                command.assert_not_called()

    def test_missing_declaration_fails_before_fetch(self):
        self.source_failure('declaration', 'no explicit declaration')

    def test_wrong_carrier_dependency_fails_before_fetch(self):
        self.source_failure('dependency', 'outside the declared static carrier')

    def test_missing_genuine_receipt_recipe_fails_before_fetch(self):
        self.source_failure('receipt', 'Cargo JSON receipt')

    def test_non_abort_carrier_fails_before_fetch(self):
        self.source_failure('panic', 'requires abort panic')

    def test_masked_cargo_failure_is_rejected_before_fetch(self):
        self.source_failure('masked-status', 'Cargo JSON receipt')

    def test_valid_source_reaches_fetch_without_claiming_artifact_proof(self):
        self.source_failure(None, 'external command reached', reaches_fetch=True)

    def test_postbuild_verification_still_requires_actual_cargo_receipt(self):
        def read(path, owner):
            if path.name == 'config.c':
                return b''
            self.assertEqual(path.name, 'rust-staticlib-artifacts.jsonl')
            raise builtin_modules.BuiltinArtifactError('actual Cargo receipt missing')
        with mock.patch.object(builtin_modules, 'validate_builtin_source',
                               return_value=['_collections_rs']) as preflight, \
                mock.patch.object(builtin_modules.SourceArtifacts, 'read', side_effect=read):
            with self.assertRaisesRegex(builtin_modules.BuiltinArtifactError,
                                        'actual Cargo receipt missing'):
                builtin_modules.verify_builtin_artifacts(Path('/source'), Path('/build'),
                    Path('/stage'), 'aarch64-apple-darwin', ())
            preflight.assert_called_once()

    def test_routes_outside_existing_declaration_contract_need_no_new_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp).resolve()
            (source / 'Modules').mkdir()
            (source / 'Modules/Setup.local').write_text('*static*\n_typing_rs\n')
            self.assertEqual(builtin_modules.validate_builtin_source(
                source, 'aarch64-apple-darwin', ()), [])
            self.assertEqual(builtin_modules.verify_builtin_artifacts(
                source, source / 'build', source / 'stage', 'aarch64-apple-darwin', ()), {})


class TreeAndArtifactTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(perf.lb, "IS_LINUX", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_built_members_survive_interleaved_cargo_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "build.log"
            log.write_text(
                "CompilingCARGO_TARGET_DIR=/tmp/target make extension\n"
                " _urllib_parse_rs v0.1.0 (/tmp/source)\n"
                "Running `CARGO_PKG_NAME=_urllib_parse_rs /tmp/rustc "
                "--crate-name _urllib_parse_rs --crate-type cdylib src/lib.rs`\n"
                "Running `CARGO_PKG_NAME=_foreign_rs /tmp/rustc "
                "--crate-name _foreign_rs src/lib.rs`\n"
                "Running `CARGO_PKG_NAME=_unbuilt_rs /tmp/rustc "
                "--crate-name build_script_build build.rs`\n"
                "Fresh _json_rs v0.1.0 (/tmp/source)\n"
            )
            self.assertEqual(
                perf._built_members(log, {"_urllib_parse_rs", "_json_rs", "_unbuilt_rs"}),
                ["_json_rs", "_urllib_parse_rs"],
            )

    def test_tree_digest_tracks_bytes_modes_and_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bin").mkdir()
            (root / "bin" / "python3.16").write_bytes(b"a")
            first = perf.tree_digest(root)["sha256"]
            (root / "bin" / "python3.16").chmod(0o755)
            second = perf.tree_digest(root)["sha256"]
            (root / "bin" / "python3").symlink_to("python3.16")
            third = perf.tree_digest(root)["sha256"]
            self.assertEqual(len({first, second, third}), 3)

    def _layout(self, root: Path, *, installed: bytes, release: bytes, debug: bool) -> tuple[Path, Path]:
        build = root / "build"
        out = build / "target" / perf.lb.TARGET / "release" / "build" / "_json_rs" / "x" / "out"
        out.mkdir(parents=True)
        (out / "lib_json_rs.dylib").write_bytes(release)
        if debug:
            (build / "target" / perf.lb.TARGET / "debug").mkdir()
        dynload = root / "stage" / "lib" / "python3.16" / "lib-dynload"
        dynload.mkdir(parents=True)
        (dynload / "_json_rs.cpython-316-darwin.so").write_bytes(installed)
        (dynload / "math.cpython-316-darwin.so").write_bytes(b"c extension")
        return build, root / "stage"

    def test_release_artifact_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            build, stage = self._layout(Path(temp), installed=b"rel", release=b"rel", debug=False)
            found = perf.verify_release_artifacts(build, stage, {"_json_rs", "cpython-sys"})
            self.assertEqual(set(found), {"_json_rs"})

    def test_debug_artifact_or_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            build, stage = self._layout(Path(temp), installed=b"dbg", release=b"rel", debug=False)
            with self.assertRaisesRegex(perf.LaneError, "release-profile"):
                perf.verify_release_artifacts(build, stage, {"_json_rs"})
        with tempfile.TemporaryDirectory() as temp:
            build, stage = self._layout(Path(temp), installed=b"rel", release=b"rel", debug=True)
            with self.assertRaisesRegex(perf.LaneError, "dev-profile"):
                perf.verify_release_artifacts(build, stage, {"_json_rs"})


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.object(perf, "_lease_dir", return_value=Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _try(self, name: str, operation: int) -> bool:
        with (Path(self.temp.name) / name).open("a+") as handle:
            try:
                fcntl.flock(handle, operation | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(handle, fcntl.LOCK_UN)
            return True

    def test_wait_timing_covers_only_lock_acquisition(self):
        timings = {}
        with mock.patch.object(perf.time, "monotonic", side_effect=[10.0, 16.5]), perf.host_lease("measure", "bench", timings=timings):
            self.assertEqual(timings, {"lease_wait": 6.5})
            self.assertFalse(self._try("host.lock", fcntl.LOCK_SH))
            self.assertFalse(self._try("turnstile.lock", fcntl.LOCK_EX))
        self.assertTrue(self._try("host.lock", fcntl.LOCK_EX))

    def test_builds_share_and_block_measurement(self):
        with perf.host_lease("build", "build a"):
            self.assertTrue(self._try("host.lock", fcntl.LOCK_SH))
            self.assertFalse(self._try("host.lock", fcntl.LOCK_EX))
            self.assertTrue(self._try("turnstile.lock", fcntl.LOCK_EX))
            self.assertEqual([item["what"] for item in perf.lease_holders()], ["build a"])
        self.assertEqual(perf.lease_holders(), [])

    def test_measurement_is_exclusive_and_closes_the_turnstile(self):
        with perf.host_lease("measure", "bench"):
            self.assertFalse(self._try("host.lock", fcntl.LOCK_SH))
            self.assertFalse(self._try("turnstile.lock", fcntl.LOCK_EX))
        self.assertTrue(self._try("host.lock", fcntl.LOCK_EX))


class HostQuietnessTests(unittest.TestCase):
    def test_linux_gate_uses_load_per_core_not_mac_idle(self):
        with mock.patch.object(perf.lb, "IS_LINUX", True), \
                mock.patch.object(perf, "_cpu_idle_percent", return_value=50.0), \
                mock.patch.object(perf, "_on_ac_power", return_value=None), \
                mock.patch.object(perf.os, "getloadavg", return_value=(12.0, 10.0, 8.0)), \
                mock.patch.object(perf.os, "cpu_count", return_value=32):
            sample = perf._host_sample(80.0, wait_seconds=0)
        self.assertTrue(sample["quiet"])
        self.assertEqual(sample["cpu_idle_percent"], 50.0)

    def test_linux_gate_rejects_load_above_the_limit(self):
        with mock.patch.object(perf.lb, "IS_LINUX", True), \
                mock.patch.object(perf, "_cpu_idle_percent", return_value=50.0), \
                mock.patch.object(perf, "_on_ac_power", return_value=None), \
                mock.patch.object(perf.os, "getloadavg", return_value=(30.0, 10.0, 8.0)), \
                mock.patch.object(perf.os, "cpu_count", return_value=32):
            sample = perf._host_sample(80.0, wait_seconds=0)
        self.assertFalse(sample["quiet"])

    def test_macos_gate_still_uses_idle(self):
        with mock.patch.object(perf.lb, "IS_LINUX", False), \
                mock.patch.object(perf, "_cpu_idle_percent", return_value=85.0), \
                mock.patch.object(perf, "_on_ac_power", return_value=True), \
                mock.patch.object(perf.os, "getloadavg", return_value=(0.0, 0.0, 0.0)):
            sample = perf._host_sample(80.0, wait_seconds=0)
        self.assertTrue(sample["quiet"])

    def test_clone_tree_uses_linux_copy(self):
        with mock.patch.object(perf.lb, "IS_LINUX", True), \
                mock.patch.object(perf.subprocess, "run") as run:
            perf._clone_tree(Path("/source"), Path("/target"))
        run.assert_called_once_with(["/bin/cp", "-a", "/source", "/target"], check=True)

    def test_clone_tree_uses_macos_clonefile(self):
        with mock.patch.object(perf.lb, "IS_LINUX", False), \
                mock.patch.object(perf.subprocess, "run") as run:
            perf._clone_tree(Path("/source"), Path("/target"))
        run.assert_called_once_with(["/bin/cp", "-cR", "/source", "/target"], check=True)


class UpstreamCompressionRouteTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(perf.lb, "IS_LINUX", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    setup_local = (
        "*shared*\n_binascii_rs _binascii_rs/Cargo.toml\n"
        "_bz2 _bz2module.c -DPYTHON_RUST_BZ2\n_bz2_rs _bz2_rs/Cargo.toml  # codec\n"
        "_zlib_rs _zlib_rs/Cargo.toml\n*disabled*\n_ssl\n")

    def test_setup_drops_exactly_the_private_compression_rows(self):
        self.assertEqual(perf._upstream_compression_setup(self.setup_local),
                         "*shared*\n_binascii_rs _binascii_rs/Cargo.toml\n*disabled*\n_ssl\n")

    def test_setup_without_a_private_row_is_refused(self):
        with self.assertRaisesRegex(perf.LaneError, "_zlib_rs"):
            perf._upstream_compression_setup(
                self.setup_local.replace("_zlib_rs _zlib_rs/Cargo.toml\n", ""))

    def test_staging_omits_private_adapters_on_the_upstream_route_only(self):
        files = {"Lib/zlib.py": "a", "Modules/_bz2module.c": "b", "Lib/gzip.py": "c",
                 "Modules/Setup.local": self.setup_local}
        def apply_overlay(staging):
            for relative, text in files.items():
                (staging / relative).parent.mkdir(parents=True, exist_ok=True)
                (staging / relative).write_text(text)
            return {"files": len(files)}
        for route, expected in (("private", sorted(files)),
                                ("upstream", ["Lib/gzip.py", "Modules/Setup.local"])):
            with tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(perf.lb, "_apply_overlay", side_effect=apply_overlay):
                paths = {"overlay_staging": Path(temp) / "staging"}
                _, staged = perf._stage_overlay(paths, route)
                self.assertEqual(staged, expected)
                setup = (paths["overlay_staging"] / "Modules/Setup.local").read_text()
                self.assertEqual("_zlib_rs" in setup, route == "private")

    def test_required_members_exclude_private_adapters_on_the_upstream_route_only(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("_zlib_rs", "_bz2_rs", "_gzip_rs"):
                manifest = Path(temp) / "Modules" / name / "Cargo.toml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(f'[package]\nname = "{name}"\n')
            with mock.patch.object(perf.lb, "OVERLAY", Path(temp)):
                self.assertEqual(perf._overlay_members("private"), {"_zlib_rs", "_bz2_rs", "_gzip_rs"})
                self.assertEqual(perf._overlay_members("upstream"), {"_gzip_rs"})

    def test_route_belongs_to_clean_candidate_builds(self):
        for options in ({"incremental": True}, {"empty_overlay": True}):
            with self.subTest(options), self.assertRaisesRegex(perf.LaneError, "--upstream-compression"):
                perf.build(name="fixture", **{"empty_overlay": False, **options},
                           upstream_compression=True)

    def test_recorded_route_must_be_known(self):
        self.assertEqual(perf._report_route({}), "private")
        self.assertEqual(perf._report_route({"compression_route": "upstream"}), "upstream")
        with self.assertRaisesRegex(perf.LaneError, "unknown compression route"):
            perf._report_route({"compression_route": "zlib-ng"})

    def test_stage_with_system_codec_libraries_or_private_adapters_is_refused(self):
        def otool(linked):
            return mock.Mock(stdout="name:\n" + "".join(f"\t{path} (compatibility version 1)\n"
                                                         for path in linked))
        def stage(root, extra=()):
            dynload = root / "lib/python3.16/lib-dynload"
            dynload.mkdir(parents=True)
            for module in ("zlib", "_bz2", "binascii", *extra):
                (dynload / f"{module}.cpython-316-darwin.so").write_text("")
        with tempfile.TemporaryDirectory() as temp:
            stage(Path(temp))
            with mock.patch.object(perf.subprocess, "run", return_value=otool(["/usr/lib/libSystem.B.dylib"])):
                perf.verify_upstream_compression(Path(temp), None)
            with mock.patch.object(perf.subprocess, "run", return_value=otool(["/usr/lib/libz.1.dylib"])), \
                    self.assertRaisesRegex(perf.LaneError, "system codec libraries"):
                perf.verify_upstream_compression(Path(temp), None)
        with tempfile.TemporaryDirectory() as temp:
            stage(Path(temp), extra=("_zlib_rs",))
            with mock.patch.object(perf.subprocess, "run", return_value=otool([])), \
                    self.assertRaisesRegex(perf.LaneError, "private adapters"):
                perf.verify_upstream_compression(Path(temp), None)

    def test_cargo_syncs_overlay_with_the_recorded_route(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            paths = {"report": Path(temp) / "report.json"}
            paths["report"].write_text(json.dumps(
                {"configured": True, "pristine": False, "compression_route": "upstream"}))
            sync = patches.enter_context(mock.patch.object(
                perf, "_plan_overlay_sync", side_effect=LookupError("stop after planning")))
            patches.enter_context(mock.patch.object(perf, "_paths", return_value=paths))
            patches.enter_context(mock.patch.object(perf, "host_lease", return_value=nullcontext()))
            patches.enter_context(mock.patch.object(perf.lb, "IS_LINUX", False))
            with self.assertRaises(LookupError):
                perf.cargo(name="fixture", arguments=["metadata"])
            sync.assert_called_once_with(paths, "upstream")


class SelectionTests(unittest.TestCase):
    def test_gate_adds_guards_and_rejects_unavailable_inputs(self):
        targets, workloads, modules = perf.selection(workloads=["zlib_stream_4k"], modules=["json"], gate=True)
        self.assertEqual(targets, ["json", "zlib_stream_4k"])
        self.assertEqual(workloads, ["zlib_stream_4k", *perf.GATE_WORKLOADS])
        self.assertEqual(modules, ["json"])
        with self.assertRaisesRegex(perf.LaneError, "does not lock"):
            perf.selection(workloads=["pylint_source"], modules=[], gate=False)
        with self.assertRaisesRegex(perf.LaneError, "unknown module"):
            perf.selection(workloads=[], modules=["nosuch"], gate=False)
        with self.assertRaisesRegex(perf.LaneError, "select"):
            perf.selection(workloads=[], modules=[], gate=False)

    def test_every_registered_route_has_a_kernel(self):
        text = (LANE / "ROUTES.md").read_text()
        import re
        routes = re.findall(r"^- \[x\] `([^`]+)`", text, re.MULTILINE)
        self.assertEqual(len(routes), 71)
        self.assertEqual(sorted(routes), sorted(perf.module_routes()))


if __name__ == "__main__":
    unittest.main()
