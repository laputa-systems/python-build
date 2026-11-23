import ast
import json
import re
import statistics
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
CG = RUN / "cachegrind"
ARMS = ("parent", "cand")
WORKLOADS = (
    "corpus", "scimark_fft", "nbody", "scimark_sparse_mat_mult",
    "setup-nbody", "setup-scimark_fft", "setup-scimark_sparse_mat_mult",
)
KERNELS = ("nbody", "scimark_fft", "scimark_sparse_mat_mult")
PUBLIC_ALLOCATORS = {"malloc", "free", "realloc", "calloc"}


def parse_annotation(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    total = None
    rows = []
    for line in text.splitlines():
        if "PROGRAM TOTALS" in line and total is None:
            match = re.match(r"\s*([\d,]+)", line)
            if match:
                total = int(match.group(1).replace(",", ""))
        match = re.match(r"\s*([\d,]+)\s+\([^)]*\)\s+(.+)", line)
        if match is None or "PROGRAM TOTALS" in line:
            continue
        location = match.group(2)
        if ":" not in location:
            continue
        source, function = location.split(":", 1)
        rows.append((int(match.group(1).replace(",", "")), source, function))
    if total is None:
        raise ValueError(f"missing PROGRAM TOTALS in {path}")
    return total, rows


def parse_run_name(path):
    stem = path.name.removesuffix(".annotate.txt")
    parts = stem.split("-")
    arm = parts[0]
    if arm not in ARMS:
        return None
    if len(parts) >= 4 and parts[-2].startswith("retry"):
        group, run = parts[-2], int(parts[-1])
        workload = "-".join(parts[1:-2])
    else:
        group, run = "initial", int(parts[-1])
        workload = "-".join(parts[1:-1])
    return arm, workload, group, run


def parse_kernel_result(text):
    for line in reversed(text.splitlines()):
        try:
            value = ast.literal_eval(line)
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, dict) and {"operation_count", "digest"} <= value.keys():
            return value
    return None


def load_profiles():
    data = {}
    for path in sorted(CG.glob("*.annotate.txt")):
        key = parse_run_name(path)
        if key is None:
            continue
        total, rows = parse_annotation(path)
        stem = path.name.removesuffix(".annotate.txt")
        stderr = (CG / f"{stem}.stderr").read_text(encoding="utf-8", errors="replace")
        ref_match = re.search(r"I refs:\s*([\d,]+)", stderr)
        stdout = (CG / f"{stem}.stdout").read_text(encoding="utf-8", errors="replace").strip()
        exit_code = (CG / f"{stem}.exit").read_text().strip()
        matched = [
            (ir, source, function)
            for ir, source, function in rows
            if "src/malloc/" in source or function in PUBLIC_ALLOCATORS
        ]
        data[key] = {
            "path": path,
            "total": total,
            "rows": rows,
            "allocator_matches": matched,
            "allocator_ir": sum(item[0] for item in matched),
            "stderr_ir": (
                int(ref_match.group(1).replace(",", "")) if ref_match else None
            ),
            "stdout": stdout,
            "result": parse_kernel_result(stdout),
            "exit": exit_code,
        }
    return data


def symmetric_delta(first, second):
    return 100 * abs(first - second) / ((first + second) / 2)


def pair_keys(data, arm, workload, group):
    first = data.get((arm, workload, group, 1))
    second = data.get((arm, workload, group, 2))
    if first is None or second is None:
        return None
    return first, second


def summarize_ir(data):
    totals = [
        "Total Ir is cg_annotate PROGRAM TOTALS; stderr I refs are recorded for cross-check.",
        "Each line retains the workload's raw stdout (including operation/digest output for kernels).",
    ]
    allocator = [
        "Allocator-family self Ir per corpus profile.",
        "Match: every source path containing src/malloc/, plus malloc/free/realloc/calloc at any source path.",
    ]
    matched_lines = [
        "Full per-function self-Ir list for every corpus run; source path is shown for each match.",
    ]
    for key in sorted(data, key=lambda k: (WORKLOADS.index(k[1]), k[2], ARMS.index(k[0]), k[3])):
        arm, workload, group, run = key
        item = data[key]
        stem = item["path"].name.removesuffix(".annotate.txt")
        totals.append(
            f"{stem} total_Ir={item['total']:,} stderr_I_refs={item['stderr_ir']} "
            f"exit={item['exit']} stdout={item['stdout']}"
        )
        if item["exit"] != "0" or item["stderr_ir"] != item["total"]:
            totals.append(f"  evidence_crosscheck=DIFF annotated={item['total']} stderr={item['stderr_ir']}")
        if workload != "corpus":
            continue
        share = 100 * item["allocator_ir"] / item["total"]
        allocator.append(
            f"{stem} allocator_self_Ir={item['allocator_ir']:,} "
            f"total_Ir={item['total']:,} share={share:.6f}%"
        )
        matched_lines.append(
            f"\n{stem}: total allocator-family self Ir={item['allocator_ir']:,}"
        )
        for ir, source, function in sorted(item["allocator_matches"], reverse=True):
            matched_lines.append(f"  {ir:>14,}  {source}:{function}")
    (RUN / "ir-totals.txt").write_text("\n".join(totals) + "\n", encoding="utf-8")
    (RUN / "allocator-self-ir.txt").write_text("\n".join(allocator) + "\n", encoding="utf-8")
    (RUN / "allocator-matched-functions.txt").write_text(
        "\n".join(matched_lines) + "\n", encoding="utf-8"
    )


def summarize_repeatability(data):
    lines = [
        "Symmetric difference = abs(run1-run2) / mean(run1,run2) * 100; procedural limit 0.1%.",
        "Each initial miss triggers exactly one fresh two-run pair on both stages for that workload.",
        "A retry miss is recorded as non-repeatable; no further pair is scheduled.",
    ]
    for workload in WORKLOADS:
        for group in ("initial", "retry1"):
            for arm in ARMS:
                pair = pair_keys(data, arm, workload, group)
                if pair is None:
                    continue
                first, second = pair
                delta = symmetric_delta(first["total"], second["total"])
                status = "within" if delta <= 0.1 else (
                    "non-repeatable" if group == "retry1" else "miss"
                )
                lines.append(
                    f"{group} {workload} {arm}: {first['total']:,} / {second['total']:,} "
                    f"delta={delta:.6f}% {status}"
                )
    reruns = RUN / "repeatability-reruns.txt"
    if reruns.is_file():
        lines.extend(["", "Scheduled fresh pairs:", reruns.read_text().rstrip() or "(none)"])
    (RUN / "repeatability.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def selected_group(data, arm, workload):
    return "retry1" if pair_keys(data, arm, workload, "retry1") else "initial"


def pair_mean(data, arm, workload):
    group = selected_group(data, arm, workload)
    pair = pair_keys(data, arm, workload, group)
    if pair is None:
        raise ValueError(f"missing pair for {arm}/{workload}/{group}")
    return group, statistics.mean(item["total"] for item in pair)


def summarize_net_kernels(data):
    values = {}
    lines = [
        "Means use the fresh retry pair when scheduled; otherwise the initial pair.",
        "Net kernel Ir = whole-kernel Ir mean - matching setup-only Ir mean.",
        "Parent-to-candidate percentage = 100 * (candidate / parent - 1).",
    ]
    for kernel in KERNELS:
        setup = {
            "nbody": "setup-nbody",
            "scimark_fft": "setup-scimark_fft",
            "scimark_sparse_mat_mult": "setup-scimark_sparse_mat_mult",
        }[kernel]
        values[kernel] = {}
        for arm in ARMS:
            whole_group, whole = pair_mean(data, arm, kernel)
            setup_group, setup_ir = pair_mean(data, arm, setup)
            values[kernel][arm] = {
                "whole": whole,
                "setup": setup_ir,
                "net": whole - setup_ir,
            }
            lines.append(
                f"{kernel} {arm} pair={whole_group}/{setup_group} "
                f"whole_mean={whole:.1f} setup_mean={setup_ir:.1f} "
                f"net={whole - setup_ir:.1f}"
            )
        for metric in ("whole", "setup", "net"):
            parent = values[kernel]["parent"][metric]
            cand = values[kernel]["cand"][metric]
            change = 100 * (cand / parent - 1) if parent else float("nan")
            lines.append(
                f"{kernel} parent_to_cand {metric}: parent={parent:.1f} "
                f"cand={cand:.1f} change={change:+.6f}%"
            )
    (RUN / "net-kernel.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_operations(data):
    lines = ["Kernel operation count and output digest from each whole-kernel run."]
    signatures = {}
    for key in sorted(data, key=lambda k: (KERNELS.index(k[1]) if k[1] in KERNELS else 99, k[2], ARMS.index(k[0]), k[3])):
        arm, workload, group, run = key
        if workload not in KERNELS:
            continue
        result = data[key]["result"]
        if result is None:
            lines.append(f"{arm} {workload} {group} run{run}: result-not-parsed raw={data[key]['stdout']}")
            continue
        signature = (result["operation_count"], result["digest"])
        signatures[(arm, workload, group, run)] = signature
        lines.append(
            f"{arm} {workload} {group} run{run}: operations={signature[0]} digest={signature[1]}"
        )
    lines.append("")
    for workload in KERNELS:
        for group in ("initial", "retry1"):
            for run in (1, 2):
                parent = signatures.get(("parent", workload, group, run))
                cand = signatures.get(("cand", workload, group, run))
                if parent is None and cand is None:
                    continue
                lines.append(
                    f"{workload} {group} run{run}: parent={parent} cand={cand} "
                    f"matching={parent == cand}"
                )
    (RUN / "operation-counts-digests.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def summarize_probes():
    modes = ("pyc", "source")
    a4 = ["Private_Dirty KiB medians from six residency-probe observations per mode."]
    a4_values = {}
    for arm in ARMS:
        report = json.loads((RUN / f"a4/{arm}.json").read_text())
        a4_values[arm] = {}
        a4.append(f"{arm}: all_valid={report['all_valid']}")
        for mode in modes:
            raw = [
                sample["rollup:Private_Dirty"]
                for sample in report["modes"][mode]["samples_kib"]
            ]
            median = statistics.median(raw)
            a4_values[arm][mode] = median
            a4.append(f"{arm} {mode}: raw={raw} median={median:g} KiB")
    for mode in modes:
        parent = a4_values["parent"][mode]
        cand = a4_values["cand"][mode]
        a4.append(
            f"cand-parent {mode}: {cand - parent:+g} KiB "
            f"({100 * (cand / parent - 1):+.6f}%)"
        )
    (RUN / "a4-summary.txt").write_text("\n".join(a4) + "\n", encoding="utf-8")

    a5 = ["Peak RSS KiB from /usr/bin/time -f %M; three corpus runs per stage."]
    a5_values = {}
    for arm in ARMS:
        runs = []
        for run in (1, 2, 3):
            text = (RUN / f"a5/{arm}-corpus-{run}.time").read_text().strip()
            match = re.search(r"(\d+)\s*$", text)
            if match is None:
                raise ValueError(f"missing time %M value for {arm} run {run}")
            runs.append(int(match.group(1)))
        median = statistics.median(runs)
        a5_values[arm] = median
        a5.append(f"{arm}: raw={runs} median={median:g} KiB")
    parent = a5_values["parent"]
    cand = a5_values["cand"]
    a5.append(
        f"cand-parent: {cand - parent:+g} KiB ({100 * (cand / parent - 1):+.6f}%)"
    )
    (RUN / "a5-summary.txt").write_text("\n".join(a5) + "\n", encoding="utf-8")


def correctness_failure_ids(arm):
    directory = RUN / "correctness/suites"
    paths = [directory / f"{arm}.log", directory / f"{arm}-capi-verbose.log"]
    ids = set()
    modules = set()
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*(?:FAIL|ERROR):\s*(.+)$", line)
            if match:
                ids.add(match.group(1).strip())
            match = re.match(r"^\s*(test_[a-z0-9_]+)\s+\(.+\)\s+(?:FAIL|ERROR)\s*$", line)
            if match:
                ids.add(match.group(1))
            match = re.match(r"^test (test_[a-z0-9_]+) failed\b", line)
            if match:
                modules.add(match.group(1))
            match = re.search(r"\bin (test_[a-z0-9_]+)$", line)
            if match:
                ids.add(match.group(1))
    return sorted(ids), sorted(modules)


def summarize_correctness():
    correctness = RUN / "correctness"
    exits = (correctness / "exit-summary.txt").read_text().splitlines()
    lines = [
        "Recorded command exit codes; no experiment verdict is computed.",
        *exits,
    ]
    parity = correctness / "parity/parity-comparison.txt"
    if parity.is_file():
        lines.extend(["", "Compile parity normalization:", parity.read_text().rstrip()])
    (correctness / "correctness-summary.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    ids = {arm: correctness_failure_ids(arm) for arm in ARMS}
    failure_lines = [
        "Failure identifiers from the seven-suite logs and any CAPI verbose diagnostic run.",
        "Suite module failures are listed separately when the runner did not emit individual IDs.",
    ]
    for arm in ARMS:
        found, modules = ids[arm]
        failure_lines.append(f"\n{arm} individual IDs ({len(found)}):")
        failure_lines.extend(f"  {item}" for item in found)
        failure_lines.append(f"{arm} failed suite modules: {modules}")
    parent_ids, cand_ids = set(ids["parent"][0]), set(ids["cand"][0])
    failure_lines.extend([
        "",
        f"parent-only IDs: {sorted(parent_ids - cand_ids)}",
        f"cand-only IDs: {sorted(cand_ids - parent_ids)}",
        f"same IDs: {sorted(parent_ids & cand_ids)}",
    ])
    (correctness / "failure-id-diff.txt").write_text(
        "\n".join(failure_lines) + "\n", encoding="utf-8"
    )


def main():
    data = load_profiles()
    summarize_ir(data)
    summarize_repeatability(data)
    summarize_net_kernels(data)
    summarize_operations(data)
    summarize_probes()
    summarize_correctness()
    print(f"summarized {len(data)} Cachegrind profiles")


if __name__ == "__main__":
    main()
