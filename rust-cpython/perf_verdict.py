"""Hill-climbing verdicts and per-module goals over paired measurements.

An entity is either an application workload measured by
`benchmarks/bench.py` or a module kernel measured by `perf_modules.py`.
Each run yields, per metric, candidate/baseline ratios paired by round and a
bootstrap 95% interval for their median. (`bench.py`'s own `direction`
compares against a 3-sigma per-sample spread, which does not narrow with
more rounds, so a hill climb uses the interval of the paired median.) This
module turns runs into verdicts, one decision per attempt, and goal
statuses against the control. It performs no I/O.

A claim counts only when it replicates. Each run reads one of four outcomes
per metric: `improved` (its whole interval lies below 1 - allowance),
`regressed` (its whole interval lies above 1 + allowance), `within budget`
(its upper bound is at most 1 + allowance), or `inconclusive`. A metric takes
a replicated outcome only when every run supports it; runs that disagree, or
that cannot bound the ratio, leave it `inconclusive`. The allowance is
`PRACTICAL_FLOOR`. Ratios are candidate over baseline everywhere, so below 1
is faster or smaller.

Memory-only acceptance evaluates module load footprint and working peak,
and supported workload peak memory, with peak RSS required. A candidate that
links Serc over a non-Serc baseline records peak RSS as a known cost and
decides on physical footprint at a +8% allowance. Output equality, complete
memory evidence, and independent replication remain mandatory; CPU, wall
time, and host quietness cannot qualify or block acceptance.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

PRACTICAL_FLOOR = 0.01
BOOTSTRAP_CONFIDENCE = 0.95
BENCH_MEMORY_METRICS = ("peak_rss", "peak_private", "peak_pss")
PHYSICAL_FOOTPRINT = "peak_phys_footprint"
SERC_MEMORY_ALLOWANCE = 0.08
SERC_RECORDED_COSTS = ("peak_rss",)
SERC_MEMORY_METRICS = ("peak_private", "peak_pss", PHYSICAL_FOOTPRINT)
SERC_ACCEPTANCE_POLICY = "memory-only-serc-cost"
WORKLOAD_IMPROVABLE = ("wall", "cpu", "peak_rss")
GOAL_CEILING = 1.0
GOAL_FLOOR = 0.9
MODULE_METRIC_FLOORS = {
    "cpu": 0.0,
    "load_footprint": 64 * 1024,
    "working_peak": 256 * 1024,
}
MODULE_MEMORY_METRICS = ("load_footprint", "working_peak")
MODULE_SAMPLE_FIELDS = {
    "cpu": "cpu_seconds_per_iteration",
    "load_footprint": "load_footprint_bytes",
    "working_peak": "working_peak_bytes",
}

ACCEPT = "ACCEPT"
REJECT = "REJECT"
WITHIN_BUDGET = "WITHIN-BUDGET"
INCONCLUSIVE = "INCONCLUSIVE"

RUN_IMPROVED = "improved"
RUN_REGRESSED = "regressed"
RUN_WITHIN_BUDGET = "within budget"
RUN_INCONCLUSIVE = "inconclusive"


def _bootstrap(values: Sequence[float]) -> list[float] | None:
    from benchmarks.harness.statistics import bootstrap_median_interval

    return bootstrap_median_interval(values, confidence=BOOTSTRAP_CONFIDENCE)


def classify(interval: Sequence[float] | None, *, floor: float = PRACTICAL_FLOOR) -> str:
    """Classify one run's ratio interval against the allowance."""
    if interval is None or len(interval) != 2:
        return RUN_INCONCLUSIVE
    low, high = float(interval[0]), float(interval[1])
    if high < 1.0 - floor:
        return RUN_IMPROVED
    if low > 1.0 + floor:
        return RUN_REGRESSED
    if high <= 1.0 + floor:
        return RUN_WITHIN_BUDGET
    return RUN_INCONCLUSIVE


def replicate(run_classes: Sequence[str]) -> str:
    """Combine per-run outcomes: every run must support the claim."""
    if not run_classes:
        return RUN_INCONCLUSIVE
    for claim in (RUN_IMPROVED, RUN_REGRESSED):
        if all(item == claim for item in run_classes):
            return claim
    if all(item in {RUN_IMPROVED, RUN_WITHIN_BUDGET} for item in run_classes):
        return RUN_WITHIN_BUDGET
    return RUN_INCONCLUSIVE


def directions_disagree(run_classes: Sequence[str]) -> bool:
    return RUN_IMPROVED in run_classes and RUN_REGRESSED in run_classes


def _paired(baseline: Sequence[float], candidate: Sequence[float], floor: float = 0.0) -> list[float]:
    count = min(len(baseline), len(candidate))
    ratios = []
    for index in range(count):
        base, cand = max(float(baseline[index]), floor), max(float(candidate[index]), floor)
        if base > 0:
            ratios.append(cand / base)
    return ratios


def _metric(ratios: list[float], interval: Sequence[float] | None = None,
            values: tuple[Sequence[float], Sequence[float]] | None = None) -> dict[str, Any]:
    if interval is None and len(ratios) >= 2:
        interval = _bootstrap(ratios)
    metric = {"ratios": ratios, "ci95": interval, "class": classify(interval)}
    if values is not None:
        metric["values"] = {"baseline": list(values[0]), "candidate": list(values[1])}
    return metric


# ------------------------------------------------------- run observations


def _memory_interval(metric: Mapping[str, Any]) -> list[float] | None:
    change = metric.get("absolute_change")
    noise = metric.get("noise_allowance")
    baseline = metric.get("baseline")
    median = baseline.get("median") if isinstance(baseline, Mapping) else None
    if metric.get("status") == "inconclusive":
        return None
    if not all(isinstance(value, (int, float)) for value in (change, noise, median)) or median <= 0:
        return None
    return [1.0 + (change - noise) / median, 1.0 + (change + noise) / median]


def _physical_footprint_samples(workload: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    sides = {}
    for side in ("baseline", "candidate"):
        memory = workload.get(side)
        rounds = memory.get("memory", {}).get("rounds", ()) if isinstance(memory, Mapping) else ()
        sides[side] = [float(record[PHYSICAL_FOOTPRINT]) for record in rounds
                       if isinstance(record, Mapping)
                       and isinstance(record.get(PHYSICAL_FOOTPRINT), (int, float))
                       and not isinstance(record.get(PHYSICAL_FOOTPRINT), bool)]
    return sides["baseline"], sides["candidate"]


def run_observation(workload: Mapping[str, Any], *, floor: float = PRACTICAL_FLOOR,
                    memory_only: bool = False, serc_pair: bool = False) -> dict[str, Any]:
    """One bench.py run of one application workload."""
    metrics: dict[str, Any] = {}
    if not memory_only:
        timing = workload["comparison"]["timing"]
        metrics = {
            "wall": _metric([float(value) for value in timing.get("paired_ratio_samples") or []],
                            timing.get("median_ratio_ci95")),
        }
        cpu = timing.get("cpu")
        cpu_metric = cpu.get("metrics", {}).get("total_seconds_per_operation") \
            if isinstance(cpu, Mapping) and cpu.get("status") == "compared" else None
        if isinstance(cpu_metric, Mapping):
            metrics["cpu"] = _metric(_paired(cpu_metric.get("baseline_samples") or [],
                                             cpu_metric.get("candidate_samples") or []))
        if (timing.get("status") == "fail" and metrics["wall"]["ci95"] is not None
                and metrics["wall"]["class"] in {RUN_WITHIN_BUDGET, RUN_INCONCLUSIVE}):
            metrics["wall"]["class"] = RUN_REGRESSED
    memory = workload["comparison"].get("memory", {})
    for name in BENCH_MEMORY_METRICS:
        metric = memory.get("metrics", {}).get(name) if isinstance(memory, Mapping) else None
        if isinstance(metric, Mapping):
            ratio = metric.get("ratio_candidate_over_baseline")
            interval = _memory_interval(metric)
            metrics[name] = {"ratios": [ratio] if isinstance(ratio, (int, float)) else [],
                             "ci95": interval, "class": classify(interval, floor=floor)}
    if memory_only and serc_pair:
        baseline_footprint, candidate_footprint = _physical_footprint_samples(workload)
        if baseline_footprint and candidate_footprint:
            detail = _metric(_paired(baseline_footprint, candidate_footprint))
            detail["class"] = classify(detail["ci95"], floor=SERC_MEMORY_ALLOWANCE)
            metrics[PHYSICAL_FOOTPRINT] = detail
    return {"kind": "workload", "metrics": metrics, "mismatch": False}


def module_observation(baseline: Sequence[Mapping[str, Any]],
                       candidate: Sequence[Mapping[str, Any]], *, memory_only: bool = False) -> dict[str, Any]:
    """One run of one module kernel: samples paired by round."""
    digests = {sample["digest"] for sample in [*baseline, *candidate]}
    metrics = {}
    for name, field in MODULE_SAMPLE_FIELDS.items():
        if memory_only and name == "cpu":
            continue
        base = [float(sample[field]) for sample in baseline]
        cand = [float(sample[field]) for sample in candidate]
        metrics[name] = _metric(_paired(base, cand, MODULE_METRIC_FLOORS[name]), values=(base, cand))
    return {"kind": "module", "metrics": metrics, "mismatch": len(digests) != 1}


def mismatch_observation(kind: str) -> dict[str, Any]:
    """The two interpreters produced different outputs; nothing was timed."""
    return {"kind": kind, "metrics": {}, "mismatch": True}


# ---------------------------------------------------------------- verdicts


def entity_verdict(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Replicate one entity's per-run observations into metric verdicts."""
    kind = runs[0]["kind"] if runs else "workload"
    names = list(dict.fromkeys(name for run in runs for name in run["metrics"]))
    metrics = {}
    for name in names:
        classes = [run["metrics"].get(name, {}).get("class", RUN_INCONCLUSIVE) for run in runs]
        ratios = [value for run in runs for value in run["metrics"].get(name, {}).get("ratios", [])]
        values = {side: [value for run in runs
                         for value in run["metrics"].get(name, {}).get("values", {}).get(side, [])]
                  for side in ("baseline", "candidate")}
        metrics[name] = {
            "verdict": replicate(classes),
            "per_run": classes,
            "per_run_ci95": [run["metrics"].get(name, {}).get("ci95") for run in runs],
            "pooled": {
                "median": float(statistics.median(ratios)) if ratios else None,
                "ci95": _bootstrap(ratios) if len(ratios) >= 2 else None,
                "samples": len(ratios),
                **({f"{side}_median": float(statistics.median(found)) for side, found in values.items()}
                   if all(values.values()) else {}),
            },
        }
    return {
        "kind": kind,
        "runs": len(runs),
        "mismatch": any(run["mismatch"] for run in runs),
        "improvable": list(MODULE_SAMPLE_FIELDS if kind == "module" else WORKLOAD_IMPROVABLE),
        "metrics": metrics,
    }


def _policy_metrics(verdict: Mapping[str, Any], *, serc_pair: bool = False) -> Sequence[str]:
    if verdict["kind"] == "module":
        return MODULE_MEMORY_METRICS
    return SERC_MEMORY_METRICS if serc_pair else BENCH_MEMORY_METRICS


def _unmeasured(detail: Mapping[str, Any] | None) -> bool:
    return detail is None or all(interval is None for interval in detail["per_run_ci95"])


def _missing_memory(entities: Mapping[str, Mapping[str, Any]], *, serc_pair: bool = False) -> list[str]:
    missing = []
    for name, verdict in entities.items():
        if verdict["mismatch"]:
            continue
        required = (MODULE_MEMORY_METRICS if verdict["kind"] == "module"
                    else ("peak_rss", PHYSICAL_FOOTPRINT) if serc_pair else ("peak_rss",))
        for metric in required:
            if _unmeasured(verdict["metrics"].get(metric)):
                missing.append(f"{name} {metric}")
    return sorted(missing)


def goal_status(verdict: Mapping[str, Any], *, memory_only: bool = False) -> dict[str, Any]:
    """Classify a module verdict (candidate over control) against the band.

    Per metric: OVER when every run's interval sits above the ceiling plus
    the allowance; BEYOND when every run's interval sits below 0.9x; MET
    when every run's upper bound is at most the ceiling plus the allowance;
    otherwise UNCLEAR. The module is OVER if any metric is, UNCLEAR if any
    is, BEYOND only if all are, and MET otherwise.
    """
    if verdict.get("mismatch"):
        return {"status": "MISMATCH", "metrics": {}}
    metrics = {}
    names = (MODULE_MEMORY_METRICS if memory_only else verdict["metrics"])
    for name in names:
        detail = verdict["metrics"].get(name)
        if detail is None or (memory_only and verdict.get("runs", 0) < 2):
            metrics[name] = "UNCLEAR"
            continue
        intervals = detail["per_run_ci95"]
        if intervals and all(ci and ci[0] > GOAL_CEILING + PRACTICAL_FLOOR for ci in intervals):
            status = "OVER"
        elif intervals and all(ci and ci[1] < GOAL_FLOOR for ci in intervals):
            status = "BEYOND"
        elif intervals and all(ci and ci[1] <= GOAL_CEILING + PRACTICAL_FLOOR for ci in intervals):
            status = "MET"
        else:
            status = "UNCLEAR"
        metrics[name] = status
    values = set(metrics.values())
    overall = ("OVER" if "OVER" in values else "UNCLEAR" if "UNCLEAR" in values or not values
               else "BEYOND" if values == {"BEYOND"} else "MET")
    return {"status": overall, "metrics": metrics}


def _every(runs: int) -> str:
    return "the only run" if runs == 1 else f"all {runs} runs"


def decide(entities: Mapping[str, Mapping[str, Any]], *, targets: Sequence[str],
           runs: int, quiet: bool | None, gate: bool,
           known_mismatches: Sequence[str] = (), memory_only: bool = False,
           serc_pair: bool = False) -> dict[str, Any]:
    """Decide one attempt from replicated entity verdicts.

    Memory-only decisions select memory metrics for targets and guards,
    require both module rows and workload RSS, and ignore host quietness.
    Documented baseline output differences retain their existing exemption.

    REJECT: any evaluated entity regresses a metric in every run, or its
    outputs differ from the baseline (outside `known_mismatches`).
    INCONCLUSIVE: the host was not quiet, a gate decision has fewer than two
    runs, a target lacks samples, or any evaluated metric is inconclusive.
    ACCEPT: a target improves an improvable metric and every other evaluated
    metric is improved or within budget.
    WITHIN-BUDGET: every evaluated metric is within budget and no target
    improved.
    """
    reasons: list[str] = []
    regressions, inconclusive, unstable, mismatches = [], [], [], []
    for name, verdict in sorted(entities.items()):
        if verdict["mismatch"]:
            if name not in known_mismatches:
                mismatches.append(name)
            continue
        for metric, detail in verdict["metrics"].items():
            if memory_only and metric not in _policy_metrics(verdict, serc_pair=serc_pair):
                continue
            if detail["verdict"] == RUN_REGRESSED:
                regressions.append(f"{name} {metric}")
            elif detail["verdict"] == RUN_INCONCLUSIVE:
                inconclusive.append(f"{name} {metric}")
                if directions_disagree(detail["per_run"]):
                    unstable.append(f"{name} {metric}")
    missing = sorted(set(targets) - set(entities))
    insufficient = sorted(
        name for name in targets if name in entities and not entities[name]["mismatch"]
        and all(_unmeasured(entities[name]["metrics"].get(metric))
                for metric in entities[name]["improvable"])
    )
    if memory_only:
        insufficient = _missing_memory(entities, serc_pair=serc_pair)
    improved = sorted(
        f"{name} {metric}" for name in targets if name in entities
        for metric in (_policy_metrics(entities[name], serc_pair=serc_pair)
                       if memory_only else entities[name]["improvable"])
        if entities[name]["metrics"].get(metric, {}).get("verdict") == RUN_IMPROVED
    )
    if mismatches:
        decision = REJECT
        reasons.append("outputs differ from the baseline: " + ", ".join(mismatches))
    elif regressions:
        decision = REJECT
        reasons.append(f"regressed in {_every(runs)}: " + ", ".join(regressions))
    elif not quiet and not memory_only:
        decision = INCONCLUSIVE
        reasons.append("host was not quiet during measurement")
    elif (gate or memory_only) and runs < 2:
        decision = INCONCLUSIVE
        reasons.append("a gate decision needs at least two independent runs")
    elif missing or insufficient:
        decision = INCONCLUSIVE
        reasons.append("targets lack samples: " + ", ".join([*missing, *insufficient]))
    elif inconclusive:
        decision = INCONCLUSIVE
        reasons.append("not bounded within budget in every run: " + ", ".join(inconclusive))
        if unstable:
            reasons.append("runs disagree in direction: " + ", ".join(unstable))
    elif improved:
        decision = ACCEPT
        reasons.append(f"improved in {_every(runs)}: " + ", ".join(improved))
    else:
        decision = WITHIN_BUDGET
        reasons.append("every evaluated metric is within budget and no target improved")
    known = sorted(name for name in known_mismatches
                   if entities.get(name, {}).get("mismatch"))
    if known:
        reasons.append("documented output differences, not timed: " + ", ".join(known))
    if memory_only and serc_pair:
        reasons.append("Serc build: peak RSS is a recorded cost of linking Serc and does not "
                       "decide; physical footprint rejects above +8%")
    if not gate:
        reasons.append("exploratory: not acceptance evidence")
    acceptance_policy = ("memory-only" if memory_only else "all-metrics")
    if memory_only and serc_pair:
        acceptance_policy = SERC_ACCEPTANCE_POLICY
    return {
        "decision": decision,
        "acceptance_policy": acceptance_policy,
        "recorded_costs": list(SERC_RECORDED_COSTS) if memory_only and serc_pair else [],
        "gate": gate,
        "runs": runs,
        "quiet": quiet,
        "practical_floor": PRACTICAL_FLOOR,
        "targets": list(targets),
        "improved": improved,
        "regressions": regressions,
        "inconclusive": inconclusive,
        "unstable": unstable,
        "mismatches": mismatches,
        "reasons": reasons,
    }


def calibration(entities: Mapping[str, Mapping[str, Any]], *, runs: int, quiet: bool | None,
                gate: bool, memory_only: bool = False) -> dict[str, Any]:
    """Judge a self-comparison: identical interpreters must read within budget.

    Any improvement, regression, direction disagreement between runs, or
    output mismatch means the harness can report a difference that does not
    exist at this allowance and run count. A metric that stays inconclusive
    leaves the calibration inconclusive.
    """
    evaluated = [(name, metric, detail)
                 for name, verdict in entities.items() if not verdict["mismatch"]
                 for metric, detail in verdict["metrics"].items()
                 if not memory_only or metric in _policy_metrics(verdict)]
    differences = sorted(
        [f"{name}: outputs differ" for name, verdict in entities.items() if verdict["mismatch"]]
        + [f"{name} {metric}: {'unstable' if directions_disagree(detail['per_run']) else detail['verdict']}"
           for name, metric, detail in evaluated
           if detail["verdict"] in {RUN_IMPROVED, RUN_REGRESSED} or directions_disagree(detail["per_run"])]
    )
    undecided = sorted(f"{name} {metric}" for name, metric, detail in evaluated
                       if detail["verdict"] == RUN_INCONCLUSIVE)
    missing = _missing_memory(entities) if memory_only else []
    if memory_only and differences:
        outcome, reasons = "CALIBRATION-FAILED", ["identical interpreters read different: "
                                                  + ", ".join(differences)]
    elif memory_only and (runs < 2 or missing):
        outcome, reasons = "CALIBRATION-INCONCLUSIVE", [
            "memory calibration needs two independent runs and complete memory samples: " + ", ".join(missing)]
    elif not quiet and not memory_only:
        outcome, reasons = "CALIBRATION-INCONCLUSIVE", ["host was not quiet during measurement"]
    elif differences:
        outcome, reasons = "CALIBRATION-FAILED", ["identical interpreters read different: "
                                                  + ", ".join(differences)]
    elif undecided:
        outcome, reasons = "CALIBRATION-INCONCLUSIVE", [
            "identical interpreters are not bounded within budget: " + ", ".join(undecided)]
    else:
        outcome, reasons = "CALIBRATION-OK", ["identical interpreters read within budget everywhere"]
    return {
        "decision": outcome,
        "acceptance_policy": "memory-only" if memory_only else "all-metrics",
        "gate": gate,
        "runs": runs,
        "quiet": quiet,
        "practical_floor": PRACTICAL_FLOOR,
        "targets": [],
        "improved": [],
        "regressions": [],
        "inconclusive": [],
        "unstable": [],
        "mismatches": [],
        "differences": differences,
        "reasons": reasons,
    }


# --------------------------------------------------------------- rendering


def _format_metric(name: str, detail: Mapping[str, Any]) -> str:
    median = detail["pooled"]["median"]
    interval = detail["pooled"]["ci95"]
    if median is None:
        value = "n/a"
    elif interval:
        value = f"{median:.3f}[{interval[0]:.3f},{interval[1]:.3f}]"
    else:
        value = f"{median:.3f}"
    return f"{name} {value} {detail['verdict']}"


def render(entities: Mapping[str, Mapping[str, Any]], decision: Mapping[str, Any],
           goals: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """One line per entity, then the decision and commit-message-ready reasons."""
    lines = []
    width = max([len(name) for name in entities] + [12]) + 1
    recorded = set(decision.get("recorded_costs") or ())
    for name, verdict in sorted(entities.items(), key=lambda item: (item[1]["kind"], item[0])):
        marker = "*" if name in decision["targets"] else " "
        goal = f"  GOAL {goals[name]['status']}" if goals and name in goals else ""
        if verdict["mismatch"]:
            body = "outputs differ from the baseline; not timed"
        else:
            body = " | ".join(_format_metric(metric, detail)
                              + (" (recorded cost)" if metric in recorded else "")
                              for metric, detail in verdict["metrics"].items())
        lines.append(f"{marker}{name:<{width}} {body}{goal}")
    lines.append("")
    policy = decision.get("acceptance_policy")
    quietness = ("not checked" if policy in {"memory-only", SERC_ACCEPTANCE_POLICY}
                 else "yes" if decision["quiet"] else "no")
    lines.append(f"DECISION: {decision['decision']} ({'gate' if decision['gate'] else 'explore'}, "
                 f"{decision['runs']} run(s), floor {decision['practical_floor']:.0%}, "
                 f"quiet={quietness}; * = target; ratios candidate/baseline)")
    if policy == SERC_ACCEPTANCE_POLICY:
        lines.append("POLICY: memory-only Serc vs non-Serc; peak RSS is a recorded cost of "
                     "linking Serc and does not decide; physical footprint rejects above +8%")
    elif policy == "memory-only":
        lines.append("POLICY: memory-only; CPU and wall time do not qualify or block")
    lines.extend(f"  - {reason}" for reason in decision["reasons"])
    return "\n".join(lines)


def render_goals(goals: Mapping[str, Mapping[str, Any]],
                 entities: Mapping[str, Mapping[str, Any]]) -> str:
    """Goal table against the control, worst first, with status counts."""
    order = {"MISMATCH": 0, "OVER": 1, "UNCLEAR": 2, "MET": 3, "BEYOND": 4}

    def worst(name: str) -> float:
        medians = [detail["pooled"]["median"] or 0.0 for detail in entities[name]["metrics"].values()]
        return -max(medians, default=0.0)

    def absolute(metric: str, pooled: Mapping[str, Any]) -> str:
        if "candidate_median" not in pooled:
            return ""
        base, cand = pooled["baseline_median"], pooled["candidate_median"]
        if metric == "cpu":
            return f" ({cand * 1e3:.3g}/{base * 1e3:.3g} ms)"
        return f" ({cand / 1024:.0f}/{base / 1024:.0f} KiB)"

    def ratio(pooled: Mapping[str, Any]) -> str:
        median = pooled.get("median")
        return "n/a" if median is None else f"{median:.2f}x"

    lines = []
    for name in sorted(goals, key=lambda item: (order[goals[item]["status"]], worst(item))):
        metrics = entities[name]["metrics"]
        body = " | ".join(
            f"{metric} {ratio(metrics.get(metric, {}).get('pooled', {}))}"
            f"{absolute(metric, metrics.get(metric, {}).get('pooled', {}))}"
            f" {status}"
            for metric, status in goals[name]["metrics"].items()
        ) or "outputs differ from the control"
        lines.append(f"{goals[name]['status']:<8} {name:<24} {body}")
    counts = {status: sum(goal["status"] == status for goal in goals.values()) for status in order}
    lines.append("")
    lines.append("GOALS (candidate/control; ceiling 1.0x, target floor 0.9x): "
                 + ", ".join(f"{status} {count}" for status, count in counts.items() if count))
    return "\n".join(lines)
