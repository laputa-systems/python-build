#!/usr/bin/env python3
import ast
import re
from pathlib import Path


ROOT = Path("/home/ubuntu/repos/python-build/wt-jbitcast1-run/docs/campaign-evidence/2026-11-23-j-bitcast/run")
C3 = Path("/home/ubuntu/repos/python-build/wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run")
ANNOTATE = re.compile(r"^\s*([\d,]+) \(100\.0%\)\s+PROGRAM TOTALS$")
WORKLOADS = (
    "corpus",
    "scimark_fft",
    "nbody",
    "scimark_sparse_mat_mult",
    "setup-nbody",
    "setup-scimark_fft",
    "setup-scimark_sparse_mat_mult",
)
KERNELS = ("nbody", "scimark_fft", "scimark_sparse_mat_mult")


def parse_c3_parent():
    records = {}
    for line in (C3 / "ir-totals.txt").read_text().splitlines():
        if not line.startswith("parent-"):
            continue
        stem, rest = line.split(" total_Ir=", 1)
        total = int(rest.split()[0].replace(",", ""))
        stdout = line.split(" stdout=", 1)[1]
        records[stem] = (total, stdout)
    return records


def parse_candidate():
    records = {}
    directory = ROOT / "cachegrind"
    for annotation in directory.glob("cand-*.annotate.txt"):
        stem = annotation.name.removesuffix(".annotate.txt")
        total = None
        for line in annotation.read_text(errors="replace").splitlines():
            match = ANNOTATE.match(line)
            if match:
                total = int(match.group(1).replace(",", ""))
                break
        if total is None:
            raise ValueError(f"missing PROGRAM TOTALS in {annotation}")
        stderr = (directory / f"{stem}.stderr").read_text(errors="replace")
        refs_match = re.search(r"I refs:\s*([\d,]+)", stderr)
        stdout = (directory / f"{stem}.stdout").read_text(errors="replace").strip()
        exit_code = (directory / f"{stem}.exit").read_text().strip()
        if exit_code != "0" or not refs_match or int(refs_match.group(1).replace(",", "")) != total:
            raise ValueError(f"Cachegrind total/exit mismatch for {stem}")
        records[stem] = (total, stdout)
    return records


def active_pair(records, arm, workload):
    retry = (f"{arm}-{workload}-retry1-1", f"{arm}-{workload}-retry1-2")
    initial = (f"{arm}-{workload}-1", f"{arm}-{workload}-2")
    pair = retry if all(key in records for key in retry) else initial
    if not all(key in records for key in pair):
        raise KeyError(f"missing pair for {arm} {workload}: {pair}")
    return pair


def pair_delta(first, second):
    return 100 * abs(first - second) / ((first + second) / 2)


def main():
    records = parse_c3_parent()
    records.update(parse_candidate())
    lines = [
        "Cachegrind total Ir from cg_annotate PROGRAM TOTALS; stderr I refs were cross-checked.",
        "Each entry includes the raw workload stdout.",
    ]
    for arm in ("parent", "cand"):
        for workload in WORKLOADS:
            retry_stem = f"{arm}-{workload}-retry1-1"
            stems = (
                [retry_stem, f"{arm}-{workload}-retry1-2"]
                if retry_stem in records
                else [f"{arm}-{workload}-1", f"{arm}-{workload}-2"]
            )
            for index, stem in enumerate(stems, 1):
                total, stdout = records[stem]
                lines.append(f"{stem} total_Ir={total:,} stdout={stdout}")
    (ROOT / "ir-totals.txt").write_text("\n".join(lines) + "\n")

    repeats = [
        "Symmetric relative difference = abs(run1-run2) / mean(run1,run2) * 100; limit 0.1%.",
        "Fresh pairs, if present, replace initial pairs for mean calculations.",
    ]
    for arm in ("parent", "cand"):
        for workload in WORKLOADS:
            initial = (f"{arm}-{workload}-1", f"{arm}-{workload}-2")
            if all(key in records for key in initial):
                delta = pair_delta(records[initial[0]][0], records[initial[1]][0])
                repeats.append(
                    f"initial {arm} {workload}: {records[initial[0]][0]:,} / "
                    f"{records[initial[1]][0]:,} delta={delta:.6f}% "
                    f"{'within' if delta <= 0.1 else 'miss'}"
                )
            retry = (f"{arm}-{workload}-retry1-1", f"{arm}-{workload}-retry1-2")
            if all(key in records for key in retry):
                delta = pair_delta(records[retry[0]][0], records[retry[1]][0])
                repeats.append(
                    f"retry1 {arm} {workload}: {records[retry[0]][0]:,} / "
                    f"{records[retry[1]][0]:,} delta={delta:.6f}% "
                    f"{'within' if delta <= 0.1 else 'non-repeatable'}"
                )
    (ROOT / "repeatability.txt").write_text("\n".join(repeats) + "\n")

    net = [
        "Means use the initial pair unless a fresh retry pair was scheduled.",
        "net = whole-kernel mean - matching setup-only mean.",
        "parent-to-candidate change = 100 * (candidate / parent - 1).",
    ]
    for kernel in KERNELS:
        values = {}
        setup = "setup-nbody" if kernel == "nbody" else "setup-scimark_fft" if kernel == "scimark_fft" else "setup-scimark_sparse_mat_mult"
        for arm in ("parent", "cand"):
            whole_pair = active_pair(records, arm, kernel)
            setup_pair = active_pair(records, arm, setup)
            whole_mean = sum(records[key][0] for key in whole_pair) / 2
            setup_mean = sum(records[key][0] for key in setup_pair) / 2
            values[arm] = (whole_mean, setup_mean, whole_mean - setup_mean)
            net.append(
                f"{kernel} {arm}: whole_mean={whole_mean:.1f} "
                f"setup_mean={setup_mean:.1f} net={whole_mean - setup_mean:.1f}"
            )
        for label, index in (("whole", 0), ("setup", 1), ("net", 2)):
            before, after = values["parent"][index], values["cand"][index]
            net.append(
                f"{kernel} parent_to_cand {label}: parent={before:.1f} "
                f"cand={after:.1f} change={100 * (after / before - 1):+.6f}%"
            )
    (ROOT / "net-kernel.txt").write_text("\n".join(net) + "\n")

    operations = ["Kernel operation counts and digests per whole-kernel run."]
    for arm in ("parent", "cand"):
        for kernel in KERNELS:
            for key in active_pair(records, arm, kernel):
                raw = records[key][1]
                parsed = ast.literal_eval(raw)
                operations.append(
                    f"{arm} {kernel} {key}: operations={parsed['operation_count']} "
                    f"digest={parsed['digest']}"
                )
    (ROOT / "operation-counts-digests.txt").write_text("\n".join(operations) + "\n")


if __name__ == "__main__":
    main()
