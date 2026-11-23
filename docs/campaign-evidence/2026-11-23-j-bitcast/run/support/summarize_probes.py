import json
import statistics
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry")
ARMS = ("base", "mi", "je")
MODES = ("pyc", "source", "source-x20")


def percent(value, base):
    return 100 * (value / base - 1)


def residency():
    records = {}
    lines = [
        "A4 process Private_Dirty (KiB): six observations per mode; all raw values retained in a4/*.json.",
        "Percentages are relative to the base-stage median for the same mode.",
    ]
    for arm in ARMS:
        report = json.loads((ROOT / f"a4/{arm}.json").read_text())
        records[arm] = report
        lines.append(f"{arm}: all_valid={report['all_valid']}")
        for mode in MODES:
            values = [
                sample["rollup:Private_Dirty"]
                for sample in report["modes"][mode]["samples_kib"]
            ]
            median = report["modes"][mode]["median_kib"]["rollup:Private_Dirty"]
            if median != statistics.median(values):
                raise SystemExit(f"median mismatch in A4 {arm}/{mode}")
            lines.append(f"{arm} {mode}: raw={values} median={median:g} KiB")
    for mode in MODES:
        base = records["base"]["modes"][mode]["median_kib"]["rollup:Private_Dirty"]
        for arm in ("mi", "je"):
            value = records[arm]["modes"][mode]["median_kib"]["rollup:Private_Dirty"]
            lines.append(f"{arm} vs base {mode}: {percent(value, base):+.6f}%")
    (ROOT / "a4-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def peak_rss():
    values = {}
    lines = [
        "A5 peak RSS in KiB from /usr/bin/time -f %M; three corpus runs per stage.",
        "Percentages are relative to the base-stage median.",
    ]
    for arm in ARMS:
        runs = [
            int((ROOT / f"a5/{arm}-corpus-{run}.time").read_text().strip())
            for run in (1, 2, 3)
        ]
        median = statistics.median(runs)
        values[arm] = (runs, median)
        lines.append(f"{arm}: raw={runs} median={median:g} KiB")
    for arm in ("mi", "je"):
        lines.append(
            f"{arm} vs base: {percent(values[arm][1], values['base'][1]):+.6f}%"
        )
    (ROOT / "a5-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    residency()
    peak_rss()
    print("wrote a4-summary.txt and a5-summary.txt")
