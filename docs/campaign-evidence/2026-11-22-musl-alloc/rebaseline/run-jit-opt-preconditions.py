#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build")
LANE = ROOT / "wt-rebase/rust-cpython"
OUT = ROOT / "wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline/correctness-followup"
sys.path.insert(0, str(ROOT / "wt-rebase"))
sys.path.insert(0, str(LANE))
import build as lane_build
import perf

os.environ["RUSTUP_HOME"] = str(LANE / ".cache/rustup-musl")
toolchain, target = lane_build._toolchain()
source_hash = "cpython-e891a9117985888df3e5d48145cf07ddda098335"
stages = (
    ("perf-rb2-jes2-parent-serc", "jes2-parent"),
    ("perf-rb2-h-serc", "h2"),
    ("perf-rb2-jes3-candidate-serc", "jes3-candidate"),
)
statuses = []

for name, label in stages:
    paths = perf._paths(name)
    source = paths["source_parent"] / source_hash
    report = perf._read_report(paths["report"])
    env = perf._build_environment(
        toolchain,
        target,
        paths,
        workers=4,
        sandbox=lane_build._sealed_sandbox(),
        jit=bool(report.get("jit")),
        serc=bool(report.get("serc")),
    )
    env["CARGO_TARGET_DIR"] = str(paths["build"] / "target")
    env["CARGO_BUILD_JOBS"] = "4"
    tests = ["replay_captured_sparse_and_fft_traces"]
    if label == "jes3-candidate":
        tests.append("captured_type_matrix_and_sparse_uop_mutations")
    for test_name in tests:
        log_path = OUT / f"{label}-{test_name}.log"
        command = [
            str(lane_build.CARGO_HOME / "bin" / "cargo"),
            *lane_build.LANE_CARGO_SOURCE_ARGS,
            "test",
            "--manifest-path",
            str(source / "Cargo.toml"),
            "-p",
            "_jit_opt_rs",
            "--test",
            "jes2_replay",
            test_name,
            "--locked",
            "--offline",
            "--",
            "--nocapture",
        ]
        with lane_build._official_index_forwarder(), log_path.open("w") as log:
            completed = subprocess.run(
                command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT,
                check=False,
            )
        print(f"{label} {test_name} exit={completed.returncode} log={log_path}")
        statuses.append(completed.returncode)

if any(statuses):
    raise SystemExit(1)
