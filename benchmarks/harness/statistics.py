"""Statistics and noise-aware verdicts for paired benchmark results.

Ratios are always candidate over baseline: timing below 1 is faster, while
resident memory or allocation ratios above 1 mean the candidate used more.
Noise limits are derived from repeated baseline measurements, with one Linux
page as the absolute floor for resident-memory metrics.
"""

from __future__ import annotations

import math
import random
import statistics as std_statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .process import KERNEL_REGION_CPU_COVERAGE
from .models import (
    MEMORY_METRICS,
    RESULT_SCHEMA_VERSION,
    validate_workload_result,
)

PAGE_SIZE_FLOOR_BYTES = 4096
TIMING_RESOLUTION_FLOOR_SECONDS = 0.001
NOISE_SIGMA_MULTIPLIER = 3.0
BOOTSTRAP_ITERATIONS = 2000
MIN_VERDICT_SAMPLES = 3
GATED_MEMORY_METRICS = ("peak_pss", "steady_pss", "postload_pss", "memory_growth")
GATED_ALLOCATION_METRICS = ("allocations_per_operation", "bytes_per_operation")
ALLOCATION_ALIASES = {
    "total_num_allocations": ("total_num_allocations", "total_allocations"),
    "total_bytes_allocated": ("total_bytes_allocated",),
    "heap_peak": ("heap_peak", "heap_peak_bytes", "peak_memory"),
    "allocations_per_operation": ("allocations_per_operation",),
    "bytes_per_operation": (
        "bytes_per_operation",
        "bytes_allocated_per_operation",
    ),
}


def median_absolute_deviation(values: Sequence[float]) -> float:
    """Return the unscaled median absolute deviation."""
    if not values:
        raise ValueError("at least one value is required")
    center = std_statistics.median(values)
    return float(std_statistics.median([abs(value - center) for value in values]))


def describe_samples(values: Sequence[float]) -> dict[str, float | int | None]:
    """Summarize a non-empty sequence without hiding one-sample limits."""
    if not values:
        raise ValueError("at least one value is required")
    samples = [float(value) for value in values]
    mean = float(std_statistics.mean(samples))
    deviation = float(std_statistics.stdev(samples)) if len(samples) > 1 else None
    return {
        "count": len(samples),
        "median": float(std_statistics.median(samples)),
        "mean": mean,
        "minimum": min(samples),
        "maximum": max(samples),
        "stdev": deviation,
        "coefficient_of_variation": deviation / mean if deviation is not None and mean else None,
        "mad": median_absolute_deviation(samples),
    }


def noise_threshold(
    values: Sequence[float],
    *,
    absolute_floor: float = 0.0,
    sigma_multiplier: float = NOISE_SIGMA_MULTIPLIER,
) -> float:
    """Estimate an absolute allowance from repeat noise and an explicit floor.

    The robust sigma estimate is 1.4826 times MAD.  A standard-error term
    catches short samples where MAD can be zero despite visible spread.
    """
    if not values:
        raise ValueError("at least one value is required")
    if absolute_floor < 0 or not math.isfinite(absolute_floor):
        raise ValueError("absolute_floor must be finite and non-negative")
    if sigma_multiplier < 0 or not math.isfinite(sigma_multiplier):
        raise ValueError("sigma_multiplier must be finite and non-negative")
    samples = [float(value) for value in values]
    mad_sigma = 1.4826 * median_absolute_deviation(samples)
    standard_error = (
        float(std_statistics.stdev(samples)) / math.sqrt(len(samples))
        if len(samples) > 1
        else 0.0
    )
    return max(absolute_floor, sigma_multiplier * max(mad_sigma, standard_error))


def paired_ratios(
    baseline: Sequence[float], candidate: Sequence[float]
) -> tuple[list[float], int, int]:
    """Return candidate/baseline ratios paired by run order.

    If one side has extra samples, use the common prefix and return the number
    of unmatched samples. The report records that count instead of silently
    pretending the rounds were paired.
    """
    count = min(len(baseline), len(candidate))
    if count == 0:
        return [], 0, max(len(baseline), len(candidate))
    ratios = [float(candidate[i]) / float(baseline[i]) for i in range(count)]
    if any(not math.isfinite(value) or value <= 0 for value in ratios):
        raise ValueError("paired ratios must be finite and positive")
    return ratios, count, abs(len(baseline) - len(candidate))


def bootstrap_median_interval(
    values: Sequence[float], *, confidence: float = 0.95, seed: int = 0
) -> list[float] | None:
    """Return a deterministic percentile bootstrap interval for the median."""
    if len(values) < 2:
        return None
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    samples = [float(value) for value in values]
    generator = random.Random(seed)
    count = len(samples)
    medians = [
        float(std_statistics.median(generator.choices(samples, k=count)))
        for _ in range(BOOTSTRAP_ITERATIONS)
    ]
    tail = (1.0 - confidence) / 2.0
    return [_percentile(medians, tail), _percentile(medians, 1.0 - tail)]


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _extract_memory_samples(memory: Any, metric: str) -> list[float]:
    if not isinstance(memory, Mapping):
        return []
    rounds = memory.get("rounds", ())
    samples = [
        float(record[metric])
        for record in rounds
        if isinstance(record, Mapping)
        and metric in record
        and record[metric] is not None
    ]
    if samples:
        return samples
    scalar = memory.get(metric)
    if isinstance(scalar, (int, float)) and not isinstance(scalar, bool):
        return [float(scalar)]
    return []


def _allocation_samples(
    allocations: Any, metric: str, operation_count: int
) -> list[float]:
    if not isinstance(allocations, Mapping):
        return []
    aliases = ALLOCATION_ALIASES[metric]
    rounds = allocations.get("rounds", ())
    samples: list[float] = []
    if isinstance(rounds, Sequence) and not isinstance(rounds, (str, bytes)):
        for record in rounds:
            if not isinstance(record, Mapping):
                continue
            round_operations = record.get("operations", operation_count)
            if (
                isinstance(round_operations, bool)
                or not isinstance(round_operations, int)
                or round_operations <= 0
            ):
                round_operations = operation_count
            found = next((record[name] for name in aliases if name in record), None)
            if found is None and metric == "heap_peak":
                metadata = record.get("metadata")
                if isinstance(metadata, Mapping):
                    found = metadata.get("peak_memory")
            if found is None and metric == "allocations_per_operation":
                total = next(
                    (
                        record[name]
                        for name in ALLOCATION_ALIASES["total_num_allocations"]
                        if name in record
                    ),
                    None,
                )
                if total is not None:
                    found = float(total) / round_operations
            if found is None and metric == "bytes_per_operation":
                total = record.get("total_bytes_allocated")
                if total is not None:
                    found = float(total) / round_operations
            if found is not None:
                samples.append(float(found))
    if samples:
        return samples

    total_operations = allocations.get("operations", operation_count)
    if (
        isinstance(total_operations, bool)
        or not isinstance(total_operations, int)
        or total_operations <= 0
    ):
        total_operations = operation_count
    found = next((allocations[name] for name in aliases if name in allocations), None)
    if found is None and metric == "heap_peak":
        metadata = allocations.get("metadata")
        if isinstance(metadata, Mapping):
            found = metadata.get("peak_memory")
    if found is None and metric == "allocations_per_operation":
        total = next(
            (allocations[name] for name in ALLOCATION_ALIASES["total_num_allocations"] if name in allocations),
            None,
        )
        if total is not None:
            found = float(total) / total_operations
    if found is None and metric == "bytes_per_operation":
        total = allocations.get("total_bytes_allocated")
        if total is not None:
            found = float(total) / total_operations
    if isinstance(found, (int, float)) and not isinstance(found, bool):
        return [float(found)]
    return []


def _series_summary(samples: Sequence[float]) -> dict[str, Any] | None:
    if not samples:
        return None
    summary = describe_samples(samples)
    summary["samples"] = list(samples)
    return summary


def _summarize_side(side: Mapping[str, Any], operation_count: int, *, memory_only: bool = False) -> dict[str, Any]:
    result = dict(side)
    timing = dict(side["timing"])
    if not memory_only:
        samples = [float(value) for value in timing["samples"]]
        summary = describe_samples(samples)
        median = float(summary["median"])
        timing["summary"] = {
            **summary,
            "samples_seconds": samples,
            "throughput_operations_per_second": operation_count / median,
            "sample_percentiles_seconds": {
                "p50": _percentile(samples, 0.50),
                "p95": _percentile(samples, 0.95),
                "p99": _percentile(samples, 0.99),
            },
        }
    result["timing"] = timing

    memory = dict(side.get("memory") or {})
    memory_metrics: dict[str, Any] = {}
    for metric in MEMORY_METRICS:
        metric_summary = _series_summary(_extract_memory_samples(memory, metric))
        if metric_summary is not None:
            memory_metrics[metric] = metric_summary
    memory["metrics"] = memory_metrics
    result["memory"] = memory

    allocations = dict(side.get("allocations") or {})
    allocation_summaries: dict[str, Any] = {}
    for metric in ALLOCATION_ALIASES:
        metric_summary = _series_summary(
            _allocation_samples(allocations, metric, operation_count)
        )
        if metric_summary is not None:
            allocation_summaries[metric] = metric_summary
    allocations["metrics"] = allocation_summaries
    result["allocations"] = allocations
    return result


def _compare_metric(
    baseline_samples: Sequence[float],
    candidate_samples: Sequence[float],
    *,
    noise_floor: float = 0.0,
    noise_reference_samples: Sequence[float] | None = None,
) -> dict[str, Any]:
    baseline = describe_samples(baseline_samples)
    candidate = describe_samples(candidate_samples)
    baseline_median = float(baseline["median"])
    candidate_median = float(candidate["median"])
    delta = candidate_median - baseline_median
    if noise_reference_samples is not None:
        allowance = noise_threshold(
            noise_reference_samples, absolute_floor=noise_floor
        )
        noise_source = "pooled self-comparison repeatability"
    else:
        allowance = noise_threshold(baseline_samples, absolute_floor=noise_floor)
        noise_source = "baseline repeatability"
    ratio = candidate_median / baseline_median if baseline_median > 0 else None
    percent_change = (
        (ratio - 1.0) * 100.0 if ratio is not None else None
    )
    enough_repeats = (
        len(baseline_samples) >= MIN_VERDICT_SAMPLES
        and len(candidate_samples) >= MIN_VERDICT_SAMPLES
    )
    if enough_repeats:
        status = "fail" if delta > allowance else "pass"
    else:
        # Short quick runs can reveal only very clear regressions.  Requiring
        # three measured noise allowances avoids turning a two-round wobble
        # into a hard failure while retaining a guard for large changes.
        status = "fail" if delta > 3.0 * allowance else "inconclusive"
    return {
        "baseline": baseline,
        "candidate": candidate,
        "ratio_candidate_over_baseline": ratio,
        "change_percent": percent_change,
        "absolute_change": delta,
        "noise_allowance": allowance,
        "noise_allowance_source": {
            "baseline_mad": baseline["mad"],
            "candidate_mad": candidate["mad"],
            "sigma_multiplier": NOISE_SIGMA_MULTIPLIER,
            "absolute_floor": noise_floor,
            "repeatability_source": noise_source,
            "minimum_samples_for_verdict": MIN_VERDICT_SAMPLES,
        },
        "status": status,
    }


def _compare_timing(
    baseline_samples: Sequence[float],
    candidate_samples: Sequence[float],
    *,
    self_calibration: bool = False,
) -> dict[str, Any]:
    ratios, paired_count, unpaired_count = paired_ratios(
        baseline_samples, candidate_samples
    )
    baseline_stats = describe_samples(baseline_samples)
    candidate_stats = describe_samples(candidate_samples)
    baseline_median = float(baseline_stats["median"])
    ratio_of_medians = float(candidate_stats["median"]) / baseline_median
    paired_median = float(std_statistics.median(ratios))
    if self_calibration:
        reference_samples = [*baseline_samples, *candidate_samples]
        noise_seconds = noise_threshold(
            reference_samples,
            absolute_floor=TIMING_RESOLUTION_FLOOR_SECONDS,
        )
        noise_source = "pooled self-comparison repeatability"
    else:
        noise_seconds = max(
            noise_threshold(
                baseline_samples,
                absolute_floor=TIMING_RESOLUTION_FLOOR_SECONDS,
            ),
            noise_threshold(
                candidate_samples,
                absolute_floor=TIMING_RESOLUTION_FLOOR_SECONDS,
            ),
        )
        noise_source = "larger of baseline and candidate repeatability"
    relative_noise = noise_seconds / baseline_median
    enough_pairs = paired_count >= MIN_VERDICT_SAMPLES
    if enough_pairs:
        verdict = "fail" if paired_median > 1.0 + relative_noise else "pass"
    else:
        verdict = (
            "fail"
            if paired_median > 1.0 + 3.0 * relative_noise
            else "inconclusive"
        )
    if paired_median < 1.0 - relative_noise:
        direction = "faster"
    elif paired_median > 1.0 + relative_noise:
        direction = "slower"
    else:
        direction = "within_noise"
    return {
        "ratio_candidate_over_baseline": paired_median,
        "time_ratio": paired_median,
        "ratio_of_medians": ratio_of_medians,
        "change_percent": (paired_median - 1.0) * 100.0,
        "paired_ratio_samples": ratios,
        "paired_samples": paired_count,
        "unpaired_samples": unpaired_count,
        "median_ratio_ci95": bootstrap_median_interval(ratios),
        "noise_threshold_ratio": relative_noise,
        "noise_threshold_percent": relative_noise * 100.0,
        "baseline_noise_seconds": noise_seconds,
        "baseline_noise_source": {
            "baseline_mad_seconds": baseline_stats["mad"],
            "sigma_multiplier": NOISE_SIGMA_MULTIPLIER,
            "repeatability_source": noise_source,
            "measurement_resolution_floor_seconds": TIMING_RESOLUTION_FLOOR_SECONDS,
            "minimum_samples_for_verdict": MIN_VERDICT_SAMPLES,
        },
        "direction": direction,
        "status": verdict,
    }


def _compare_cpu(
    baseline_rounds: Any, candidate_rounds: Any,
) -> dict[str, Any]:
    """Describe compute consumed in the uninstrumented timing runs."""
    fields = ("user_seconds_per_operation", "system_seconds_per_operation",
              "total_seconds_per_operation")
    result: dict[str, Any] = {"status": "unsupported", "metrics": {}}
    if not isinstance(baseline_rounds, list) or not isinstance(candidate_rounds, list):
        return result
    for field in fields:
        baseline = [float(item[field]) for item in baseline_rounds
                    if isinstance(item, Mapping) and isinstance(item.get(field), (int, float))]
        candidate = [float(item[field]) for item in candidate_rounds
                     if isinstance(item, Mapping) and isinstance(item.get(field), (int, float))]
        if not baseline and not candidate:
            continue
        base_median = float(std_statistics.median(baseline)) if baseline else None
        candidate_median = float(std_statistics.median(candidate)) if candidate else None
        result["metrics"][field] = {
            "baseline_median": base_median,
            "candidate_median": candidate_median,
            "change_percent": (None if base_median is None or candidate_median is None or base_median == 0
                               else (candidate_median / base_median - 1) * 100),
            "baseline_samples": baseline,
            "candidate_samples": candidate,
        }
    coverages = [item.get("coverage", "") for item in [*baseline_rounds, *candidate_rounds]
                 if isinstance(item, Mapping)]
    if result["metrics"]:
        result["status"] = (
            "compared" if len(baseline_rounds) == len(candidate_rounds)
            and (all(coverage.startswith("wait4 root") for coverage in coverages)
                 or all(coverage == KERNEL_REGION_CPU_COVERAGE for coverage in coverages))
            and all(len(metric["baseline_samples"]) == len(baseline_rounds)
                    and len(metric["candidate_samples"]) == len(candidate_rounds)
                    for metric in result["metrics"].values()) else "incomplete"
        )
    result["coverage"] = {
        "baseline": [item.get("coverage") for item in baseline_rounds if isinstance(item, Mapping)],
        "candidate": [item.get("coverage") for item in candidate_rounds if isinstance(item, Mapping)],
    }
    return result


def _compare_memory(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    memory_gate: bool,
    memory_primary_metric: str = "peak_pss",
) -> dict[str, Any]:
    if memory_primary_metric not in {"peak_pss", "peak_rss"}:
        raise ValueError("memory_primary_metric must be peak_pss or peak_rss")
    gated_metrics = (GATED_MEMORY_METRICS if memory_primary_metric == "peak_pss"
                     else ("peak_rss",))
    baseline_memory = baseline.get("memory") or {}
    candidate_memory = candidate.get("memory") or {}
    self_calibration = baseline.get("label") == candidate.get("label") and bool(
        baseline.get("self_calibration")
    )
    metrics: dict[str, Any] = {}
    statuses: list[str] = []
    measured_gate_metrics = 0
    sampling_incomplete = any(
        isinstance(round_result, Mapping) and (
            round_result.get("sampling_errors") or not round_result.get("samples")
        )
        for side_memory in (baseline_memory, candidate_memory)
        for round_result in side_memory.get("rounds", ())
    )

    for metric in MEMORY_METRICS:
        baseline_samples = _extract_memory_samples(baseline_memory, metric)
        candidate_samples = _extract_memory_samples(candidate_memory, metric)
        if not baseline_samples and not candidate_samples:
            continue
        noise_floor = PAGE_SIZE_FLOOR_BYTES if metric != "process_count" else 0.0
        if not baseline_samples or not candidate_samples:
            metric_result = {
                "status": "incomplete",
                "baseline": _series_summary(baseline_samples),
                "candidate": _series_summary(candidate_samples),
                "reason": "metric is present for only one interpreter",
            }
        else:
            metric_result = _compare_metric(
                baseline_samples,
                candidate_samples,
                noise_floor=noise_floor,
                noise_reference_samples=(
                    [*baseline_samples, *candidate_samples]
                    if self_calibration
                    else None
                ),
            )
        metric_result["gated"] = metric in gated_metrics and memory_gate
        metrics[metric] = metric_result
        if metric in gated_metrics and memory_gate:
            measured_gate_metrics += 1
            statuses.append(metric_result["status"])

    if not memory_gate:
        status = "not_gated"
    elif not metrics:
        status = "not_measured"
    elif memory_primary_metric == "peak_rss" and sampling_incomplete:
        status = "incomplete"
    elif "fail" in statuses:
        status = "fail"
    elif "incomplete" in statuses or "inconclusive" in statuses or measured_gate_metrics == 0 or memory_primary_metric not in metrics:
        status = "incomplete"
    elif memory_primary_metric == "peak_rss":
        # Summed RSS can double-count shared pages. A regression is useful
        # evidence, but an RSS pass cannot prove upstream memory parity.
        status = "incomplete"
    else:
        status = "pass"
    return {
        "gate_enabled": memory_gate,
        "primary_metric": memory_primary_metric,
        "qualification_coverage": (
            "diagnostic RSS only; unique/private or PSS unavailable"
            if memory_primary_metric == "peak_rss" else "PSS resource gate"
        ),
        "sampling_incomplete": bool(sampling_incomplete),
        "status": status,
        "metrics": metrics,
        "gated_metrics": list(gated_metrics),
        "noise_policy": {
            "normal_comparison": "baseline 3-sigma repeatability bound",
            "self_comparison": "pooled baseline and candidate 3-sigma repeatability bound",
            "repeatability_formula": "3 x max(1.4826 * MAD, standard error)",
            "absolute_floor_bytes": PAGE_SIZE_FLOOR_BYTES,
            "minimum_samples_for_verdict": MIN_VERDICT_SAMPLES,
        },
    }


def _compare_allocations(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    operation_count: int,
    *,
    allocation_gate: bool,
) -> dict[str, Any]:
    baseline_allocations = baseline.get("allocations") or {}
    candidate_allocations = candidate.get("allocations") or {}
    self_calibration = baseline.get("label") == candidate.get("label") and bool(
        baseline.get("self_calibration")
    )
    metrics: dict[str, Any] = {}
    statuses: list[str] = []
    measured_gate_metrics = 0
    for metric in ALLOCATION_ALIASES:
        baseline_samples = _allocation_samples(
            baseline_allocations, metric, operation_count
        )
        candidate_samples = _allocation_samples(
            candidate_allocations, metric, operation_count
        )
        if not baseline_samples and not candidate_samples:
            continue
        if not baseline_samples or not candidate_samples:
            metric_result = {
                "status": "incomplete",
                "baseline": _series_summary(baseline_samples),
                "candidate": _series_summary(candidate_samples),
                "reason": "metric is present for only one interpreter",
            }
        else:
            if metric in GATED_ALLOCATION_METRICS:
                granularity = 1.0 / operation_count
            elif metric == "heap_peak":
                granularity = float(PAGE_SIZE_FLOOR_BYTES)
            else:
                granularity = 0.0
            metric_result = _compare_metric(
                baseline_samples,
                candidate_samples,
                noise_floor=granularity,
                noise_reference_samples=(
                    [*baseline_samples, *candidate_samples]
                    if self_calibration
                    else None
                ),
            )
        metric_result["gated"] = metric in GATED_ALLOCATION_METRICS and allocation_gate
        metrics[metric] = metric_result
        if metric in GATED_ALLOCATION_METRICS and allocation_gate:
            measured_gate_metrics += 1
            statuses.append(metric_result["status"])

    if not allocation_gate:
        status = "not_gated"
    elif not metrics:
        status = "not_measured"
    elif "fail" in statuses:
        status = "fail"
    elif "incomplete" in statuses or "inconclusive" in statuses or measured_gate_metrics == 0:
        status = "incomplete"
    else:
        status = "pass"
    return {
        "gate_enabled": allocation_gate,
        "status": status,
        "metrics": metrics,
        "gated_metrics": list(GATED_ALLOCATION_METRICS),
        "noise_policy": {
            "normal_comparison": "baseline 3-sigma repeatability bound",
            "self_comparison": "pooled baseline and candidate 3-sigma repeatability bound",
            "repeatability_formula": "3 x max(1.4826 * MAD, standard error)",
            "absolute_floor": "1 / allocation operations for normalized metrics; page size for heap peak",
            "minimum_samples_for_verdict": MIN_VERDICT_SAMPLES,
        },
    }


def compare_workload(
    workload: Mapping[str, Any],
    *,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
    baseline_kind: str | None = None,
    memory_gate: bool = True,
    memory_primary_metric: str = "peak_pss",
    allocation_gate: bool = True,
) -> dict[str, Any]:
    """Compare one workload with paired timing, memory, and allocation verdicts.

    The two sides' sample lists are paired by round order. Material resident
    memory regressions fail the comparison by default for every named
    baseline, including PBS; `baseline_kind` keeps the source explicit in the
    report and never changes the label to upstream CPython.
    """
    record = validate_workload_result(workload)
    if not isinstance(baseline_label, str) or not baseline_label.strip():
        raise ValueError("baseline_label must be a non-empty string")
    if not isinstance(candidate_label, str) or not candidate_label.strip():
        raise ValueError("candidate_label must be a non-empty string")
    if baseline_kind is not None and (
        not isinstance(baseline_kind, str) or not baseline_kind.strip()
    ):
        raise ValueError("baseline_kind must be a non-empty string when provided")

    memory_only = record.get("measurement_mode") == "memory-only"
    operation_count = int(record["identity"]["operation_count"])
    baseline = _summarize_side(record["baseline"], operation_count, memory_only=memory_only)
    candidate = _summarize_side(record["candidate"], operation_count, memory_only=memory_only)
    self_calibration = baseline_kind == "self" and baseline_label == candidate_label
    baseline["label"] = baseline_label
    candidate["label"] = candidate_label
    baseline["self_calibration"] = self_calibration
    candidate["self_calibration"] = self_calibration
    if memory_only:
        timing = {"status": "not_measured", "reason": "memory-only", "time_ratio": None}
    else:
        baseline_samples = [float(value) for value in record["baseline"]["timing"]["samples"]]
        candidate_samples = [float(value) for value in record["candidate"]["timing"]["samples"]]
        timing = _compare_timing(
            baseline_samples, candidate_samples, self_calibration=self_calibration
        )
        timing["cpu"] = _compare_cpu(
            record["baseline"]["timing"].get("cpu_rounds"),
            record["candidate"]["timing"].get("cpu_rounds"),
        )
    memory = _compare_memory(baseline, candidate, memory_gate=memory_gate,
                             memory_primary_metric=memory_primary_metric)
    allocations = _compare_allocations(
        baseline, candidate, operation_count, allocation_gate=allocation_gate
    )

    statuses = {
        "timing": timing["status"],
        "memory": memory["status"],
        "allocations": allocations["status"],
    }
    reasons: list[str] = []
    if timing["status"] == "fail":
        reasons.append(
            f"timing regressed by {timing['change_percent']:+.2f}% beyond measured noise"
        )
    for metric, result in memory["metrics"].items():
        if result.get("gated") and result.get("status") == "fail":
            change = result.get("change_percent")
            if change is None:
                detail = f"an absolute {result.get('absolute_change')!r} units"
            else:
                detail = f"{change:+.2f}%"
            reasons.append(f"{metric} increased by {detail} beyond measured noise")
    for metric, result in allocations["metrics"].items():
        if result.get("gated") and result.get("status") == "fail":
            change = result.get("change_percent")
            if change is None:
                detail = f"an absolute {result.get('absolute_change')!r} units"
            else:
                detail = f"{change:+.2f}%"
            reasons.append(f"{metric} increased by {detail} beyond measured noise")
    if "inconclusive" in statuses.values() or "incomplete" in statuses.values():
        reasons.append("one or more verdicts need additional measurement rounds or metrics")
    if memory["status"] == "not_measured":
        reasons.append("resident-memory measurements were not supplied")
    if allocations["status"] == "not_measured":
        reasons.append("allocation measurements were not supplied")

    if "fail" in statuses.values():
        overall = "fail"
    elif any(status in ("incomplete", "inconclusive", "not_measured") for status in statuses.values()):
        overall = "incomplete"
    else:
        overall = "pass"

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        **({"measurement_mode": "memory-only"} if memory_only else {}),
        "identity": dict(record["identity"]),
        "baseline_label": baseline_label,
        "baseline_kind": baseline_kind,
        "candidate_label": candidate_label,
        "baseline": baseline,
        "candidate": candidate,
        "comparison": {
            "timing": timing,
            "time_ratio": timing["time_ratio"],
            "memory": memory,
            "allocations": allocations,
        },
        "verdict": {
            **statuses,
            "overall": overall,
            "reasons": reasons,
        },
    }
