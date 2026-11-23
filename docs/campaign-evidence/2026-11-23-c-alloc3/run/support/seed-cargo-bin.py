import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build")
RUN = ROOT / "wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run"
arm = sys.argv[1]
if arm not in ("parent", "cand"):
    raise SystemExit(f"unknown arm: {arm}")

source = ROOT / "wt-allocretry/rust-cpython/.cargo-home/bin"
destination = ROOT / f"wt-calloc3-{arm}/rust-cpython/.cargo-home/bin"
destination.mkdir(parents=True, exist_ok=True)
lines = [
    f"Existing musl lane Cargo launchers copied after perf.py setup-worktree.",
    f"source={source}",
    f"destination={destination}",
]
for name in ("cargo", "rustc", "rustup"):
    source_file = source / name
    destination_file = destination / name
    if not source_file.is_file() or source_file.is_symlink():
        raise SystemExit(f"invalid Cargo launcher in existing lane cache: {source_file}")
    if destination_file.is_symlink():
        raise SystemExit(f"stage-local Cargo launcher is unexpectedly a symlink: {destination_file}")
    shutil.copy2(source_file, destination_file)
    digest = hashlib.sha256(destination_file.read_bytes()).hexdigest()
    lines.append(f"{name} sha256={digest}")

(RUN / "support" / f"{arm}-cargo-bin-seed.txt").write_text(
    "\n".join(lines) + "\n", encoding="utf-8"
)
print("\n".join(lines))
