"""Re-judge archived bench receipts under injected timing shifts.

Each receipt's per-run wall/cpu ratios and intervals are scaled by a known
factor, one entity at a time, and judged again by the current
`perf_verdict` policy. Same-source A/A and semantically null receipts then
act as controls: a factor of 1 must not reject or accept, factors beyond
the practical floor must be detected, and small factors must stay within
budget. Use this before changing the acceptance policy.
"""

from __future__ import annotations

import argparse
import collections
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import perf_verdict as pv

TIMING = ("wall", "cpu")


def scaled(observations: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    out = copy.deepcopy(observations)
    for run in out:
        for name, metric in run["metrics"].items():
            if name in TIMING and factor != 1.0:
                metric["ratios"] = [ratio * factor for ratio in metric.get("ratios") or []]
                if metric.get("ci95"):
                    metric["ci95"] = [bound * factor for bound in metric["ci95"]]
            floor = pv.SERC_MEMORY_ALLOWANCE if name == pv.PHYSICAL_FOOTPRINT else pv.PRACTICAL_FLOOR
            metric["class"] = pv.classify(metric.get("ci95"), floor=floor)
    return out


def judge(receipt: Path, factor: float) -> tuple[collections.Counter, collections.Counter]:
    verdict = json.loads((receipt / "verdict.json").read_text())
    raw, decision = verdict["raw"], verdict["decision"]
    unshifted = {name: pv.entity_verdict(scaled(obs, 1.0)) for name, obs in raw.items()}
    metrics: collections.Counter = collections.Counter()
    decisions: collections.Counter = collections.Counter()
    for name, observations in raw.items():
        shifted = pv.entity_verdict(scaled(observations, factor))
        for metric in TIMING:
            if metric in shifted["metrics"]:
                metrics[shifted["metrics"][metric]["verdict"]] += 1
        entities = {**unshifted, name: shifted}
        decisions[pv.decide(entities, targets=decision["targets"], runs=decision["runs"],
                            quiet=True, gate=True)["decision"]] += 1
    return metrics, decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("receipts", nargs="+", type=Path, help="bench result directories with verdict.json")
    parser.add_argument("--factor", type=float, action="append", required=True,
                        help="candidate/baseline shift to inject; repeatable")
    args = parser.parse_args()
    for factor in args.factor:
        metrics: collections.Counter = collections.Counter()
        decisions: collections.Counter = collections.Counter()
        for receipt in args.receipts:
            found_metrics, found_decisions = judge(receipt, factor)
            metrics.update(found_metrics)
            decisions.update(found_decisions)
        total = sum(metrics.values())
        shares = "  ".join(f"{name}={metrics[name] / total:.1%}"
                           for name in (pv.RUN_IMPROVED, pv.RUN_WITHIN_BUDGET,
                                        pv.RUN_INCONCLUSIVE, pv.RUN_REGRESSED))
        print(f"factor={factor:g} metrics={total}: {shares} | decisions: {dict(sorted(decisions.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
