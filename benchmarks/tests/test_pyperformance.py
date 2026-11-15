"""Unit tests for direct, offline pyperformance execution and result parsing."""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "benchmarks" / "harness" / "pyperformance.py"
SPEC = importlib.util.spec_from_file_location("bench_pyperformance", MODULE_PATH)
assert SPEC and SPEC.loader
pyperformance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pyperformance
SPEC.loader.exec_module(pyperformance)


def write_manifest(benchmark_root: Path, names: list[str]) -> None:
    rows = ["[benchmarks]", "name\tmetafile"]
    for name in names:
        rows.append(f"{name}\t<local>")
        directory = benchmark_root / f"bm_{name}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run_benchmark.py").write_text("# benchmark fixture\n")
        (directory / "pyproject.toml").write_text(
            "[tool.pyperformance]\n"
            f'name = "{name}"\n'
            "extra_opts = [\"fixture-option\"]\n"
        )
    (benchmark_root / "MANIFEST").write_text("\n".join(rows) + "\n")


def raw_suite(
    name: str,
    values: list[int | float],
    *,
    unit: str = "second",
    metadata: dict | None = None,
) -> dict:
    return {
        "version": "1.0",
        "metadata": {},
        "benchmarks": [
            {
                "metadata": {"name": name, "unit": unit},
                "runs": [{"values": values, "metadata": metadata or {}}],
            }
        ],
    }


def write_run(
    directory: Path,
    filename: str,
    name: str,
    mode: str,
    values: list[int | float],
    rss: int,
    command_rss: int | None = None,
) -> pyperformance.PyperformanceRun:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    unit = "second" if mode == "timing" else "byte"
    document = {
        "version": "1.0",
        "metadata": {},
        "benchmarks": [
            {
                "metadata": {"name": name, "unit": unit},
                "runs": [
                    {
                        "values": values,
                        "metadata": {
                            "loops": 1,
                            "warmups": 0,
                            "mem_max_rss": rss,
                            **({} if command_rss is None else {"command_max_rss": command_rss}),
                        },
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(document))
    parsed = pyperformance.parse_raw_json(path).benchmarks[name]
    return pyperformance.PyperformanceRun(
        python=sys.executable,
        mode=mode,
        selection=(name,),
        directory=directory,
        benchmarks=(parsed,),
        files=(path,),
    )


class ManifestTests(unittest.TestCase):
    def test_all_and_report_groups_select_manifest_names_in_order(self):
        names = sorted({name for group in pyperformance.GROUPS.values() for name in group})
        names.extend(("extra_benchmark",))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_manifest(root, names)

            all_names = [item.name for item in pyperformance.select_benchmarks(root, "all")]
            apps = [item.name for item in pyperformance.select_benchmarks(root, "apps")]
            multi = [
                item.name
                for item in pyperformance.select_benchmarks(
                    root, ("startup/import", "python_startup")
                )
            ]

        self.assertEqual(all_names, names)
        self.assertEqual(apps, [name for name in names if name in pyperformance.GROUPS["apps"]])
        self.assertEqual(multi, ["python_startup", "python_startup_no_site"])
        self.assertNotIn("extra_benchmark", apps)

    def test_unknown_selection_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_manifest(root, ["python_startup"])
            with self.assertRaisesRegex(ValueError, "unknown pyperformance selection"):
                pyperformance.select_benchmarks(root, "does_not_exist")

    def test_variant_metadata_supplies_extra_runner_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "bm_base"
            base.mkdir()
            (base / "run_benchmark.py").write_text("# fixture\n")
            (base / "pyproject.toml").write_text(
                '[tool.pyperformance]\nname = "base_output"\nextra_opts = ["base"]\n'
            )
            (base / "bm_variant.toml").write_text(
                '[tool.pyperformance]\nname = "variant_output"\nextra_opts = ["variant", "--flag"]\n'
            )
            (root / "MANIFEST").write_text(
                "[benchmarks]\nname\tmetafile\nbase\t<local>\nvariant\t<local:base>\n"
            )

            base_spec, variant_spec = pyperformance.load_benchmarks(root)

        self.assertEqual(base_spec.extra_opts, ("base",))
        self.assertEqual(variant_spec.extra_opts, ("variant", "--flag"))
        self.assertEqual(variant_spec.script, base_spec.script)


class PyperfJsonTests(unittest.TestCase):
    def test_pyperf_calibration_run_without_values_and_warmups_is_preserved(self):
        document = {
            "version": "1.0",
            "metadata": {"name": "json_loads", "unit": "second"},
            "benchmarks": [
                {
                    "runs": [
                        {
                            "metadata": {"calibrate_loops": 8},
                            "warmups": [[1, 0.002], [8, 0.001]],
                        },
                        {
                            "metadata": {"loops": 8},
                            "values": [0.0011, 0.0012],
                            "warmups": [[8, 0.0013]],
                        },
                    ]
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            path.write_text(json.dumps(document))
            benchmark = pyperformance.parse_raw_json(path).benchmarks["json_loads"]

        self.assertEqual(benchmark.runs, ((), (0.0011, 0.0012)))
        self.assertEqual(
            benchmark.warmups,
            (((1, 0.002), (8, 0.001)), ((8, 0.0013),)),
        )
        self.assertEqual(benchmark.samples, (0.0011, 0.0012))
        self.assertEqual(benchmark.to_dict()["warmups"], [[[1, 0.002], [8, 0.001]], [[8, 0.0013]]])

    def test_raw_json_preserves_runs_and_extracts_rss_from_all_metadata_levels(self):
        document = {
            "version": "1.0",
            "metadata": {"command_max_rss": 32_000},
            "common_metadata": {"mem_max_rss": 12_000},
            "benchmarks": [
                {
                    "metadata": {"name": "json_dumps", "unit": "second"},
                    "runs": [
                        {"values": [0.1, 0.2], "metadata": {"loops": 4, "mem_max_rss": 8_000}},
                        {
                            "values": [0.11],
                            "metadata": {"warmups": 1, "mem_max_rss": 16_000, "command_max_rss": 30_000},
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timing.json"
            path.write_text(json.dumps(document))
            result = pyperformance.parse_raw_json(path)

        benchmark = result.benchmarks["json_dumps"]
        self.assertEqual(benchmark.unit, "second")
        self.assertEqual(benchmark.runs, ((0.1, 0.2), (0.11,)))
        self.assertEqual(benchmark.samples, (0.1, 0.2, 0.11))
        self.assertEqual(benchmark.run_metadata[0]["loops"], 4)
        self.assertEqual(benchmark.run_metadata[1]["warmups"], 1)
        self.assertEqual(benchmark.mem_max_rss, 16_000)
        self.assertEqual(benchmark.command_max_rss, 32_000)

    def test_gzip_memory_json_uses_bytes_and_keeps_run_values(self):
        document = raw_suite(
            "python_startup",
            [2_097_152],
            unit="byte",
            metadata={"mem_max_rss": 4_194_304, "command_max_rss": 3_145_728},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                json.dump(document, stream)
            result = pyperformance.parse_raw_json(path)

        benchmark = result.benchmarks["python_startup"]
        self.assertEqual(benchmark.unit, "byte")
        self.assertEqual(benchmark.samples, (2_097_152,))
        self.assertEqual(benchmark.mem_max_rss, 4_194_304)
        self.assertEqual(benchmark.command_max_rss, 3_145_728)

    def test_malformed_rss_metadata_is_rejected(self):
        document = raw_suite("one", [1.0], metadata={"mem_max_rss": "4 MB"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(pyperformance.PyPerformanceError, "mem_max_rss"):
                pyperformance.parse_raw_json(path)


class DirectRunTests(unittest.TestCase):
    def prepared_tree(self, root: Path) -> tuple[Path, Path, Path]:
        site_packages = root / "site-packages"
        site_packages.mkdir()
        benchmark_root = root / "site-packages" / "pyperformance" / "data-files" / "benchmarks"
        benchmark_root.mkdir(parents=True)
        write_manifest(benchmark_root, ["fixture_benchmark"])
        return site_packages, benchmark_root, root / "results"

    def run_fixture(self, mode: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        site_packages, benchmark_root, output_dir = self.prepared_tree(root)
        payload = raw_suite(
            "fixture_benchmark",
            [0.25] if mode == "timing" else [4_096],
            unit="second" if mode == "timing" else "byte",
            metadata={"mem_max_rss": 8_192},
        )
        commands: list[list[str]] = []
        environments: list[dict[str, str]] = []

        def fake_run(command, *, cwd, env, stdout, stderr, text, check):
            commands.append(command)
            environments.append(env)
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps(payload))
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(pyperformance.subprocess, "run", side_effect=fake_run):
            result = pyperformance.run_pyperformance(
                sys.executable,
                site_packages,
                benchmark_root,
                output_dir,
                selection="fixture_benchmark",
                mode=mode,
            )
        return result, commands[0], environments[0], site_packages, output_dir

    @unittest.skipIf("venv" in sys.executable.lower(), "benchmark harness must run on the built interpreter, not a venv")
    def test_timing_runs_script_directly_with_external_path_and_rigorous_pyperf(self):
        result, command, environment, site_packages, output_dir = self.run_fixture("timing")

        self.assertEqual(result.mode, "timing")
        self.assertEqual(result.benchmarks[0].samples, (0.25,))
        self.assertIn("--rigorous", command)
        self.assertIn("--warmups", command)
        self.assertNotIn("--track-memory", command)
        self.assertTrue(command[1].endswith("/bm_fixture_benchmark/run_benchmark.py"))
        self.assertNotIn("pip", " ".join(command).lower())
        self.assertNotIn("venv", " ".join(command).lower())
        self.assertEqual(result.to_dict()["benchmarks"][0]["manifest_name"], "fixture_benchmark")
        self.assertEqual(environment["PYTHONPATH"], str(site_packages.resolve()))
        self.assertIn("fixture-option", command)
        self.assertEqual(result.files[0].parents[3], output_dir.resolve())

    def test_memory_is_a_separate_track_memory_pass(self):
        result, command, _environment, _site_packages, _output_dir = self.run_fixture("memory")

        self.assertEqual(result.mode, "memory")
        self.assertEqual(result.benchmarks[0].unit, "byte")
        self.assertIn("--track-memory", command)
        self.assertEqual(result.benchmarks[0].mem_max_rss, 8_192)
        self.assertIn("/memory/", str(result.files[0]))

    def test_to_dict_is_json_safe_and_includes_raw_result_location(self):
        result, _command, _environment, _site_packages, _output_dir = self.run_fixture("timing")

        encoded = json.dumps(result.to_dict(), allow_nan=False)
        self.assertIn('"suite_version": "1.14.0"', encoded)
        self.assertIn('"result_file"', encoded)

    def test_full_selection_keeps_a_failed_benchmark_and_runs_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = root / "site-packages"
            site_packages.mkdir()
            benchmark_root = root / "benchmark-tree"
            benchmark_root.mkdir()
            write_manifest(benchmark_root, ["first", "second"])
            output_dir = root / "results"
            invocations: list[str] = []

            def fail_first(command, *, stdout, **_kwargs):
                invocations.append(Path(command[1]).parent.name)
                if invocations[-1] == "bm_first":
                    stdout.write("known failure")
                    return subprocess.CompletedProcess(command, 1)
                output = Path(command[command.index("--output") + 1])
                output.write_text(json.dumps(raw_suite("second", [0.1])))
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(pyperformance.subprocess, "run", side_effect=fail_first):
                result = pyperformance.run_pyperformance(
                    sys.executable,
                    site_packages,
                    benchmark_root,
                    output_dir,
                    selection="all",
                )

        self.assertEqual(invocations, ["bm_first", "bm_second"])
        self.assertEqual([item.manifest_name for item in result.benchmarks], ["second"])
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].name, "first")
        self.assertEqual(result.failures[0].stage, "execute")

    def test_2to3_uses_vendored_lib2to3_without_target_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = root / "site-packages"
            site_packages.mkdir()
            benchmark_root = root / "benchmark-tree"
            benchmark_root.mkdir()
            write_manifest(benchmark_root, ["2to3"])
            external_source = benchmark_root / "bm_2to3" / "vendor" / "src"
            package = external_source / "lib2to3"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("# pinned fixture\n")

            def run_script(command, *, env, stdout, **_kwargs):
                self.assertNotIn("pip", " ".join(command).lower())
                self.assertEqual(
                    env["PYTHONPATH"].split(os.pathsep)[0], str(external_source.resolve())
                )
                output = Path(command[command.index("--output") + 1])
                output.write_text(json.dumps(raw_suite("2to3", [0.1])))
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(pyperformance.subprocess, "run", side_effect=run_script):
                result = pyperformance.run_pyperformance(
                    sys.executable,
                    site_packages,
                    benchmark_root,
                    root / "results",
                    selection="2to3",
                )

        self.assertEqual(result.failures, ())
        self.assertEqual(result.benchmarks[0].manifest_name, "2to3")

    def test_2to3_without_pinned_compatibility_source_is_reported_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = root / "site-packages"
            site_packages.mkdir()
            benchmark_root = root / "benchmark-tree"
            benchmark_root.mkdir()
            write_manifest(benchmark_root, ["2to3"])
            with mock.patch.object(pyperformance.subprocess, "run") as execute:
                result = pyperformance.run_pyperformance(
                    sys.executable,
                    site_packages,
                    benchmark_root,
                    root / "results",
                    selection="2to3",
                )

        execute.assert_not_called()
        self.assertEqual(result.benchmarks, ())
        self.assertEqual(result.failures[0].stage, "unsupported")


class ComparisonTests(unittest.TestCase):
    def test_uses_pyperf_significance_and_preserves_timing_and_rss_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_time = write_run(
                root / "baseline", "time.json", "json_dumps", "timing",
                [1.00, 1.01, 0.99, 1.02, 0.98, 1.00], 100_000,
            )
            candidate_time = write_run(
                root / "candidate", "time.json", "json_dumps", "timing",
                [0.80, 0.81, 0.79, 0.82, 0.78, 0.80], 100_000,
            )
            baseline_memory = write_run(
                root / "baseline", "memory.json", "json_dumps", "memory",
                [1_000_000, 1_010_000, 990_000, 1_000_000, 1_005_000, 995_000],
                1_200_000,
                command_rss=1_400_000,
            )
            candidate_memory = write_run(
                root / "candidate", "memory.json", "json_dumps", "memory",
                [1_200_000, 1_210_000, 1_190_000, 1_200_000, 1_205_000, 1_195_000],
                1_320_000,
                command_rss=1_540_000,
            )

            report = pyperformance.compare_pyperformance_runs(
                baseline_time,
                candidate_time,
                baseline_memory,
                candidate_memory,
            )

        record = report["benchmarks"][0]
        self.assertAlmostEqual(record["timing"]["candidate_over_baseline"], 0.8)
        self.assertTrue(record["timing"]["significant"])
        self.assertEqual(record["baseline_timing"]["runs"], [[1.0, 1.01, 0.99, 1.02, 0.98, 1.0]])
        self.assertAlmostEqual(record["memory"]["candidate_over_baseline"], 1.2)
        self.assertEqual(record["memory"]["mem_max_rss"]["candidate_bytes"], 1_320_000)
        self.assertEqual(record["memory"]["command_max_rss"]["ratio"], 1.1)
        self.assertEqual(report["baseline_failures"], [])
        json.dumps(report, allow_nan=False)

    def test_groups_use_manifest_names_and_report_selected_coverage_and_medians(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def manifest_run(run):
                return replace(
                    run,
                    selection=("apps",),
                    benchmarks=tuple(
                        replace(benchmark, manifest_name="fastapi")
                        for benchmark in run.benchmarks
                    ),
                )

            baseline_time = manifest_run(
                write_run(
                    root / "baseline", "time.json", "fastapi_http", "timing",
                    [1.00, 1.01, 0.99, 1.02, 0.98, 1.00], 100_000,
                )
            )
            candidate_time = manifest_run(
                write_run(
                    root / "candidate", "time.json", "fastapi_http", "timing",
                    [0.80, 0.81, 0.79, 0.82, 0.78, 0.80], 100_000,
                )
            )
            baseline_memory = manifest_run(
                write_run(
                    root / "baseline", "memory.json", "fastapi_http", "memory",
                    [1_000_000, 1_010_000, 990_000, 1_000_000, 1_005_000, 995_000],
                    1_200_000,
                    command_rss=1_400_000,
                )
            )
            candidate_memory = manifest_run(
                write_run(
                    root / "candidate", "memory.json", "fastapi_http", "memory",
                    [1_200_000, 1_210_000, 1_190_000, 1_200_000, 1_205_000, 1_195_000],
                    1_320_000,
                    command_rss=1_540_000,
                )
            )

            report = pyperformance.compare_pyperformance_runs(
                baseline_time,
                candidate_time,
                baseline_memory,
                candidate_memory,
            )

        record = report["benchmarks"][0]
        self.assertEqual(record["name"], "fastapi_http")
        self.assertEqual(record["manifest_name"], "fastapi")
        self.assertEqual(record["groups"], ["apps", "async/network"])
        self.assertEqual(report["groups"]["apps"]["timing_coverage"], {"compared": 1, "selected": 7})
        self.assertEqual(report["groups"]["async/network"]["memory_coverage"], {"compared": 1, "selected": 2})
        self.assertAlmostEqual(report["groups"]["apps"]["median_time_change_pct"], -20.0)
        self.assertAlmostEqual(report["groups"]["async/network"]["median_mem_max_rss_change_pct"], 10.0)
        self.assertIn("descriptive_only", report["groups_decision_authority"])
        json.dumps(report, allow_nan=False)

    def test_missing_side_is_reported_without_filling_a_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_time = write_run(root / "base", "time.json", "one", "timing", [1, 1.1], 100)
            changed_time = write_run(root / "changed", "time.json", "other", "timing", [1, 1.1], 100)
            base_memory = write_run(root / "base", "memory.json", "one", "memory", [100, 110], 120)
            changed_memory = write_run(root / "changed", "memory.json", "other", "memory", [100, 110], 120)
            changed_time = replace(
                changed_time,
                failures=(
                    pyperformance.BenchmarkFailure(
                        "one", "unsupported", "not runnable", None, None
                    ),
                ),
            )

            report = pyperformance.compare_pyperformance_runs(
                base_time, changed_time, base_memory, changed_memory
            )

        one = next(item for item in report["benchmarks"] if item["name"] == "one")
        self.assertEqual(one["timing"]["status"], "missing_candidate")
        self.assertIsNone(one["timing"]["candidate_over_baseline"])
        self.assertEqual(report["candidate_failures"][0]["name"], "one")


if __name__ == "__main__":
    unittest.main()
