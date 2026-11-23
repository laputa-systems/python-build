#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
stage=$root/wt-allocretry/rust-cpython/stage-perf-musl-base2
retry=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-profile/callers
cg=$out/base2-corpus.callgrind
stdout=$out/base2-corpus.stdout
stderr=$out/base2-corpus.stderr
exit_file=$out/base2-corpus.exit

mkdir -p "$out"
if LD_LIBRARY_PATH=$stage/lib valgrind --tool=callgrind \
    --dump-instr=no --smc-check=all --separate-callers=2 \
    --callgrind-out-file=$cg \
    $stage/bin/python3.16 -S -W ignore \
    $retry/support/compile_corpus.py compile $stage/lib/python3.16 \
    > "$stdout" 2> "$stderr"; then
    rc=0
else
    rc=$?
fi
printf '%s\n' "$rc" > "$exit_file"
if [ "$rc" -ne 0 ]; then
    printf 'Callgrind failed: compile corpus (exit %s)\n' "$rc" >&2
    exit "$rc"
fi
xz -T0 -9 "$cg"
