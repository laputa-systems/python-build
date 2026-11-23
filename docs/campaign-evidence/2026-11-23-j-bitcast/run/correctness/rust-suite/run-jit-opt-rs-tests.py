#!/usr/bin/env python3
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build")
REBASE = ROOT / "wt-rebase"
LANE = REBASE / "rust-cpython"
OUT = ROOT / (
    "wt-jbitcast1-run/docs/campaign-evidence/2026-11-23-j-bitcast/"
    "run/correctness/rust-suite"
)
sys.path.insert(0, str(REBASE))
sys.path.insert(0, str(LANE))
import build as lane_build
import perf

os.environ["RUSTUP_HOME"] = str(LANE / ".cache/rustup-musl")
toolchain, target = lane_build._toolchain()
source_hash = "cpython-e891a9117985888df3e5d48145cf07ddda098335"
candidate_lane = ROOT / "wt-jbitcast1/rust-cpython"
candidate_stage_dir = candidate_lane / "work/perf/perf-jbitcast1-cand"
candidate_source = candidate_stage_dir / "source" / source_hash
candidate_report_path = candidate_lane / "results/perf-jbitcast1-cand.json"
paths = dict(perf._paths("perf-rb2-h-serc"))
paths["build"] = candidate_stage_dir / "build"
paths["stage"] = candidate_lane / "stage-perf-jbitcast1-cand"
paths["source_parent"] = candidate_source.parent
report = perf._read_report(candidate_report_path)
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
if env.get("CARGO_NET_OFFLINE") != "true":
    raise RuntimeError("lane environment is not offline")

command = [
    str(lane_build.CARGO_HOME / "bin" / "cargo"),
    *lane_build.LANE_CARGO_SOURCE_ARGS,
    "test",
    "--manifest-path",
    str(candidate_source / "Cargo.toml"),
    "-p",
    "_jit_opt_rs",
    "--locked",
    "--offline",
]
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "candidate.command.txt").write_text(
    "\n".join(
        [
            f"cwd={candidate_source}",
            f"command={shlex.join(command)}",
            *(
                f"{key}={env.get(key, '')}"
                for key in (
                    "CARGO_HOME",
                    "RUSTUP_HOME",
                    "RUSTUP_TOOLCHAIN",
                    "CARGO_TARGET_DIR",
                    "CARGO_BUILD_JOBS",
                    "CARGO_NET_OFFLINE",
                    "LLVM_TARGET",
                    "HOST_CC",
                    "BINDGEN_EXTRA_CLANG_ARGS",
                )
            ),
            "registry_forwarder=lane_build._official_index_forwarder",
        ]
    )
    + "\n"
)
with lane_build._official_index_forwarder(), (OUT / "candidate.log").open("w") as log:
    result = subprocess.run(
        command,
        cwd=candidate_source,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=False,
    )
(OUT / "candidate.exit").write_text(f"{result.returncode}\n")
print(f"candidate _jit_opt_rs exit={result.returncode} log={OUT / 'candidate.log'}")
raise SystemExit(result.returncode)
