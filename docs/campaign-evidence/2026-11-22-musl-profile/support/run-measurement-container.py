#!/usr/bin/env python3
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

root = Path("/home/ubuntu/repos/python-build")
stage_repo = root / "wt-allocretry"
profile_repo = root / "wt-musl-profile"
evidence = root / "wt-integ/docs/campaign-evidence/2026-11-22-musl-profile"
external = Path("/home/ubuntu/exp-musl-profile")
profile_name = sys.argv[1] if len(sys.argv) > 1 else "cachegrind"
if profile_name not in {"cachegrind", "callgrind"}:
    raise SystemExit("usage: run-measurement-container.py [cachegrind|callgrind]")
lane = stage_repo / "rust-cpython"
lock = json.loads((profile_repo / "rust-cpython/linux-musl-toolchain.lock.json").read_text())
bootstrap = json.loads((stage_repo / "bootstrap.lock.json").read_text())["linux_toolchain"]
debug = lock["measurement"]["debug"]["musl-dbg"]
debug_apk = external / "inputs" / f'musl-dbg-{debug["version"]}.apk'
payload = debug_apk.read_bytes()
if len(payload) != debug["size"] or hashlib.sha256(payload).hexdigest() != debug["sha256"]:
    raise SystemExit("musl-dbg APK differs from the measurement lock")
cache_check = subprocess.run(
    [sys.executable, str(lane / "tools/musl-run"), "--verify-cache"],
    cwd=stage_repo,
    capture_output=True,
    text=True,
    check=False,
)
(evidence / f"{profile_name}-apk-cache-verification.txt").write_text(
    cache_check.stdout + cache_check.stderr
)
if cache_check.returncode:
    raise SystemExit(
        f"locked APK cache verification failed; see {profile_name}-apk-cache-verification.txt"
    )

sys.path.insert(0, str(profile_repo / "rust-cpython"))
from lane_cache import cache_root, mount_root, musl_llvm_prepared_prefix

base_cache = stage_repo / ".cache/bootstrap/apk/amd64"
lane_cache = stage_repo / lock["lane_apk_cache"]
base_apks = [
    base_cache / f'{item["sha256"]}.apk'
    for item in bootstrap["apk_closure"]["x86_64"]
]
lane_apks = [
    lane_cache / f'{item["name"]}-{item["version"]}.apk'
    for item in lock["lane_packages"]
]
for path in [*base_apks, *lane_apks]:
    if not path.is_file():
        raise SystemExit(f"missing locked measurement APK: {path}")

llvm = lock["llvm"]
llvm_prefix, llvm_key = musl_llvm_prepared_prefix(
    cache_root(root), lock["target"], llvm["sha256"]
)
if not llvm_prefix.is_dir():
    raise SystemExit(f"missing prepared LLVM prefix for symbolizer tools: {llvm_prefix} ({llvm_key})")
rustup_home = lane / lock["rust"]["cache"]
cargo_home = lane / ".cargo-home"
debug_path = str(debug_apk)
inner = evidence / f"support/run-{profile_name}-base2.sh"
container_script = f"""set -eu
apk --no-network --repositories-file /dev/null add -- {shlex.join(map(str, base_apks))}
apk --no-network --repositories-file /dev/null add -- {shlex.join(map(str, lane_apks))}
python3 {lane / 'tools/musl-run'} --verify-installed
apk --no-network --repositories-file /dev/null --allow-untrusted add -- {shlex.quote(debug_path)}
export HOME={lane / '.home'}
export CARGO_HOME={cargo_home}
export RUSTUP_HOME={rustup_home}
export RUSTUP_TOOLCHAIN={lock['rust']['channel']}
export PATH={cargo_home / 'bin'}:{llvm_prefix / 'bin'}:/usr/bin:/bin
export LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export SOURCE_DATE_EPOCH=1704067200
export MUSL_LANE_IMAGE_DIGEST={lock['production']['image_digest']}
export RUST_CPYTHON_DOCKER_OFFLINE=1
exec python3 {lane / 'tools/musl-run'} --as-user "$HOST_UID" "$HOST_GID" command sh {inner}
"""
command = [
    "docker", "run", "--rm", "--platform", "linux/amd64",
    "--mount", f"type=bind,source={mount_root(stage_repo)},target={mount_root(stage_repo)}",
    "--mount", f"type=bind,source={external},target={external}",
    "--workdir", str(stage_repo), "--network", "none",
    "--env", f"HOST_UID={os.getuid()}", "--env", f"HOST_GID={os.getgid()}",
    "alpine@" + lock["production"]["image_digest"],
    "sh", "-euc", container_script, "musl-profile-container",
]
(evidence / f"{profile_name}-container-command.json").write_text(
    json.dumps(command, indent=2) + "\n"
)
result = subprocess.run(command, check=False)
raise SystemExit(result.returncode)
