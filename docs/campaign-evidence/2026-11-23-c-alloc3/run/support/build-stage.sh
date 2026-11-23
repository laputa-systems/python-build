#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
arm=${1:?usage: build-stage.sh parent|cand}
case "$arm" in
    parent) repo=$root/wt-calloc3-parent ;;
    cand) repo=$root/wt-calloc3-cand ;;
    *) printf 'unknown arm: %s\n' "$arm" >&2; exit 2 ;;
esac

cd "$repo"
python3 rust-cpython/perf.py setup-worktree
python3 /home/ubuntu/repos/python-build/wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run/support/seed-cargo-bin.py "$arm"
python3 rust-cpython/perf.py build \
    --name "perf-calloc3-$arm" --allocator musl --jit --serc
