import re
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry")
CG = ROOT / "cachegrind"
ARMS = ("base", "mi", "je")
WORKLOADS = ("corpus", "scimark_fft", "nbody", "scimark_sparse_mat_mult")
PUBLIC = {"malloc", "free", "calloc", "realloc"}
PATTERNS = {
    "base": ("^(malloc|free|calloc|realloc)$", "^__libc_malloc_impl$",
             "^alloc_.*$", "^enframe$"),
    "mi": ("^(malloc|free|calloc|realloc)$", "^mi_.*$", "^_mi_.*$"),
    "je": ("^(malloc|free|calloc|realloc)$", "^je_.*$"),
}


def run_files():
    files = {}
    for path in sorted(CG.glob("*.annotate.txt")):
        stem = path.name.removesuffix(".annotate.txt")
        parts = stem.split("-")
        arm = parts[0]
        if arm not in ARMS:
            continue
        if len(parts) >= 4 and parts[-2].startswith("retry"):
            group = parts[-2]
            run = int(parts[-1])
            workload = "-".join(parts[1:-2])
        else:
            group = "initial"
            run = int(parts[-1])
            workload = "-".join(parts[1:-1])
        files[(arm, workload, group, run)] = path
    return files


def parse_annotation(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    total_match = re.search(r"([\d,]+) \(100\.0%\)  PROGRAM TOTALS", text)
    if total_match is None:
        raise ValueError(f"missing PROGRAM TOTALS in {path}")
    rows = []
    for line in text.splitlines():
        match = re.match(r"\s*([\d,]+)\s+\([^)]*\)\s+(.+)", line)
        if match is None or "PROGRAM TOTALS" in line:
            continue
        location = match.group(2)
        if ":" not in location:
            continue
        source, function = location.split(":", 1)
        rows.append((int(match.group(1).replace(",", "")), source, function))
    return int(total_match.group(1).replace(",", "")), rows


def allocator_matches(arm, rows):
    matched = []
    for count, source, function in rows:
        public = function in PUBLIC
        if arm == "base":
            internal = (function == "__libc_malloc_impl" or function.startswith("alloc_")
                        or function == "enframe")
        elif arm == "mi":
            internal = function.startswith(("mi_", "_mi_"))
        else:
            internal = function.startswith("je_")
        if public or internal:
            matched.append((count, source, function))
    return matched


def load(files):
    data = {}
    for key, path in files.items():
        total, rows = parse_annotation(path)
        matched = allocator_matches(key[0], rows)
        stem = path.name.removesuffix(".annotate.txt")
        stdout = (CG / f"{stem}.stdout").read_text().strip()
        exit_code = (CG / f"{stem}.exit").read_text().strip()
        stderr = (CG / f"{stem}.stderr").read_text(errors="replace")
        ir_refs = re.search(r"I refs:\s*([\d,]+)", stderr)
        data[key] = {
            "path": path,
            "total": total,
            "family": sum(row[0] for row in matched),
            "matched": matched,
            "stdout": stdout,
            "exit": exit_code,
            "stderr_ir": (int(ir_refs.group(1).replace(",", "")) if ir_refs else None),
        }
    return data


def pct_delta(value, base):
    return 100 * (value / base - 1)


def fmt(value):
    return f"{value:,}"


def emit(files, data):
    totals = []
    families = []
    repeatability = []
    comparisons = []
    keys = sorted(data, key=lambda k: (WORKLOADS.index(k[1]), k[2], ARMS.index(k[0]), k[3]))

    for key in keys:
        arm, workload, group, run = key
        item = data[key]
        stem = item["path"].name.removesuffix(".annotate.txt")
        totals.append(f"{stem} {fmt(item['total'])} | {item['stdout']}")
        share = 100 * item["family"] / item["total"]
        families.append(f"{stem} {fmt(item['family'])} {share:.6f}%")
        if item["exit"] != "0" or item["stderr_ir"] != item["total"]:
            raise SystemExit(f"Cachegrind evidence mismatch: {stem}, exit={item['exit']}, "
                             f"cg_annotate={item['total']}, I_refs={item['stderr_ir']}")

    groups = sorted({key[2] for key in data}, key=lambda value: (value != "initial", value))
    for group in groups:
        for workload in WORKLOADS:
            workload_arms = {arm for arm in ARMS if (arm, workload, group, 1) in data}
            if not workload_arms:
                continue
            for arm in sorted(workload_arms, key=ARMS.index):
                first = data[(arm, workload, group, 1)]["total"]
                second = data[(arm, workload, group, 2)]["total"]
                delta = 100 * abs(first - second) / ((first + second) / 2)
                repeatability.append(
                    f"{group} {workload} {arm} {fmt(first)} {fmt(second)} "
                    f"{delta:.6f}% {'within' if delta <= 0.1 else 'miss'}"
                )
            if "base" not in workload_arms:
                continue
            base = data[("base", workload, group, 1)]
            for arm in ("mi", "je"):
                if arm not in workload_arms:
                    continue
                candidate = data[(arm, workload, group, 1)]
                total_pct = pct_delta(candidate["total"], base["total"])
                family_pct = pct_delta(candidate["family"], base["family"]) if base["family"] else None
                comparisons.append(
                    f"{group} {workload} total_ir base={fmt(base['total'])} "
                    f"{arm}={fmt(candidate['total'])} delta={total_pct:+.6f}%"
                )
                comparisons.append(
                    f"{group} {workload} allocator_self_ir base={fmt(base['family'])} "
                    f"{arm}={fmt(candidate['family'])} "
                    f"delta={(f'{family_pct:+.6f}%' if family_pct is not None else 'n/a')}"
                )

    (ROOT / "ir-totals.txt").write_text(
        "Cachegrind total Ir and raw workload stdout\n"
        "Total Ir is cg_annotate PROGRAM TOTALS; it was cross-checked against Valgrind I refs.\n"
        + "\n".join(totals) + "\n", encoding="utf-8")
    (ROOT / "allocator-self-ir.txt").write_text(
        "Allocator-family self Ir by run\n"
        "Patterns: base public exact malloc/free/calloc/realloc; __libc_malloc_impl; alloc_*; enframe. "
        "mi public exact malloc/free/calloc/realloc; mi_*, _mi_*. "
        "je public exact malloc/free/calloc/realloc; je_*.\n"
        + "\n".join(families) + "\n", encoding="utf-8")
    (ROOT / "repeatability.txt").write_text(
        "Symmetric relative difference = abs(run1-run2) / mean(run1,run2) * 100.\n"
        + "\n".join(repeatability) + "\n", encoding="utf-8")
    (ROOT / "cachegrind-comparisons.txt").write_text(
        "Per-arm percentage vs base uses run 1 of the corresponding run group.\n"
        + "\n".join(comparisons) + "\n", encoding="utf-8")

    matched_lines = []
    for key in keys:
        arm, workload, group, run = key
        item = data[key]
        names = ", ".join(
            f"{function}={fmt(count)}" for count, _source, function in item["matched"]
        ) or "(no allocator-family symbols matched)"
        matched_lines.append(f"{arm} {workload} {group} run{run}: {names}")
    (ROOT / "allocator-matched-functions.txt").write_text(
        "Matched allocator-family functions (self Ir)\n" + "\n".join(matched_lines) + "\n",
        encoding="utf-8")

    print(f"Cachegrind profiles summarized: {len(data)}")
    print(f"repeatability records: {len(repeatability)}")
    print(f"comparison records: {len(comparisons)}")
    print("outputs: ir-totals.txt allocator-self-ir.txt repeatability.txt "
          "cachegrind-comparisons.txt allocator-matched-functions.txt")


if __name__ == "__main__":
    profile_files = run_files()
    emit(profile_files, load(profile_files))
