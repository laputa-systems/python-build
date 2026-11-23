#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
arm=${1:?usage: build-stage.sh cand}
case "$arm" in
    cand) repo=$root/wt-jbitcast1 ;;
    *) printf 'unknown arm: %s\n' "$arm" >&2; exit 2 ;;
esac

cd "$repo"
python3 rust-cpython/perf.py setup-worktree
python3 /home/ubuntu/repos/python-build/wt-jbitcast1-run/docs/campaign-evidence/2026-11-23-j-bitcast/run/support/seed-cargo-bin.py "$arm"
python3 rust-cpython/perf.py build \
    --name "perf-jbitcast1-$arm" --allocator musl --jit --serc
