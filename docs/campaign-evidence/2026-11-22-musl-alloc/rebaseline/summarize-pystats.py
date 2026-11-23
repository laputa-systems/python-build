#!/usr/bin/env python3
import ast
import json
import re
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline/pystats")
WORKLOADS = (
    "nbody", "spectral_norm", "float", "richards", "raytrace", "fannkuch",
    "deltablue", "chaos", "go", "nqueens", "hexiom", "scimark_sor",
    "scimark_sparse_mat_mult", "scimark_fft", "scimark_lu", "scimark_monte_carlo",
)
STAGES = ("h-pystats", "jes2-parent-pystats", "jes3-candidate-pystats")
COUNTERS = ("_CHECK_PERIODIC", "_STORE_FAST_FUSED", "_BOX_FLOAT", "_TIER2_RESUME_CHECK")


def workload_data(stage: str, workload: str) -> dict:
    directory = ROOT / stage / workload
    counters = {name: 0 for name in COUNTERS}
    for path in directory.glob("stats-*.txt"):
        for line in path.read_text().splitlines():
            match = re.fullmatch(r"uops\[(.+)\]\.execution_count\s*:\s*(\d+)", line)
            if match:
                name = re.sub(r"_r\d+$", "", match.group(1))
                if name in counters:
                    counters[name] += int(match.group(2))
    output = ast.literal_eval((directory / "stdout.txt").read_text().strip())
    return {"counters": counters, "output": output}


data = {
    stage: {workload: workload_data(stage, workload) for workload in WORKLOADS}
    for stage in STAGES
}
comparisons = {
    "jes2": ("jes2-parent-pystats", "h-pystats"),
    "jes3": ("h-pystats", "jes3-candidate-pystats"),
}
result = {"stages": data, "comparisons": {}}
lines = []
for comparison, (parent, candidate) in comparisons.items():
    result["comparisons"][comparison] = {}
    lines.append(f"[{comparison}] parent={parent} candidate={candidate}")
    for workload in WORKLOADS:
        parent_data = data[parent][workload]
        candidate_data = data[candidate][workload]
        row = {"parent": parent_data["counters"], "candidate": candidate_data["counters"],
               "delta_percent": {}}
        for counter in COUNTERS:
            before = parent_data["counters"][counter]
            after = candidate_data["counters"][counter]
            row["delta_percent"][counter] = None if before == 0 else 100 * (after / before - 1)
        row["result_equal"] = (
            parent_data["output"]["operation_count"] == candidate_data["output"]["operation_count"]
            and parent_data["output"]["digest"] == candidate_data["output"]["digest"]
        )
        row["parent_result"] = {
            "operation_count": parent_data["output"]["operation_count"],
            "digest": parent_data["output"]["digest"],
        }
        row["candidate_result"] = {
            "operation_count": candidate_data["output"]["operation_count"],
            "digest": candidate_data["output"]["digest"],
        }
        result["comparisons"][comparison][workload] = row
        check_delta = row["delta_percent"]["_CHECK_PERIODIC"]
        store_delta = row["delta_percent"]["_STORE_FAST_FUSED"]
        check_delta_text = "n/a" if check_delta is None else f"{check_delta:+.6f}%"
        store_delta_text = "n/a" if store_delta is None else f"{store_delta:+.6f}%"
        lines.append(
            f"{workload}: CHECK_PERIODIC {row['parent']['_CHECK_PERIODIC']} -> "
            f"{row['candidate']['_CHECK_PERIODIC']} ({check_delta_text}); "
            f"STORE_FAST_FUSED {row['parent']['_STORE_FAST_FUSED']} -> "
            f"{row['candidate']['_STORE_FAST_FUSED']} ({store_delta_text}); "
            f"BOX_FLOAT {row['parent']['_BOX_FLOAT']} -> {row['candidate']['_BOX_FLOAT']}; "
            f"TIER2_RESUME_CHECK {row['parent']['_TIER2_RESUME_CHECK']} -> "
            f"{row['candidate']['_TIER2_RESUME_CHECK']}; "
            f"operations_equal={row['result_equal']}"
        )

(Path(__file__).parent / "pystats-summary.json").write_text(
    json.dumps(result, indent=2) + "\n", encoding="utf-8")
(Path(__file__).parent / "pystats-comparisons.txt").write_text(
    "\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote pystats-summary.json and pystats-comparisons.txt for {len(WORKLOADS)} workloads")
