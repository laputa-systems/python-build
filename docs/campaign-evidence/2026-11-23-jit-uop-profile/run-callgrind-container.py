#!/usr/bin/env python3
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build")
STAGE_REPO = ROOT / "wt-allocretry"
RUN_REPO = ROOT / "wt-jitnet"
RUN = ROOT / "wt-integ/docs/campaign-evidence/2026-11-23-jit-uop-profile"
INPUTS = Path("/home/ubuntu/exp-calloc3")
EXTERNAL = Path("/home/ubuntu/exp-jituop")
INNER = RUN / "run-callgrind.sh"

stage_lock = json.loads((STAGE_REPO / "rust-cpython/linux-musl-toolchain.lock.json").read_text())
run_lock = json.loads((RUN_REPO / "rust-cpython/linux-musl-toolchain.lock.json").read_text())
debug = run_lock["measurement"]["debug"]["musl-dbg"]
debug_apk = INPUTS / "inputs" / f'musl-dbg-{debug["version"]}.apk'
payload = debug_apk.read_bytes()
if len(payload) != debug["size"] or hashlib.sha256(payload).hexdigest() != debug["sha256"]:
    raise SystemExit("musl-dbg APK differs from the measurement lock")
(RUN / "measurement-debug-input.txt").write_text(
    f'package=musl-dbg={debug["version"]}\nurl={debug["url"]}\n'
    f'sha256={debug["sha256"]}\nsize={debug["size"]}\n'
    f'role={debug["role"]}\nstage_identity=excluded\nsource={debug_apk}\n'
)

lane = STAGE_REPO / "rust-cpython"
cache_check = subprocess.run(
    [sys.executable, str(lane / "tools/musl-run"), "--verify-cache"],
    cwd=STAGE_REPO, capture_output=True, text=True, check=False,
)
(RUN / "cache-verification.txt").write_text(cache_check.stdout + cache_check.stderr)
if cache_check.returncode:
    raise SystemExit("locked APK cache verification failed; see cache-verification.txt")

sys.path.insert(0, str(RUN_REPO / "rust-cpython"))
from lane_cache import cache_root, mount_root, musl_llvm_prepared_prefix

base_lock = json.loads((STAGE_REPO / "bootstrap.lock.json").read_text())["linux_toolchain"]
base_cache = STAGE_REPO / ".cache/bootstrap/apk/amd64"
lane_apk_cache = STAGE_REPO / stage_lock["lane_apk_cache"]
base_apks = [base_cache / f'{item["sha256"]}.apk' for item in base_lock["apk_closure"]["x86_64"]]
lane_apks = [lane_apk_cache / f'{item["name"]}-{item["version"]}.apk' for item in stage_lock["lane_packages"]]
for path in [*base_apks, *lane_apks]:
    if not path.is_file():
        raise SystemExit(f"missing locked measurement APK: {path}")

llvm = stage_lock["llvm"]
llvm_prefix, llvm_key = musl_llvm_prepared_prefix(cache_root(ROOT), stage_lock["target"], llvm["sha256"])
if not llvm_prefix.is_dir():
    raise SystemExit(f"missing prepared LLVM prefix: {llvm_prefix} ({llvm_key})")

rustup_home = lane / stage_lock["rust"]["cache"]
cargo_home = lane / ".cargo-home"
container_script = f"""set -eu
apk --no-network --repositories-file /dev/null add -- {shlex.join(map(str, base_apks))}
apk --no-network --repositories-file /dev/null add -- {shlex.join(map(str, lane_apks))}
python3 {lane / 'tools/musl-run'} --verify-installed
apk --no-network --repositories-file /dev/null --allow-untrusted add -- {shlex.quote(str(debug_apk))}
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
exec python3 {lane / 'tools/musl-run'} --as-user "$HOST_UID" "$HOST_GID" command sh -x {INNER}
"""
mount = mount_root(STAGE_REPO)
command = [
    "docker", "run", "--rm", "--platform", "linux/amd64",
    "--mount", f"type=bind,source={mount},target={mount}",
    "--mount", f"type=bind,source={RUN},target={RUN}",
    "--mount", f"type=bind,source={INPUTS},target={INPUTS}",
    "--mount", f"type=bind,source={EXTERNAL},target={EXTERNAL}",
    "--workdir", str(STAGE_REPO), "--network", "none",
    "--env", f"HOST_UID={__import__('os').getuid()}",
    "--env", f"HOST_GID={__import__('os').getgid()}",
    "alpine@" + stage_lock["production"]["image_digest"],
    "sh", "-euc", container_script, "jit-net-container",
]
(RUN / "container-command.json").write_text(json.dumps(command, indent=2) + "\n")
with (RUN / "container.stdout").open("w") as stdout, (RUN / "container.stderr").open("w") as stderr:
    result = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
print(f"callgrind container exit={result.returncode}")
raise SystemExit(result.returncode)
