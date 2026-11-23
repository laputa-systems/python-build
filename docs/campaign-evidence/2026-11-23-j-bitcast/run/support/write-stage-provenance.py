import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build")
RUN = ROOT / "wt-jbitcast1-run/docs/campaign-evidence/2026-11-23-j-bitcast/run"
records = {}
for arm in ("parent", "cand"):
    repo = ROOT / ("wt-calloc3-parent" if arm == "parent" else "wt-jbitcast1")
    lane = repo / "rust-cpython"
    name = "perf-calloc3-parent" if arm == "parent" else "perf-jbitcast1-cand"
    stage = lane / f"stage-{name}"
    report = json.loads((lane / "results" / f"{name}.json").read_text())
    libpython = stage / "lib/libpython3.16.so.1.0"
    git_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    record = {
        "arm": arm,
        "source_sha": git_sha,
        "build_report_sha": report.get("git", {}).get("commit"),
        "overlay_dirty": report.get("git", {}).get("overlay_dirty"),
        "allocator": report.get("allocator"),
        "allocator_profile": report.get("allocator_profile"),
        "allocator_config": report.get("allocator_config"),
        "target": report.get("c_toolchain", {}).get("target"),
        "jit": report.get("jit"),
        "serc": report.get("serc"),
        "pystats": report.get("pystats", False),
        "configure_arguments": report.get("configure_arguments"),
        "build_mode": report.get("build_mode"),
        "image_digest": report.get("c_toolchain", {}).get("image_digest"),
        "libpython": str(libpython),
        "libpython_sha256": hashlib.sha256(libpython.read_bytes()).hexdigest(),
    }
    records[arm] = record

(RUN / "stage-provenance.json").write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
lines = []
for arm, record in records.items():
    lines.extend([
        f"[{arm}]",
        f"source_sha={record['source_sha']}",
        f"build_report_sha={record['build_report_sha']}",
        f"overlay_dirty={record['overlay_dirty']}",
        f"allocator={record['allocator']} profile={record['allocator_profile']} config={record['allocator_config']}",
        f"target={record['target']} jit={record['jit']} serc={record['serc']} pystats={record['pystats']}",
        f"build_mode={record['build_mode']}",
        f"image_digest={record['image_digest']}",
        f"libpython={record['libpython']}",
        f"libpython_sha256={record['libpython_sha256']}",
        f"configure_arguments={json.dumps(record['configure_arguments'])}",
        "",
    ])
(RUN / "stage-provenance.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
