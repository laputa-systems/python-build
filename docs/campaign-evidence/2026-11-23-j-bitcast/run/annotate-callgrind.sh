#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
run=$root/wt-jitnet/docs/campaign-evidence/2026-11-23-jit-net-profile
external=/home/ubuntu/exp-jitnet
annotator=$run/support/callgrind_annotate-3.18.1

for name in \
    parent-nbody-kernel \
    parent-nbody-setup \
    parent-scimark_sparse_mat_mult-kernel \
    parent-scimark_sparse_mat_mult-setup
do
    compressed=$external/profiles/$name.callgrind.xz
    raw=$external/profiles/$name.annotate-input
    xz -dc "$compressed" > "$raw"
    if "$annotator" --auto=no --show=Ir --threshold=100 --inclusive=yes \
        --tree=caller "$raw" > "$run/callgrind/$name.caller-tree.txt" \
        2> "$run/callgrind/$name.annotate.stderr"; then
        rc=0
    else
        rc=$?
    fi
    printf '%s\n' "$rc" > "$run/callgrind/$name.annotate.exit"
    rm "$raw"
done
