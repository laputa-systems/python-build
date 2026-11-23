#!/usr/bin/env python3
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build")
STAGE_REPO = ROOT / "wt-allocretry"
RUN = ROOT / "wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run"
EXTERNAL = Path("/home/ubuntu/exp-calloc3")
mode = sys.argv[1] if len(sys.argv) > 1 else ""
MODES = {
    "build-parent": (RUN / "support/build-stage.sh", ["parent"]),
    "build-cand": (RUN / "support/build-stage.sh", ["cand"]),
    "cachegrind": (RUN / "run-cachegrind.sh", []),
    "probes": (RUN / "run-probes.sh", []),
    "correctness": (RUN / "run-correctness.sh", []),
}
if mode not in MODES:
    raise SystemExit(f"usage: run-container.py [{ '|'.join(MODES) }]")
inner, inner_args = MODES[mode]
if not inner.is_file():
    raise SystemExit(f"missing container command: {inner}")

lane = STAGE_REPO / "rust-cpython"
stage_lock = json.loads((lane / "linux-musl-toolchain.lock.json").read_text())
run_lock = json.loads(
    (ROOT / "wt-calloc3-run/rust-cpython/linux-musl-toolchain.lock.json").read_text()
)
bootstrap = json.loads((STAGE_REPO / "bootstrap.lock.json").read_text())["linux_toolchain"]
debug = run_lock["measurement"]["debug"]["musl-dbg"]
debug_apk = EXTERNAL / "inputs" / f'musl-dbg-{debug["version"]}.apk'
payload = debug_apk.read_bytes()
if len(payload) != debug["size"] or hashlib.sha256(payload).hexdigest() != debug["sha256"]:
    raise SystemExit("musl-dbg APK differs from the measurement lock")
(RUN / "measurement-debug-input.txt").write_text(
    f'package=musl-dbg={debug["version"]}\n'
    f'url={debug["url"]}\nsha256={debug["sha256"]}\nsize={debug["size"]}\n'
    f'role={debug["role"]}\nstage_identity=excluded\nsource={debug_apk}\n'
)

cache_check = subprocess.run(
    [sys.executable, str(lane / "tools/musl-run"), "--verify-cache"],
    cwd=STAGE_REPO,
    capture_output=True,
    text=True,
    check=False,
)
(RUN / "support" / f"{mode}-apk-cache-verification.txt").write_text(
    cache_check.stdout + cache_check.stderr
)
if cache_check.returncode:
    raise SystemExit(
        f"locked APK cache verification failed; see support/{mode}-apk-cache-verification.txt"
    )

sys.path.insert(0, str(ROOT / "wt-calloc3-run/rust-cpython"))
from lane_cache import (
    cache_root,
    mount_root,
    musl_llvm_prepared_prefix,
    share_worktree_caches,
)

if mode in ("build-parent", "build-cand"):
    arm = mode.removeprefix("build-")
    build_repo = ROOT / f"wt-calloc3-{arm}"
    build_lock = json.loads(
        (build_repo / "rust-cpython/linux-musl-toolchain.lock.json").read_text()
    )
    relevant_lock_keys = ("target", "production", "rust", "llvm", "lane_apk_cache", "lane_packages")
    if any(build_lock.get(key) != stage_lock.get(key) for key in relevant_lock_keys):
        raise SystemExit(f"{arm} build lock differs from the cached lane toolchain lock")
    build_bootstrap = json.loads((build_repo / "bootstrap.lock.json").read_text())
    if build_bootstrap.get("linux_toolchain") != bootstrap:
        raise SystemExit(f"{arm} bootstrap lock differs from the cached APK lock")

    rustup_source = lane / stage_lock["rust"]["cache"]
    primary_rustup = ROOT / "rust-cpython/.cache/rustup-musl"
    primary_rustup.parent.mkdir(parents=True, exist_ok=True)
    if primary_rustup.is_symlink():
        if primary_rustup.resolve() != rustup_source.resolve():
            raise SystemExit(f"primary Rust cache points elsewhere: {primary_rustup}")
    elif primary_rustup.exists():
        raise SystemExit(f"primary Rust cache path is occupied: {primary_rustup}")
    else:
        primary_rustup.symlink_to(rustup_source.resolve())

    owner, links = share_worktree_caches(build_repo, ROOT, musl=True)
    cache_note = [
        f"primary={owner}",
        f"reused_rustup_cache={rustup_source.resolve()}",
        *[f"{target} -> {source}" for target, source in links],
    ]
    (RUN / "support" / f"{mode}-cache-sharing.txt").write_text(
        "\n".join(cache_note) + "\n", encoding="utf-8"
    )

base_cache = STAGE_REPO / ".cache/bootstrap/apk/amd64"
lane_apk_cache = STAGE_REPO / stage_lock["lane_apk_cache"]
base_apks = [
    base_cache / f'{item["sha256"]}.apk'
    for item in bootstrap["apk_closure"]["x86_64"]
]
lane_apks = [
    lane_apk_cache / f'{item["name"]}-{item["version"]}.apk'
    for item in stage_lock["lane_packages"]
]
for path in [*base_apks, *lane_apks]:
    if not path.is_file():
        raise SystemExit(f"missing locked measurement APK: {path}")

llvm = stage_lock["llvm"]
llvm_prefix, llvm_key = musl_llvm_prepared_prefix(
    cache_root(ROOT), stage_lock["target"], llvm["sha256"]
)
if not llvm_prefix.is_dir():
    raise SystemExit(f"missing prepared LLVM prefix: {llvm_prefix} ({llvm_key})")
rustup_home = lane / stage_lock["rust"]["cache"]
cargo_home = lane / ".cargo-home"
container_script = f"""set -eu
apk --no-network --repositories-file /dev/null add -- {shlex.join(map(str, base_apks))}
apk --no-network --repositories-file /dev/null add -- {shlex.join(map(str, lane_apks))}
python3 {lane / 'tools/musl-run'} --verify-installed
if [ {shlex.quote(mode)} = cachegrind ]; then
    apk --no-network --repositories-file /dev/null --allow-untrusted add -- {shlex.quote(str(debug_apk))}
fi
export HOME={lane / '.home'}
export CARGO_HOME={cargo_home}
export RUSTUP_HOME={rustup_home}
export RUSTUP_TOOLCHAIN={stage_lock['rust']['channel']}
export PATH={cargo_home / 'bin'}:{llvm_prefix / 'bin'}:/usr/bin:/bin
export LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export SOURCE_DATE_EPOCH=1704067200
export MUSL_LANE_IMAGE_DIGEST={stage_lock['production']['image_digest']}
export RUST_CPYTHON_DOCKER_OFFLINE=1
exec python3 {lane / 'tools/musl-run'} --as-user "$HOST_UID" "$HOST_GID" command sh {inner} {shlex.join(inner_args)}
"""
mount = mount_root(STAGE_REPO)
command = [
    "docker", "run", "--rm", "--platform", "linux/amd64",
    "--mount", f"type=bind,source={mount},target={mount}",
    "--mount", f"type=bind,source={EXTERNAL},target={EXTERNAL}",
    "--workdir", str(STAGE_REPO), "--network", "none",
    "--env", f"HOST_UID={os.getuid()}", "--env", f"HOST_GID={os.getgid()}",
    "alpine@" + stage_lock["production"]["image_digest"],
    "sh", "-euc", container_script, "calloc3-container",
]
(RUN / "support" / f"{mode}-container-command.json").write_text(
    json.dumps(command, indent=2) + "\n"
)
with (RUN / "support" / f"{mode}-container.stdout").open("w") as stdout, \
        (RUN / "support" / f"{mode}-container.stderr").open("w") as stderr:
    result = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
print(f"{mode} container exit={result.returncode}")
raise SystemExit(result.returncode)
