"""Run the pinned pyperformance suite against an externally prepared Python.

The benchmark run scripts and their manifest come from pyperformance 1.14.0.
This adapter runs those scripts directly with the tested interpreter and a
prepared external ``site-packages`` prefix. It deliberately does not call the
pyperformance CLI, which creates virtual environments and installs packages.
Timing and RSS collection are separate passes because pyperf's memory mode
replaces time samples with memory samples.
"""

from __future__ import annotations

import ast
import gzip
import importlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Any, Literal

PYPERFORMANCE_VERSION = "1.14.0"
PYPERF_VERSION = "2.11.0"
Mode = Literal["timing", "memory"]

# The pinned 2to3 script has a pip fallback for lib2to3, which the tested
# interpreter intentionally does not ship. Add pyperformance's vendored
# source directly to PYTHONPATH so both the runner and its child command see it
# and the fallback is never reached.
EXTERNAL_SOURCE_PATHS = {"2to3": ("vendor", "src")}
UPSTREAM_EXCLUSIONS = {
    "hg_startup": (
        "commented out in pyperformance 1.14's MANIFEST because its venv cannot "
        "locate the hg executable"
    ),
}


# These are report and selection groups for this harness. ``apps`` follows
# pyperformance 1.14's apps benchmark tag; the other names make useful slices
# across its stdlib, math, asyncio, and serialization workloads.
GROUPS: dict[str, tuple[str, ...]] = {
    "apps": (
        "2to3",
        "chameleon",
        "docutils",
        "fastapi",
        "html5lib",
        "sphinx",
        "tornado_http",
    ),
    "startup/import": ("python_startup", "python_startup_no_site"),
    "stdlib": (
        "argparse",
        "argparse_subparsers",
        "base64",
        "comprehensions",
        "deepcopy",
        "gc_collect",
        "gc_traversal",
        "generators",
        "json_dumps",
        "json_loads",
        "logging",
        "pathlib",
        "pickle",
        "pickle_dict",
        "pickle_list",
        "pickle_pure_python",
        "pprint",
        "python_startup",
        "python_startup_no_site",
        "regex_compile",
        "regex_dna",
        "regex_effbot",
        "regex_v8",
        "sqlite_synth",
        "tomli_loads",
        "typing_runtime_protocols",
        "unpack_sequence",
        "unpickle",
        "unpickle_list",
        "unpickle_pure_python",
        "xml_etree",
    ),
    "numeric": (
        "deltablue",
        "fannkuch",
        "float",
        "nbody",
        "pidigits",
        "raytrace",
        "scimark",
        "spectral_norm",
        "telco",
    ),
    "async/network": (
        "async_generators",
        "async_tree",
        "async_tree_cpu_io_mixed",
        "async_tree_io",
        "async_tree_memoization",
        "async_tree_eager",
        "async_tree_eager_cpu_io_mixed",
        "async_tree_eager_io",
        "async_tree_eager_memoization",
        "async_tree_tg",
        "async_tree_cpu_io_mixed_tg",
        "async_tree_io_tg",
        "async_tree_memoization_tg",
        "async_tree_eager_tg",
        "async_tree_eager_cpu_io_mixed_tg",
        "async_tree_eager_io_tg",
        "async_tree_eager_memoization_tg",
        "asyncio_tcp",
        "asyncio_tcp_ssl",
        "asyncio_websockets",
        "concurrent_imap",
        "coroutines",
        "dask",
        "fastapi",
        "tornado_http",
    ),
    "parsing/serialization": (
        "2to3",
        "base64",
        "bpe_tokeniser",
        "docutils",
        "html5lib",
        "json_dumps",
        "json_loads",
        "pickle",
        "pickle_dict",
        "pickle_list",
        "pickle_pure_python",
        "pyflate",
        "regex_compile",
        "regex_dna",
        "regex_effbot",
        "regex_v8",
        "sqlglot_v2",
        "sqlglot_v2_parse",
        "sqlglot_v2_transpile",
        "sqlglot_v2_optimize",
        "tomli_loads",
        "unpickle",
        "unpickle_list",
        "unpickle_pure_python",
        "xml_etree",
    ),
}

_HEADER = ("name", "metafile")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_RSS_FIELDS = ("mem_max_rss", "command_max_rss")


class PyPerformanceError(RuntimeError):
    """A pyperformance input or measurement could not be used."""


@dataclass(frozen=True)
class BenchmarkSpec:
    """One named entry in pyperformance's pinned benchmark manifest."""

    name: str
    script: Path
    extra_opts: tuple[str, ...] = ()


@dataclass(frozen=True)
class PyperfBenchmark:
    """Raw pyperf runs and the metadata needed to interpret their values."""

    name: str
    manifest_name: str | None
    unit: str | None
    runs: tuple[tuple[float | int, ...], ...]
    warmups: tuple[tuple[tuple[float | int, float | int], ...], ...]
    run_metadata: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]
    mem_max_rss: int | None
    command_max_rss: int | None

    @property
    def samples(self) -> tuple[float | int, ...]:
        """Flatten sample values while retaining the original runs separately."""

        return tuple(sample for run in self.runs for sample in run)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe result without discarding the pyperf run values."""

        return {
            "name": self.name,
            "manifest_name": self.manifest_name,
            "unit": self.unit,
            "runs": [list(run) for run in self.runs],
            "warmups": [
                [list(warmup) for warmup in run_warmups]
                for run_warmups in self.warmups
            ],
            "samples": list(self.samples),
            "run_metadata": [dict(item) for item in self.run_metadata],
            "metadata": dict(self.metadata),
            "mem_max_rss": self.mem_max_rss,
            "command_max_rss": self.command_max_rss,
        }


@dataclass(frozen=True)
class PyperfResult:
    """Parsed contents of one pyperf JSON file."""

    benchmarks: Mapping[str, PyperfBenchmark]
    metadata: Mapping[str, Any]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class BenchmarkFailure:
    """A pyperformance benchmark that could not produce comparable JSON."""

    name: str
    stage: str
    detail: str
    log_file: Path | None
    result_file: Path | None
    returncode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "detail": self.detail,
            "log_file": None if self.log_file is None else str(self.log_file),
            "result_file": None if self.result_file is None else str(self.result_file),
            "returncode": self.returncode,
        }


@dataclass(frozen=True)
class PyperformanceRun:
    """Output from one selected pyperformance timing or memory pass."""

    python: str
    mode: Mode
    selection: tuple[str, ...]
    directory: Path
    benchmarks: tuple[PyperfBenchmark, ...]
    files: tuple[Path, ...]
    failures: tuple[BenchmarkFailure, ...] = ()
    fixed_loops: Mapping[str, int] | None = None
    calibration: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe manifest of raw result files and parsed data."""

        return {
            "suite": "pyperformance",
            "suite_version": PYPERFORMANCE_VERSION,
            "pyperf_version": PYPERF_VERSION,
            "mode": self.mode,
            "selection": list(self.selection),
            "python": self.python,
            "directory": str(self.directory),
            "successful_count": len(self.benchmarks),
            "failed_count": len(self.failures),
            "fixed_loops": None if self.fixed_loops is None else dict(self.fixed_loops),
            "calibration": self.calibration,
            "upstream_exclusions": dict(UPSTREAM_EXCLUSIONS),
            "benchmarks": [
                dict(benchmark.to_dict(), result_file=str(path))
                for benchmark, path in zip(self.benchmarks, self.files, strict=True)
            ],
            "failures": [failure.to_dict() for failure in self.failures],
        }


def _parse_manifest(benchmark_root: Path) -> list[tuple[str, str]]:
    manifest = benchmark_root / "MANIFEST"
    if not manifest.is_file():
        raise PyPerformanceError(f"pyperformance manifest not found: {manifest}")

    rows: list[tuple[str, str]] = []
    in_benchmarks = False
    saw_header = False
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_benchmarks = line == "[benchmarks]"
            continue
        if not in_benchmarks:
            continue
        if not saw_header:
            columns = tuple(line.split())
            if columns != _HEADER:
                raise PyPerformanceError(
                    f"{manifest}:{line_number}: expected tabular header "
                    f"{_HEADER!r}, got {columns!r}"
                )
            saw_header = True
            continue
        fields = line.split()
        if len(fields) != 2:
            raise PyPerformanceError(
                f"{manifest}:{line_number}: expected benchmark name and metadata path"
            )
        name, metafile = fields
        if not _SAFE_NAME.fullmatch(name):
            raise PyPerformanceError(
                f"{manifest}:{line_number}: invalid benchmark name {name!r}"
            )
        if any(existing == name for existing, _ in rows):
            raise PyPerformanceError(f"{manifest}:{line_number}: duplicate name {name!r}")
        rows.append((name, metafile))

    if not saw_header or not rows:
        raise PyPerformanceError(f"pyperformance manifest has no benchmark entries: {manifest}")
    return rows


def _metadata_for(name: str, metafile: str, benchmark_root: Path) -> tuple[Path, list[Path]]:
    """Resolve the pinned manifest's local and inherited metadata files."""

    if metafile == "<local>":
        directory = benchmark_root / f"bm_{name}"
        return directory, [directory / "pyproject.toml"]
    if metafile.startswith("<local:") and metafile.endswith(">"):
        base_name = metafile[len("<local:") : -1]
        if not _SAFE_NAME.fullmatch(base_name):
            raise PyPerformanceError(f"invalid metadata base for {name!r}: {metafile!r}")
        directory = benchmark_root / f"bm_{base_name}"
        return directory, [directory / "pyproject.toml", directory / f"bm_{name}.toml"]
    raise PyPerformanceError(
        f"unsupported pyperformance 1.14 manifest metadata reference for "
        f"{name!r}: {metafile!r}"
    )


def _tool_metadata(path: Path) -> dict[str, Any]:
    """Read only runscript and extra_opts from a pyperformance metadata file."""

    if not path.is_file():
        raise PyPerformanceError(f"benchmark metadata file not found: {path}")
    values: dict[str, Any] = {}
    in_tool_section = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_tool_section = stripped == "[tool.pyperformance]"
            continue
        if not in_tool_section or "=" not in stripped:
            continue
        key, raw_value = (part.strip() for part in stripped.split("=", 1))
        if key not in ("name", "extra_opts", "runscript"):
            continue
        try:
            value = ast.literal_eval(raw_value)
        except (SyntaxError, ValueError) as error:
            raise PyPerformanceError(
                f"{path}:{line_number}: invalid {key} value: {error}"
            ) from error
        if key == "extra_opts":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise PyPerformanceError(f"{path}:{line_number}: extra_opts must be a string array")
            values[key] = value
        elif key == "name":
            if not isinstance(value, str) or not value:
                raise PyPerformanceError(f"{path}:{line_number}: name must be a non-empty string")
            values[key] = value
        else:
            if not isinstance(value, str):
                raise PyPerformanceError(f"{path}:{line_number}: runscript must be a string")
            values[key] = value
    return values


def load_benchmarks(benchmark_root: Path) -> tuple[BenchmarkSpec, ...]:
    """Load benchmark names, scripts, and variant options from the wheel tree."""

    benchmark_root = benchmark_root.resolve()
    specs: list[BenchmarkSpec] = []
    for name, metafile in _parse_manifest(benchmark_root):
        directory, metadata_files = _metadata_for(name, metafile, benchmark_root)
        metadata: dict[str, Any] = {}
        for path in metadata_files:
            metadata.update(_tool_metadata(path))
        runscript = metadata.get("runscript", "run_benchmark.py")
        script = (directory / runscript).resolve()
        if not script.is_relative_to(benchmark_root):
            raise PyPerformanceError(f"benchmark {name!r} script escapes benchmark tree: {script}")
        if not script.is_file():
            raise PyPerformanceError(f"benchmark {name!r} script not found: {script}")
        specs.append(
            BenchmarkSpec(
                name=name,
                script=script,
                extra_opts=tuple(metadata.get("extra_opts", ())),
            )
        )
    return tuple(specs)


def select_benchmarks(
    benchmark_root: Path, selection: str | Sequence[str] = "all"
) -> tuple[BenchmarkSpec, ...]:
    """Select all, a named grouping, or one or more individual benchmarks."""

    specs = load_benchmarks(benchmark_root)
    by_name = {spec.name: spec for spec in specs}
    requested = (selection,) if isinstance(selection, str) else tuple(selection)
    if not requested or any(not isinstance(item, str) or not item for item in requested):
        raise ValueError("selection must contain at least one non-empty group or benchmark")

    chosen: set[str] = set()
    for item in requested:
        if item in ("all", "full", "pyperformance"):
            chosen.update(by_name)
        elif item in GROUPS:
            missing = set(GROUPS[item]) - by_name.keys()
            if missing:
                raise PyPerformanceError(
                    f"group {item!r} refers to benchmarks absent from the pinned manifest: "
                    + ", ".join(sorted(missing))
                )
            chosen.update(GROUPS[item])
        elif item in by_name:
            chosen.add(item)
        else:
            valid = ", ".join((*GROUPS, "all", "<benchmark-name>"))
            raise ValueError(f"unknown pyperformance selection {item!r}; expected {valid}")

    if not chosen:
        raise PyPerformanceError("selection resolved to no pyperformance benchmarks")
    return tuple(spec for spec in specs if spec.name in chosen)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise PyPerformanceError(f"{where} must be a JSON object with string keys")
    return value


def _rss(metadata: Mapping[str, Any], field: str) -> int | None:
    value = metadata.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PyPerformanceError(f"pyperf {field} must be a non-negative integer number of bytes")
    return value


def _read_json(path: Path) -> Any:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                return json.load(stream)
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PyPerformanceError(f"cannot read pyperf JSON {path}: {error}") from error


def parse_raw_json(path: str | os.PathLike[str]) -> PyperfResult:
    """Parse raw pyperf JSON, preserving values and extracting RSS metadata.

    ``mem_max_rss`` and ``command_max_rss`` are bytes. They may appear in the
    suite, benchmark, or individual run metadata, so the parser checks each
    location and reports the highest recorded value while keeping each run's
    merged metadata intact.
    """

    result_path = Path(path)
    document = _mapping(_read_json(result_path), "pyperf root")
    version = document.get("version")
    if version not in (5, 6, "1.0"):
        raise PyPerformanceError(f"unsupported pyperf JSON version {version!r}: {result_path}")
    raw_benchmarks = document.get("benchmarks")
    if not isinstance(raw_benchmarks, list) or not raw_benchmarks:
        raise PyPerformanceError(f"pyperf JSON has no benchmarks: {result_path}")
    suite_metadata = dict(_mapping(document.get("metadata", {}), "pyperf metadata"))
    suite_metadata.update(
        _mapping(document.get("common_metadata", {}), "pyperf common_metadata")
    )

    parsed: dict[str, PyperfBenchmark] = {}
    for index, raw_benchmark in enumerate(raw_benchmarks):
        benchmark_data = _mapping(raw_benchmark, f"benchmarks[{index}]")
        benchmark_metadata = dict(suite_metadata)
        benchmark_metadata.update(
            _mapping(benchmark_data.get("metadata", {}), f"benchmarks[{index}].metadata")
        )
        name = benchmark_metadata.get("name")
        if not isinstance(name, str) or not name:
            raise PyPerformanceError(f"benchmarks[{index}] has no non-empty metadata.name")
        if name in parsed:
            raise PyPerformanceError(f"duplicate benchmark name in pyperf JSON: {name!r}")

        raw_runs = benchmark_data.get("runs")
        if not isinstance(raw_runs, list) or not raw_runs:
            raise PyPerformanceError(f"benchmark {name!r} has no pyperf runs")
        runs: list[tuple[float | int, ...]] = []
        warmups: list[tuple[tuple[float | int, float | int], ...]] = []
        run_metadata: list[Mapping[str, Any]] = []
        units: set[str] = set()
        rss_values: dict[str, list[int]] = {field: [] for field in _RSS_FIELDS}
        for run_index, raw_run in enumerate(raw_runs):
            run_data = _mapping(raw_run, f"benchmark {name!r} runs[{run_index}]")
            if "values" not in run_data:
                # pyperf stores loop/warmup calibration runs without a values
                # field. They are valid raw runs, but do not enter the sample
                # set used for timing or memory comparisons.
                values: list[float | int] = []
            else:
                values = run_data["values"]
                if not isinstance(values, list) or not values:
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].values must be a non-empty array"
                    )
                if any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in values
                ):
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].values must contain numbers"
                    )
                if any(isinstance(value, float) and not math.isfinite(value) for value in values):
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].values must be finite"
                    )
                if any(value <= 0 for value in values):
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].values must be greater than zero"
                    )
            runs.append(tuple(values))

            raw_warmups = run_data.get("warmups", [])
            if not isinstance(raw_warmups, list):
                raise PyPerformanceError(
                    f"benchmark {name!r} runs[{run_index}].warmups must be an array"
                )
            parsed_warmups: list[tuple[float | int, float | int]] = []
            for warmup_index, raw_warmup in enumerate(raw_warmups):
                if not isinstance(raw_warmup, list) or len(raw_warmup) != 2:
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].warmups[{warmup_index}] "
                        "must be a [loops, value] pair"
                    )
                loops, value = raw_warmup
                if isinstance(loops, bool) or not isinstance(loops, int) or loops <= 0:
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].warmups[{warmup_index}] "
                        "loops must be a positive integer"
                    )
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or (isinstance(value, float) and not math.isfinite(value))
                    or value <= 0
                ):
                    raise PyPerformanceError(
                        f"benchmark {name!r} runs[{run_index}].warmups[{warmup_index}] "
                        "value must be a finite positive number"
                    )
                parsed_warmups.append((loops, value))
            warmups.append(tuple(parsed_warmups))
            metadata = dict(benchmark_metadata)
            metadata.update(
                _mapping(
                    run_data.get("metadata", {}),
                    f"benchmark {name!r} runs[{run_index}].metadata",
                )
            )
            run_metadata.append(metadata)
            unit = metadata.get("unit")
            if unit is not None:
                if not isinstance(unit, str):
                    raise PyPerformanceError(f"benchmark {name!r} has a non-string unit")
                units.add(unit)
            for field in _RSS_FIELDS:
                memory_value = _rss(metadata, field)
                if memory_value is not None:
                    rss_values[field].append(memory_value)

        if len(units) > 1:
            raise PyPerformanceError(
                f"benchmark {name!r} contains inconsistent units: {sorted(units)}"
            )
        unit = next(iter(units), None)
        memory_metrics = {
            field: max(values) if values else None for field, values in rss_values.items()
        }
        parsed[name] = PyperfBenchmark(
            name=name,
            manifest_name=None,
            unit=unit,
            runs=tuple(runs),
            warmups=tuple(warmups),
            run_metadata=tuple(run_metadata),
            metadata=benchmark_metadata,
            mem_max_rss=memory_metrics["mem_max_rss"],
            command_max_rss=memory_metrics["command_max_rss"],
        )

    return PyperfResult(parsed, suite_metadata, document)


def _clean_environment(site_packages: Path) -> dict[str, str]:
    """Expose only the prepared dependencies and prevent pip configuration."""

    excluded = {
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "VIRTUAL_ENV",
        "PIP_CONFIG_FILE",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_TRUSTED_HOST",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in excluded and not key.startswith("PIP_")
    }
    env.update(
        {
            "PYTHONPATH": str(site_packages),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_CONFIG_FILE": os.devnull,
        }
    )
    return env


def _selection_name(selection: str | Sequence[str]) -> tuple[tuple[str, ...], str]:
    names = (selection,) if isinstance(selection, str) else tuple(selection)
    if not names:
        raise ValueError("selection must contain at least one group or benchmark")
    slug = "+".join(names)
    if not _SAFE_NAME.fullmatch(slug.replace("/", "_")):
        raise ValueError(f"selection cannot be used as a result directory: {names!r}")
    return names, slug.replace("/", "_")


def run_pyperformance(
    python: str | os.PathLike[str],
    site_packages: str | os.PathLike[str],
    benchmark_root: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    selection: str | Sequence[str] = "all",
    mode: Mode = "timing",
    *,
    warmups: int = 2,
    affinity: str | None = None,
    fixed_loops: Mapping[str, int] | None = None,
    calibration: bool = False,
) -> PyperformanceRun:
    """Run one complete pyperformance pass with a tested interpreter.

    ``site_packages`` and ``benchmark_root`` must come from benchmark input
    preparation. The prepared dependencies are external to ``python``; this
    function does not invoke pip, venv, pyperformance's installation CLI, or
    any package index. Its memory pass invokes pyperf's Linux RSS sampler and
    stores memory samples in a separate directory from timing JSON.
    """

    if mode not in ("timing", "memory"):
        raise ValueError(f"unsupported pyperformance mode {mode!r}")
    if calibration and (mode != "timing" or fixed_loops is not None):
        raise ValueError("loop calibration requires an automatically calibrated timing pass")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    if fixed_loops is not None and any(
        not isinstance(name, str) or isinstance(count, bool)
        or not isinstance(count, int) or count <= 0
        for name, count in fixed_loops.items()
    ):
        raise ValueError("fixed loops must map benchmark names to positive integers")

    python_arg = os.fspath(python)
    site_path = Path(site_packages).resolve()
    root_path = Path(benchmark_root).resolve()
    parent_output = Path(output_dir).resolve()
    if "/" in python_arg and not Path(python_arg).is_file():
        raise PyPerformanceError(f"tested Python executable not found: {python_arg}")
    if not site_path.is_dir():
        raise PyPerformanceError(f"prepared site-packages directory not found: {site_path}")

    requested, selection_slug = _selection_name(selection)
    specs = select_benchmarks(root_path, requested)
    run_directory = parent_output / "pyperformance" / mode / selection_slug
    run_directory.mkdir(parents=True, exist_ok=True)
    environment = _clean_environment(site_path)

    paths = {
        spec.name: (run_directory / f"{spec.name}.json", run_directory / f"{spec.name}.log")
        for spec in specs
    }
    existing = [
        str(path)
        for result_path, log_path in paths.values()
        for path in (result_path, log_path)
        if path.exists()
    ]
    if existing:
        raise PyPerformanceError(
            "refusing to overwrite existing pyperformance result files: "
            + ", ".join(existing)
        )

    parsed_benchmarks: list[PyperfBenchmark] = []
    result_files: list[Path] = []
    failures: list[BenchmarkFailure] = []
    for spec in specs:
        output, log = paths[spec.name]
        if fixed_loops is not None and spec.name not in fixed_loops:
            failures.append(BenchmarkFailure(
                spec.name, "unsupported", "baseline calibration yielded no fixed loop count", None, None
            ))
            continue
        benchmark_environment = environment
        extra_path = EXTERNAL_SOURCE_PATHS.get(spec.name)
        if extra_path is not None:
            external_source = spec.script.parent.joinpath(*extra_path).resolve()
            if not (external_source / "lib2to3").is_dir():
                failures.append(
                    BenchmarkFailure(
                        spec.name,
                        "unsupported",
                        f"pinned compatibility source is missing: {external_source / 'lib2to3'}",
                        None,
                        None,
                    )
                )
                continue
            benchmark_environment = dict(environment)
            benchmark_environment["PYTHONPATH"] = os.pathsep.join(
                (str(external_source), environment["PYTHONPATH"])
            )
        command = [
            python_arg,
            str(spec.script),
            *spec.extra_opts,
        ]
        if calibration:
            command.extend(("--processes", "1", "--values", "1"))
        else:
            command.append("--rigorous")
        command.extend(("--warmups", str(warmups)))
        if fixed_loops is not None:
            command.extend(("--loops", str(fixed_loops[spec.name])))
        if affinity:
            command.extend(("--affinity", affinity))
        if mode == "memory":
            command.append("--track-memory")
        command.extend(("--output", str(output)))

        try:
            with log.open("x", encoding="utf-8") as log_stream:
                completed = subprocess.run(
                    command,
                    cwd=spec.script.parent,
                    env=benchmark_environment,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
        except OSError as error:
            failures.append(
                BenchmarkFailure(
                    spec.name,
                    "launch",
                    str(error),
                    log if log.is_file() else None,
                    None,
                )
            )
            continue
        if completed.returncode != 0:
            tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
            failures.append(
                BenchmarkFailure(
                    spec.name,
                    "execute",
                    tail or f"process exited with status {completed.returncode}",
                    log,
                    output if output.is_file() else None,
                    completed.returncode,
                )
            )
            continue
        if not output.is_file():
            failures.append(
                BenchmarkFailure(
                    spec.name,
                    "output",
                    "benchmark exited successfully without writing a pyperf result",
                    log,
                    None,
                    completed.returncode,
                )
            )
            continue
        try:
            parsed = parse_raw_json(output)
        except PyPerformanceError as error:
            failures.append(
                BenchmarkFailure(spec.name, "parse", str(error), log, output, completed.returncode)
            )
            continue
        for benchmark in parsed.benchmarks.values():
            if fixed_loops is not None:
                measured_loops = [
                    metadata.get("loops")
                    for values, metadata in zip(benchmark.runs, benchmark.run_metadata, strict=True)
                    if values
                ]
                if not measured_loops or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    or value != fixed_loops[spec.name]
                    for value in measured_loops
                ):
                    failures.append(BenchmarkFailure(
                        spec.name, "loops",
                        f"expected {fixed_loops[spec.name]} loops for {benchmark.name}, "
                        f"recorded {measured_loops!r}", log, output,
                        completed.returncode,
                    ))
                    break
        else:
            for benchmark in parsed.benchmarks.values():
                parsed_benchmarks.append(replace(benchmark, manifest_name=spec.name))
                result_files.append(output)

    return PyperformanceRun(
        python=python_arg,
        mode=mode,
        selection=requested,
        directory=run_directory,
        benchmarks=tuple(parsed_benchmarks),
        files=tuple(result_files),
        failures=tuple(failures),
        fixed_loops=fixed_loops,
        calibration=calibration,
    )


def baseline_loop_counts(
    calibration: PyperformanceRun,
) -> tuple[dict[str, int], dict[str, str]]:
    """Choose one baseline-calibrated loop count for each manifest script.

    A script can emit several named results but accepts only one ``--loops``
    argument. The largest measured baseline count covers every emitted result;
    all four measured passes then use that same count for the script.
    """

    if calibration.mode != "timing" or calibration.fixed_loops is not None or not calibration.calibration:
        raise ValueError("loop calibration requires an automatically calibrated timing pass")
    counts: dict[str, list[int]] = {}
    unsupported = {failure.name: failure.detail for failure in calibration.failures}
    for benchmark in calibration.benchmarks:
        manifest_name = benchmark.manifest_name
        if manifest_name is None:
            raise PyPerformanceError(f"missing manifest name for {benchmark.name}")
        values = [
            metadata.get("loops")
            for samples, metadata in zip(benchmark.runs, benchmark.run_metadata, strict=True)
            if samples
        ]
        if not values or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            unsupported[manifest_name] = f"missing positive measured loop metadata for {benchmark.name}"
            continue
        if len(set(values)) != 1:
            unsupported[manifest_name] = f"inconsistent measured loop metadata for {benchmark.name}: {sorted(set(values))}"
            continue
        counts.setdefault(manifest_name, []).extend(values)
    for name in unsupported:
        counts.pop(name, None)
    chosen: dict[str, int] = {}
    for name, values in counts.items():
        smallest, largest = min(values), max(values)
        if largest > 4 * smallest:
            unsupported[name] = (
                f"named results need divergent baseline loops ({smallest} to {largest}); "
                "one script accepts only one --loops value"
            )
        else:
            chosen[name] = largest
    return chosen, unsupported


def _pinned_pyperf() -> Any:
    """Import the vendored pyperf API used by the produced JSON files."""

    module = sys.modules.get("pyperf")
    if module is None:
        wheel = Path(__file__).resolve().parents[1] / "vendor" / "pyperf-2.11.0-py3-none-any.whl"
        if not wheel.is_file():
            raise PyPerformanceError(f"vendored pyperf wheel is missing: {wheel}")
        sys.path.insert(0, str(wheel))
        try:
            module = importlib.import_module("pyperf")
        except ImportError as error:
            raise PyPerformanceError(f"could not import vendored pyperf {PYPERF_VERSION}: {error}") from error
        finally:
            sys.path.remove(str(wheel))
    version = getattr(module, "__version__", None)
    if version != PYPERF_VERSION:
        raise PyPerformanceError(
            f"pyperformance results require pyperf {PYPERF_VERSION}, found {version!r}"
        )
    return module


def _pyperf_benchmarks(run: PyperformanceRun) -> dict[str, Any]:
    """Load raw files through pyperf's own JSON and sample implementation."""

    pyperf = _pinned_pyperf()
    benchmarks: dict[str, Any] = {}
    for path in dict.fromkeys(run.files):
        try:
            suite = pyperf.BenchmarkSuite.load(str(path))
        except Exception as error:
            raise PyPerformanceError(f"pyperf could not load {path}: {error}") from error
        for benchmark in suite:
            name = benchmark.get_name()
            if name in benchmarks:
                raise PyPerformanceError(
                    f"pyperf result name {name!r} appears in multiple output files"
                )
            benchmarks[name] = benchmark
    return benchmarks


def _ratio_fields(baseline: int | None, candidate: int | None) -> dict[str, Any]:
    if baseline is None or candidate is None:
        return {"baseline_bytes": baseline, "candidate_bytes": candidate, "ratio": None, "change_pct": None}
    if baseline == 0:
        return {"baseline_bytes": baseline, "candidate_bytes": candidate, "ratio": None, "change_pct": None}
    ratio = candidate / baseline
    return {
        "baseline_bytes": baseline,
        "candidate_bytes": candidate,
        "ratio": ratio,
        "change_pct": (ratio - 1.0) * 100.0,
    }


def compare_pyperformance_runs(
    baseline_timing: PyperformanceRun,
    candidate_timing: PyperformanceRun,
    baseline_memory: PyperformanceRun,
    candidate_memory: PyperformanceRun,
) -> dict[str, Any]:
    """Compare matching pyperf runs with pyperf's own mean and t-test logic.

    The return document retains each side's original run/value arrays, metadata,
    RSS values, and any harness failure records. Time and memory comparisons
    stay separate. A benchmark missing from either side remains an explicit
    record and is never treated as a zero-valued result.
    """

    for run in (baseline_timing, candidate_timing):
        if run.mode != "timing":
            raise ValueError(f"expected a timing pass, got {run.mode!r}")
    for run in (baseline_memory, candidate_memory):
        if run.mode != "memory":
            raise ValueError(f"expected a memory pass, got {run.mode!r}")

    baseline_times = _pyperf_benchmarks(baseline_timing)
    candidate_times = _pyperf_benchmarks(candidate_timing)
    baseline_memory_results = _pyperf_benchmarks(baseline_memory)
    candidate_memory_results = _pyperf_benchmarks(candidate_memory)
    baseline_time_records = {item.name: item for item in baseline_timing.benchmarks}
    candidate_time_records = {item.name: item for item in candidate_timing.benchmarks}
    baseline_memory_records = {item.name: item for item in baseline_memory.benchmarks}
    candidate_memory_records = {item.name: item for item in candidate_memory.benchmarks}

    try:
        compare_module = importlib.import_module("pyperf._compare")
    except ImportError as error:
        raise PyPerformanceError(f"could not import pyperf comparison code: {error}") from error

    class CompareData:
        def __init__(self, name: str, benchmark: Any):
            self.name = name
            self.benchmark = benchmark

    def compare_samples(
        name: str,
        ref: Any | None,
        changed: Any | None,
        ref_record: PyperfBenchmark | None,
        changed_record: PyperfBenchmark | None,
        expected_unit: str,
    ) -> dict[str, Any]:
        if ref is None or changed is None:
            return {
                "status": "missing_baseline" if ref is None else "missing_candidate",
                "candidate_over_baseline": None,
                "significant": None,
                "t_score": None,
            }
        ref_unit = None if ref_record is None else ref_record.unit
        changed_unit = None if changed_record is None else changed_record.unit
        if ref_unit != changed_unit or ref_unit != expected_unit:
            return {
                "status": "unit_mismatch",
                "baseline_unit": ref_unit,
                "candidate_unit": changed_unit,
                "expected_unit": expected_unit,
                "candidate_over_baseline": None,
                "significant": None,
                "t_score": None,
            }
        comparison = compare_module.CompareResult(
            CompareData(name, ref), CompareData(name, changed)
        )
        return {
            "status": "compared",
            "candidate_over_baseline": comparison.norm_mean,
            "change_pct": (comparison.norm_mean - 1.0) * 100.0,
            "significant": comparison.significant,
            "t_score": comparison.t_score,
        }

    names = sorted(
        set(baseline_times)
        | set(candidate_times)
        | set(baseline_memory_results)
        | set(candidate_memory_results)
    )
    records: list[dict[str, Any]] = []
    for name in names:
        base_time = baseline_time_records.get(name)
        changed_time = candidate_time_records.get(name)
        base_memory = baseline_memory_records.get(name)
        changed_memory = candidate_memory_records.get(name)
        memory_comparison = compare_samples(
            name,
            baseline_memory_results.get(name),
            candidate_memory_results.get(name),
            base_memory,
            changed_memory,
            "byte",
        )
        manifest_names = sorted(
            {
                record.manifest_name
                for record in (base_time, changed_time, base_memory, changed_memory)
                if record is not None and record.manifest_name is not None
            }
        )
        if not manifest_names and any(name in members for members in GROUPS.values()):
            # Hand-built or older run records may not carry manifest metadata.
            # This fallback applies only when the pyperf result label itself is
            # an unambiguous name from the pinned manifest groups.
            manifest_names = [name]
        memberships = [
            group
            for group, members in GROUPS.items()
            if any(manifest_name in members for manifest_name in manifest_names)
        ]
        records.append(
            {
                "name": name,
                "manifest_name": manifest_names[0] if len(manifest_names) == 1 else None,
                "manifest_names": manifest_names,
                "groups": memberships,
                "timing": compare_samples(
                    name,
                    baseline_times.get(name),
                    candidate_times.get(name),
                    base_time,
                    changed_time,
                    "second",
                ),
                "memory": {
                    **memory_comparison,
                    "unit": "byte",
                    "mem_max_rss": _ratio_fields(
                        None if base_memory is None else base_memory.mem_max_rss,
                        None if changed_memory is None else changed_memory.mem_max_rss,
                    ),
                    "command_max_rss": _ratio_fields(
                        None if base_memory is None else base_memory.command_max_rss,
                        None if changed_memory is None else changed_memory.command_max_rss,
                    ),
                },
                "baseline_timing": None if base_time is None else base_time.to_dict(),
                "candidate_timing": None if changed_time is None else changed_time.to_dict(),
                "baseline_memory": None if base_memory is None else base_memory.to_dict(),
                "candidate_memory": None if changed_memory is None else changed_memory.to_dict(),
            }
        )

    # A run may select an entire group (or all benchmarks), so expand selection
    # labels against the same source of truth used by the adapter. This keeps
    # group coverage denominators accurate even when selected benchmarks fail
    # before producing a pyperf result file.
    selected_manifest_names: set[str] = set()
    grouped_names = {name for members in GROUPS.values() for name in members}
    for run in (baseline_timing, candidate_timing, baseline_memory, candidate_memory):
        for selected in run.selection:
            if selected in ("all", "full", "pyperformance"):
                selected_manifest_names.update(grouped_names)
            elif selected in GROUPS:
                selected_manifest_names.update(GROUPS[selected])
            else:
                selected_manifest_names.add(selected)

    group_summaries: dict[str, Any] = {}
    for group, members in GROUPS.items():
        selected_members = sorted(set(members) & selected_manifest_names)
        if not selected_members:
            continue
        group_records = [record for record in records if group in record["groups"]]
        timing_changes = [
            float(record["timing"]["change_pct"])
            for record in group_records
            if record["timing"].get("status") == "compared"
            and isinstance(record["timing"].get("change_pct"), (int, float))
            and not isinstance(record["timing"].get("change_pct"), bool)
        ]
        mem_rss_changes = [
            float(record["memory"]["mem_max_rss"]["change_pct"])
            for record in group_records
            if isinstance(record["memory"].get("mem_max_rss"), Mapping)
            and isinstance(record["memory"]["mem_max_rss"].get("change_pct"), (int, float))
            and not isinstance(record["memory"]["mem_max_rss"].get("change_pct"), bool)
        ]
        command_rss_changes = [
            float(record["memory"]["command_max_rss"]["change_pct"])
            for record in group_records
            if isinstance(record["memory"].get("command_max_rss"), Mapping)
            and isinstance(record["memory"]["command_max_rss"].get("change_pct"), (int, float))
            and not isinstance(record["memory"]["command_max_rss"].get("change_pct"), bool)
        ]
        timing_compared = sum(
            record["timing"].get("status") == "compared" for record in group_records
        )
        memory_compared = sum(
            record["memory"].get("status") == "compared" for record in group_records
        )
        group_summaries[group] = {
            "selected_members": selected_members,
            "selected_count": len(selected_members),
            "recorded_members": sorted({record["name"] for record in group_records}),
            "recorded_count": len(group_records),
            "timing_coverage": {
                "compared": timing_compared,
                "selected": len(selected_members),
            },
            "memory_coverage": {
                "compared": memory_compared,
                "selected": len(selected_members),
            },
            "median_time_change_pct": median(timing_changes) if timing_changes else None,
            "time_change_count": len(timing_changes),
            "median_mem_max_rss_change_pct": median(mem_rss_changes) if mem_rss_changes else None,
            "mem_max_rss_change_count": len(mem_rss_changes),
            "median_command_max_rss_change_pct": median(command_rss_changes) if command_rss_changes else None,
            "command_max_rss_change_count": len(command_rss_changes),
        }
    return {
        "suite": "pyperformance",
        "suite_version": PYPERFORMANCE_VERSION,
        "pyperf_version": PYPERF_VERSION,
        "upstream_exclusions": dict(UPSTREAM_EXCLUSIONS),
        "baseline_failures": [failure.to_dict() for run in (baseline_timing, baseline_memory) for failure in run.failures],
        "candidate_failures": [failure.to_dict() for run in (candidate_timing, candidate_memory) for failure in run.failures],
        "benchmarks": records,
        "groups": group_summaries,
        "groups_decision_authority": "descriptive_only; group summaries do not determine a verdict",
    }


__all__ = [
    "EXTERNAL_SOURCE_PATHS",
    "GROUPS",
    "PYPERFORMANCE_VERSION",
    "PYPERF_VERSION",
    "UPSTREAM_EXCLUSIONS",
    "BenchmarkFailure",
    "BenchmarkSpec",
    "PyPerformanceError",
    "PyperfBenchmark",
    "PyperfResult",
    "PyperformanceRun",
    "baseline_loop_counts",
    "compare_pyperformance_runs",
    "load_benchmarks",
    "parse_raw_json",
    "run_pyperformance",
    "select_benchmarks",
]
