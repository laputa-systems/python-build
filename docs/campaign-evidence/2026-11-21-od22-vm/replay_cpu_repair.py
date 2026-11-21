#!/usr/bin/env python3
"""DIAGNOSTIC replay of OD22 VM bench verdicts under a given perf_verdict.py.

Rebuilds workload observations from each run's retained summary.json, reuses
the recorded module observations, and re-decides with the recorded targets,
runs, quiet and gate flags. Raw verdicts are never modified; output is
diagnostic only and is not a gate receipt.

usage: replay_cpu_repair.py <dir-containing-perf_verdict.py> <perf-bench-dir> [run-id ...]
"""
import importlib.util, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
spec = importlib.util.spec_from_file_location("pv", Path(sys.argv[1]) / "perf_verdict.py")
pv = importlib.util.module_from_spec(spec); spec.loader.exec_module(pv)
bench = Path(sys.argv[2])
wanted = sys.argv[3:]
KNOWN_CONTROL_MISMATCHES = ()  # no OD22 comparison used @control as baseline
out = {}
for verdict_path in sorted(bench.glob("2026112[01]T*/verdict.json")):
    run_dir = verdict_path.parent
    if wanted and not any(run_dir.name.startswith(w) for w in wanted):
        continue
    original = json.loads(verdict_path.read_text())
    decision = original["decision"]
    runs = decision["runs"]
    serc_pair = any(pv.PHYSICAL_FOOTPRINT in obs["metrics"]
                    for records in original["raw"].values() for obs in records)
    observations = {}
    for name, records in original["raw"].items():
        if records and records[0]["kind"] == "module":
            observations[name] = records
            continue
        rebuilt = []
        for run in range(1, len(records) + 1):
            summary = run_dir / f"run-{run}" / name / "summary.json"
            if not summary.exists():
                rebuilt.append(pv.mismatch_observation("workload")); continue
            data = json.loads(summary.read_text())
            workloads = data["workloads"]
            workload = workloads[0] if isinstance(workloads, list) else next(iter(workloads.values()))
            rebuilt.append(pv.run_observation(workload, serc_pair=serc_pair))
        observations[name] = rebuilt
    incomplete = [n for n, r in observations.items() if len(r) != runs]
    entities = {n: pv.entity_verdict(r) for n, r in observations.items() if n not in incomplete}
    if "calibrate" in run_dir.name:
        replay = pv.calibration(entities, runs=runs, quiet=decision["quiet"], gate=decision["gate"])
    else:
        replay = pv.decide(entities, targets=decision["targets"], runs=runs, quiet=decision["quiet"],
                           gate=decision["gate"], known_mismatches=KNOWN_CONTROL_MISMATCHES,
                           serc_pair=serc_pair)
    cpu_unbounded = sorted(n for n, e in entities.items()
                           if "cpu" in e["metrics"] and e["metrics"]["cpu"]["verdict"] == pv.RUN_INCONCLUSIVE)
    out[run_dir.name] = {
        "original_decision": decision["decision"],
        "replay_decision": replay["decision"],
        "replay_inconclusive": replay.get("inconclusive"),
        "replay_regressions": replay.get("regressions"),
        "cpu_inconclusive_entities": cpu_unbounded,
        "entities_without_cpu_metric": sorted(n for n, e in entities.items()
                                              if e["kind"] == "workload" and "cpu" not in e["metrics"]),
    }
json.dump({"diagnostic": True, "verdict_module": str(Path(sys.argv[1]).resolve()), "runs": out},
          sys.stdout, indent=1, sort_keys=True)
print()
