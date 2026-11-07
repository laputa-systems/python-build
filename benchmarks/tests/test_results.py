"""Focused tests for benchmark result validation, verdicts, and reports."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.harness.models import (
    ResultSchemaError,
    result_from_json,
    result_to_json,
    summary_from_json,
)
from benchmarks.harness.process import (
    KERNEL_REGION_CPU_COVERAGE,
    MAC_REAPED_CPU_COVERAGE,
)
from benchmarks.harness.report import build_summary, render_summary, save_summary
from benchmarks.harness.statistics import (
    compare_workload,
    describe_samples,
    median_absolute_deviation,
    noise_threshold,
    paired_ratios,
)


def workload(
    *,
    candidate_time: tuple[float, ...] = (1.0, 1.001, 0.999),
    candidate_pss: tuple[int, ...] = (100_000, 101_000, 99_000),
    candidate_allocations: tuple[int, ...] = (1000, 1000, 1000),
    candidate_bytes: tuple[int, ...] = (50_000, 50_000, 50_000),
) -> dict:
    return {
        "schema_version": 1,
        "identity": {
            "name": "django_wsgi_request",
            "category": "web",
            "operation": "request",
            "operation_count": 100,
            "dependency_versions": {"Django": "6.1.0"},
        },
        "baseline": {
            "timing": {"samples": [1.0, 1.001, 0.999]},
            "memory": {
                "rounds": [
                    {"peak_pss": 100_000, "peak_rss": 120_000, "peak_private": 80_000},
                    {"peak_pss": 101_000, "peak_rss": 121_000, "peak_private": 81_000},
                    {"peak_pss": 99_000, "peak_rss": 119_000, "peak_private": 79_000},
                ]
            },
            "allocations": {
                "rounds": [
                    {
                        "total_num_allocations": 1000,
                        "total_bytes_allocated": 50_000,
                        "metadata": {"peak_memory": 40_000},
                    }
                    for _ in range(3)
                ]
            },
        },
        "candidate": {
            "timing": {"samples": list(candidate_time)},
            "memory": {
                "rounds": [
                    {
                        "peak_pss": pss,
                        "peak_rss": pss + 20_000,
                        "peak_private": pss - 20_000,
                    }
                    for pss in candidate_pss
                ]
            },
            "allocations": {
                "rounds": [
                    {
                        "total_num_allocations": count,
                        "total_bytes_allocated": byte_count,
                        "metadata": {"peak_memory": 40_000},
                    }
                    for count, byte_count in zip(
                        candidate_allocations, candidate_bytes
                    )
                ]
            },
        },
    }


class ResultSchemaTests(unittest.TestCase):
    def test_cpu_seconds_per_operation_are_compared_separately_from_wall_time(self):
        record = workload()
        for side, total in (("baseline", 0.002), ("candidate", 0.003)):
            record[side]["timing"]["cpu_rounds"] = [
                {
                    "user_seconds_per_operation": total * 0.8,
                    "system_seconds_per_operation": total * 0.2,
                    "total_seconds_per_operation": total,
                    "coverage": "wait4 root only; descendants excluded",
                }
                for _ in range(3)
            ]
        compared = compare_workload(record)
        cpu = compared["comparison"]["timing"]["cpu"]
        self.assertEqual(cpu["status"], "compared")
        self.assertAlmostEqual(cpu["metrics"]["total_seconds_per_operation"]["change_percent"], 50)
        self.assertEqual(compared["comparison"]["timing"]["change_percent"], 0)

    def test_kernel_region_cpu_compares_only_against_kernel_region_cpu(self):
        def rounds(coverage):
            return [{"user_seconds_per_operation": 0.001, "system_seconds_per_operation": 0.0,
                     "total_seconds_per_operation": 0.001, "coverage": coverage}
                    for _ in range(3)]
        record = workload()
        record["baseline"]["timing"]["cpu_rounds"] = rounds(KERNEL_REGION_CPU_COVERAGE)
        record["candidate"]["timing"]["cpu_rounds"] = rounds(KERNEL_REGION_CPU_COVERAGE)
        self.assertEqual(compare_workload(record)["comparison"]["timing"]["cpu"]["status"], "compared")
        record["candidate"]["timing"]["cpu_rounds"] = rounds(MAC_REAPED_CPU_COVERAGE)
        self.assertEqual(compare_workload(record)["comparison"]["timing"]["cpu"]["status"], "incomplete")

    def test_macos_rss_gate_keeps_pss_and_private_unavailable(self):
        record = workload()
        for side in ("baseline", "candidate"):
            for round_result in record[side]["memory"]["rounds"]:
                round_result["peak_pss"] = None
                round_result["peak_private"] = None
        compared = compare_workload(record, memory_primary_metric="peak_rss")
        memory = compared["comparison"]["memory"]
        self.assertEqual(memory["primary_metric"], "peak_rss")
        self.assertEqual(memory["status"], "incomplete")
        self.assertIn("peak_rss", memory["metrics"])
        self.assertNotIn("peak_pss", memory["metrics"])
        self.assertEqual(memory["gated_metrics"], ["peak_rss"])
        rendered = render_summary(build_summary(
            baseline={"label": "control"}, candidate={"label": "candidate"},
            workloads=[compared],
        ))
        self.assertIn("Peak RSS Δ (growth +)", rendered)
        summary = build_summary(
            baseline={"label": "control"}, candidate={"label": "candidate"},
            workloads=[compared],
        )
        self.assertIsNone(summary["aggregate"]["median_changes_percent"]["peak_pss_change_percent"])
        self.assertIsNone(summary["aggregate"]["median_ratios_candidate_over_baseline"]["peak_pss_candidate_over_baseline"])
        self.assertIsNotNone(summary["aggregate"]["median_changes_percent"]["peak_memory_change_percent"])

    def test_linux_summary_retains_peak_pss_keys_and_meaning(self):
        compared = compare_workload(workload())
        summary = build_summary(
            baseline={"label": "control"}, candidate={"label": "candidate"},
            workloads=[compared],
        )
        changes = summary["aggregate"]["median_changes_percent"]
        ratios = summary["aggregate"]["median_ratios_candidate_over_baseline"]
        self.assertEqual(changes["peak_pss_change_percent"], changes["peak_memory_change_percent"])
        self.assertEqual(ratios["peak_pss_candidate_over_baseline"], ratios["peak_memory_candidate_over_baseline"])
        category_changes = summary["categories"]["web"]["median_changes_percent"]
        self.assertEqual(category_changes["peak_pss_change_percent"], changes["peak_pss_change_percent"])
        self.assertIn("Peak PSS Δ (growth +)", render_summary(summary))

    def test_result_json_round_trips(self):
        original = workload()
        encoded = result_to_json(original)
        self.assertTrue(encoded.endswith("\n"))
        self.assertEqual(result_from_json(encoded), original)

    def test_rejects_missing_identity_and_empty_timing_samples(self):
        malformed = workload()
        del malformed["identity"]["operation"]
        with self.assertRaisesRegex(ResultSchemaError, "identity.operation"):
            result_to_json(malformed)

        malformed = workload()
        malformed["candidate"]["timing"]["samples"] = []
        with self.assertRaisesRegex(ResultSchemaError, "at least one sample"):
            result_to_json(malformed)

    def test_rejects_nonfinite_measurement_and_unknown_schema(self):
        malformed = workload()
        malformed["baseline"]["timing"]["samples"][0] = float("nan")
        with self.assertRaisesRegex(ResultSchemaError, "finite"):
            result_to_json(malformed)

        malformed = workload()
        malformed["schema_version"] = 2
        with self.assertRaisesRegex(ResultSchemaError, "unsupported result schema"):
            result_to_json(malformed)

    def test_rejects_invalid_json(self):
        with self.assertRaises(ResultSchemaError):
            result_from_json("{")

    def test_summary_json_round_trips_and_checks_version(self):
        compared = compare_workload(workload())
        summary = build_summary(
            [compared], baseline="PBS release 20260610", candidate="python-build"
        )
        payload = json.dumps(summary)
        self.assertEqual(summary_from_json(payload), summary)
        summary["schema_version"] = 2
        with self.assertRaisesRegex(ResultSchemaError, "schema_version"):
            summary_from_json(json.dumps(summary))


class StatisticsTests(unittest.TestCase):
    def test_median_mad_stdev_and_cv(self):
        result = describe_samples([1.0, 2.0, 3.0])
        self.assertEqual(result["median"], 2.0)
        self.assertEqual(result["mean"], 2.0)
        self.assertEqual(result["stdev"], 1.0)
        self.assertEqual(result["coefficient_of_variation"], 0.5)
        self.assertEqual(result["mad"], 1.0)
        self.assertEqual(median_absolute_deviation([1.0, 2.0, 3.0]), 1.0)

    def test_single_sample_marks_stdev_and_cv_unavailable(self):
        result = describe_samples([2.0])
        self.assertIsNone(result["stdev"])
        self.assertIsNone(result["coefficient_of_variation"])

    def test_paired_ratios_and_unmatched_samples(self):
        ratios, pairs, unmatched = paired_ratios([1.0, 2.0, 3.0], [2.0, 4.0])
        self.assertEqual(ratios, [2.0, 2.0])
        self.assertEqual(pairs, 2)
        self.assertEqual(unmatched, 1)

    def test_noise_threshold_uses_repeat_spread_and_page_floor(self):
        self.assertEqual(noise_threshold([100.0, 100.0, 100.0]), 0.0)
        self.assertEqual(
            noise_threshold([100.0, 100.0, 100.0], absolute_floor=4096.0),
            4096.0,
        )
        self.assertGreater(noise_threshold([100.0, 110.0, 120.0]), 0.0)

    def test_timing_regression_beyond_repeat_noise_fails(self):
        result = compare_workload(
            workload(candidate_time=(1.05, 1.051, 1.049)),
            memory_gate=False,
            allocation_gate=False,
        )
        self.assertEqual(result["verdict"]["timing"], "fail")
        self.assertGreater(result["comparison"]["timing"]["time_ratio"], 1.0)

    def test_material_pss_increase_fails_even_for_pbs_comparison(self):
        result = compare_workload(
            workload(candidate_pss=(120_000, 120_000, 120_000)),
            baseline_label="PBS release 20260610",
            baseline_kind="pbs",
            allocation_gate=False,
        )
        self.assertEqual(result["baseline_label"], "PBS release 20260610")
        self.assertEqual(result["baseline_kind"], "pbs")
        self.assertEqual(result["verdict"]["memory"], "fail")
        self.assertEqual(result["verdict"]["overall"], "fail")
        self.assertEqual(
            result["comparison"]["memory"]["metrics"]["peak_pss"]["status"],
            "fail",
        )

    def test_material_allocation_increase_has_its_own_failure(self):
        result = compare_workload(
            workload(candidate_allocations=(1300, 1300, 1300)), memory_gate=False
        )
        self.assertEqual(result["verdict"]["allocations"], "fail")
        self.assertEqual(result["verdict"]["memory"], "not_gated")

    def test_candidate_memory_variance_does_not_hide_baseline_regression(self):
        raw = workload(candidate_pss=(101_000, 150_000, 199_000))
        result = compare_workload(raw, allocation_gate=False)
        self.assertEqual(
            result["comparison"]["memory"]["metrics"]["peak_pss"]["status"],
            "fail",
        )

    def test_two_quick_pairs_are_inconclusive_unless_regression_is_clear(self):
        raw = workload(candidate_time=(1.0005, 1.0005))
        raw["baseline"]["timing"]["samples"] = [1.0, 1.0]
        result = compare_workload(raw, memory_gate=False, allocation_gate=False)
        self.assertEqual(result["verdict"]["timing"], "inconclusive")

        raw = workload(candidate_time=(1.2, 1.2))
        raw["baseline"]["timing"]["samples"] = [1.0, 1.0]
        result = compare_workload(raw, memory_gate=False, allocation_gate=False)
        self.assertEqual(result["verdict"]["timing"], "fail")

    def test_self_comparison_pools_samples_to_calibrate_noise(self):
        raw = workload(
            candidate_time=(1.0005, 1.0005, 1.0005),
            candidate_pss=(105_000, 105_000, 105_000),
        )
        result = compare_workload(
            raw,
            baseline_label="same interpreter",
            candidate_label="same interpreter",
            baseline_kind="self",
        )
        self.assertEqual(result["verdict"]["timing"], "pass")
        self.assertEqual(result["verdict"]["memory"], "pass")
        self.assertEqual(
            result["comparison"]["memory"]["metrics"]["peak_pss"]
            ["noise_allowance_source"]["repeatability_source"],
            "pooled self-comparison repeatability",
        )

    def test_runner_memray_normalized_field_names_are_reported(self):
        raw = workload()
        for side in ("baseline", "candidate"):
            raw[side]["allocations"] = {
                "rounds": [
                    {
                        "total_num_allocations": 1000,
                        "total_bytes_allocated": 50_000,
                        "heap_peak_bytes": 40_000,
                        "allocations_per_operation": 10,
                        "bytes_allocated_per_operation": 500,
                    }
                    for _ in range(3)
                ]
            }
        result = compare_workload(raw)
        metrics = result["comparison"]["allocations"]["metrics"]
        self.assertEqual(metrics["heap_peak"]["baseline"]["median"], 40_000)
        self.assertEqual(metrics["bytes_per_operation"]["baseline"]["median"], 500)

    def test_missing_memory_and_allocations_cannot_pass_resource_verdict(self):
        raw = workload()
        raw["baseline"].pop("memory")
        raw["candidate"].pop("memory")
        raw["baseline"].pop("allocations")
        raw["candidate"].pop("allocations")
        result = compare_workload(raw)
        self.assertEqual(result["verdict"]["memory"], "not_measured")
        self.assertEqual(result["verdict"]["allocations"], "not_measured")
        self.assertEqual(result["verdict"]["overall"], "incomplete")


class ReportTests(unittest.TestCase):
    @staticmethod
    def pyperformance_result() -> dict:
        return {
            "suite": "pyperformance",
            "suite_version": "1.14.0",
            "benchmarks": [
                {
                    "name": "json_dumps",
                    "groups": ["stdlib", "parsing/serialization"],
                    "timing": {
                        "status": "compared",
                        "candidate_over_baseline": 0.92,
                        "change_pct": -8.0,
                        "significant": True,
                    },
                    "memory": {
                        "status": "compared",
                        "mem_max_rss": {"change_pct": 2.5},
                        "command_max_rss": {"change_pct": 3.0},
                    },
                },
                {
                    "name": "json_loads",
                    "groups": ["stdlib", "parsing/serialization"],
                    "timing": {
                        "status": "missing_candidate",
                        "candidate_over_baseline": None,
                        "change_pct": None,
                        "significant": None,
                    },
                    "memory": {
                        "status": "compared",
                        "mem_max_rss": {"change_pct": -1.0},
                        "command_max_rss": {"change_pct": -0.5},
                    },
                },
            ],
            "groups": {
                "stdlib": {
                    "selected_members": ["json_dumps", "json_loads"],
                    "selected_count": 2,
                    "recorded_members": ["json_dumps", "json_loads"],
                    "recorded_count": 2,
                    "timing_coverage": {"compared": 1, "selected": 2},
                    "memory_coverage": {"compared": 2, "selected": 2},
                    "median_time_change_pct": -8.0,
                    "time_change_count": 1,
                    "median_mem_max_rss_change_pct": 0.75,
                    "mem_max_rss_change_count": 2,
                    "median_command_max_rss_change_pct": 1.25,
                    "command_max_rss_change_count": 2,
                },
                "parsing/serialization": {
                    "selected_members": ["json_dumps", "json_loads"],
                    "selected_count": 2,
                    "recorded_members": ["json_dumps", "json_loads"],
                    "recorded_count": 2,
                    "timing_coverage": {"compared": 1, "selected": 2},
                    "memory_coverage": {"compared": 2, "selected": 2},
                    "median_time_change_pct": -8.0,
                    "time_change_count": 1,
                    "median_mem_max_rss_change_pct": 0.75,
                    "mem_max_rss_change_count": 2,
                    "median_command_max_rss_change_pct": 1.25,
                    "command_max_rss_change_count": 2,
                },
            },
            "groups_decision_authority": "descriptive_only; group summaries do not determine a verdict",
            "baseline_failures": [
                {"name": "legacy", "stage": "unsupported", "detail": "removed in Python 3.14"}
            ],
            "candidate_failures": [],
        }

    def test_report_has_direction_labels_and_keeps_baseline_identity(self):
        compared = compare_workload(
            workload(candidate_time=(0.95, 0.951, 0.949)),
            baseline_label="PBS release 20260610",
            baseline_kind="pbs",
            candidate_label="local build",
        )
        summary = build_summary(
            [compared],
            baseline={"label": "PBS release 20260610", "kind": "pbs"},
            candidate={"label": "local build"},
        )
        rendered = render_summary(summary)
        self.assertIn("local build vs PBS release 20260610", rendered)
        self.assertIn("Time Δ (faster if −)", rendered)
        self.assertIn("Peak PSS Δ (growth +)", rendered)
        self.assertIn("Bytes/op Δ (growth +)", rendered)
        self.assertNotIn("upstream CPython", rendered)

    def test_cross_version_report_labels_research_comparison(self):
        summary = build_summary(
            [compare_workload(workload())], baseline="CPython 3.14", candidate="CPython 3.16"
        )
        summary["cross_version"] = True
        self.assertIn("Cross-version research comparison", render_summary(summary))

    def test_save_summary_writes_json_and_markdown(self):
        compared = compare_workload(workload())
        summary = build_summary(
            [compared], baseline="upstream CPython", candidate="candidate"
        )
        with tempfile.TemporaryDirectory() as tempdir:
            json_path, markdown_path = save_summary(
                summary, Path(tempdir), {"commit": "abc123"}
            )
            self.assertEqual(json_path.name, "summary.json")
            self.assertEqual(markdown_path.name, "summary.md")
            loaded = json.loads(json_path.read_text())
            self.assertEqual(loaded["provenance"]["commit"], "abc123")
            self.assertIn("## Repository-owned workload results", markdown_path.read_text())

    def test_pyperformance_section_follows_macro_results(self):
        compared = compare_workload(workload())
        summary = build_summary(
            [compared], baseline="upstream CPython", candidate="candidate"
        )
        summary["pyperformance"] = self.pyperformance_result()
        rendered = render_summary(summary)

        macro_index = rendered.index("## Repository-owned workload results")
        pyperf_index = rendered.index("## Pyperformance")
        details_index = rendered.index("## Verdict details")
        self.assertLess(macro_index, pyperf_index)
        self.assertLess(pyperf_index, details_index)
        self.assertIn("1/2 benchmarks compared for timing", rendered)
        self.assertIn("2/2 for memory", rendered)
        self.assertIn("baseline 1, candidate 0", rendered)
        self.assertIn("Macro verdict: **PASS**", rendered)
        self.assertIn("Time Δ (faster if −)", rendered)
        self.assertIn("Pyperf max RSS Δ (growth +)", rendered)
        self.assertIn("-8.00%", rendered)
        self.assertIn("+2.50%", rendered)
        self.assertIn("Group summaries are descriptive", rendered)
        self.assertIn("Median pyperf max RSS Δ (growth +)", rendered)
        self.assertIn("| stdlib | 1/2 | -8.00% | 2/2 | +0.75% | +1.25% |", rendered)
        self.assertIn("stdlib, parsing/serialization", rendered)
        self.assertIn("legacy (unsupported): removed in Python 3.14", rendered)

    def test_pyperformance_only_summary_is_readable_without_macro_rows(self):
        summary = {
            "schema_version": 1,
            "baseline": {"label": "PBS release 20260610", "kind": "pbs"},
            "candidate": {"label": "local build"},
            "workloads": [],
            "pyperformance": self.pyperformance_result(),
        }
        rendered = render_summary(summary)

        self.assertIn("local build vs PBS release 20260610", rendered)
        self.assertIn("Repository-owned workload verdict: **NOT RUN**", rendered)
        self.assertIn("pyperformance results only", rendered)
        self.assertIn("## Pyperformance", rendered)
        self.assertIn("Status: **informational**; coverage **partial**", rendered)
        self.assertIn("1/2 benchmarks compared for timing", rendered)
        self.assertIn("json_dumps", rendered)
        self.assertNotIn("INCOMPLETE", rendered)
        self.assertNotIn("0 repository-owned workload(s)", rendered)
        self.assertNotIn("## Category summaries", rendered)
        self.assertNotIn("## Suite median changes", rendered)

    def test_pyperformance_only_json_keeps_coverage_without_macro_verdict(self):
        summary = {
            "schema_version": 1,
            "baseline": {"label": "PBS release 20260610", "kind": "pbs"},
            "candidate": {"label": "local build"},
            "workloads": [],
            "pyperformance": self.pyperformance_result(),
        }
        # Even a large descriptive group regression is not a macro verdict.
        summary["pyperformance"]["groups"]["stdlib"]["median_time_change_pct"] = 500.0
        with tempfile.TemporaryDirectory() as tempdir:
            json_path, _ = save_summary(summary, Path(tempdir))
            saved = json.loads(json_path.read_text())

        self.assertEqual(saved["verdict"]["overall"], "not_run")
        self.assertEqual(saved["verdict"]["macro"]["status"], "not_run")
        self.assertEqual(
            saved["verdict"]["macro"]["workload_count"], 0
        )
        self.assertEqual(
            saved["verdict"]["pyperformance"]["status"], "informational"
        )
        self.assertEqual(
            saved["verdict"]["pyperformance"]["coverage_status"], "partial"
        )
        self.assertEqual(
            saved["verdict"]["pyperformance"]["timing_compared"], 1
        )
        self.assertEqual(
            saved["verdict"]["pyperformance"]["memory_compared"], 2
        )
        self.assertEqual(
            saved["pyperformance"]["suite"], "pyperformance"
        )
        self.assertEqual(
            saved["pyperformance"]["groups"]["stdlib"]["timing_coverage"],
            {"compared": 1, "selected": 2},
        )


if __name__ == "__main__":
    unittest.main()
