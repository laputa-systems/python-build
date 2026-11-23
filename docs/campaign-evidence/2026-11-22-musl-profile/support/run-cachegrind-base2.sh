#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
stage=$root/wt-allocretry/rust-cpython/stage-perf-musl-base2
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-profile
retry=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry
annotator=$retry/support/cg_annotate-3.18.1

mkdir -p "$out/cachegrind"
{
    awk 'BEGIN { RS = "" } /^P:musl\n/ || /^P:musl-dbg\n/ { print $0 "\n" }' \
        /lib/apk/db/installed
    printf '\n'
    ls -l /lib/ld-musl-x86_64.so.1 /usr/lib/debug/lib/ld-musl-x86_64.so.1.debug
    printf '\n'
    file /lib/ld-musl-x86_64.so.1 /usr/lib/debug/lib/ld-musl-x86_64.so.1.debug
} > "$out/musl-debug-install.txt"

run_profile() {
    name=$1
    kind=$2
    workload=$3
    map_file=$4
    cg=$out/cachegrind/$name.cg
    stdout=$out/cachegrind/$name.stdout
    stderr=$out/cachegrind/$name.stderr
    exit_file=$out/cachegrind/$name.exit
    : > "$map_file"
    if [ "$kind" = corpus ]; then
        LD_LIBRARY_PATH=$stage/lib valgrind --tool=cachegrind --cache-sim=no \
            --smc-check=all --cachegrind-out-file=$cg \
            $stage/bin/python3.16 -S -W ignore \
            $retry/support/compile_corpus.py compile $stage/lib/python3.16 \
            > "$stdout" 2> "$stderr" &
    else
        LD_LIBRARY_PATH=$stage/lib PYTHON_JIT=1 valgrind --tool=cachegrind \
            --cache-sim=no --smc-check=all --cachegrind-out-file=$cg \
            $stage/bin/python3.16 -S -c \
            'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' \
            $workload > "$stdout" 2> "$stderr" &
    fi
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        cat "/proc/$pid/maps" >> "$map_file" 2>/dev/null || true
        sleep 0.05
    done
    if wait "$pid"; then
        rc=0
    else
        rc=$?
    fi
    printf '%s\n' "$rc" > "$exit_file"
    if [ "$rc" -ne 0 ]; then
        printf 'Cachegrind failed: %s (exit %s)\n' "$name" "$rc" >&2
        exit "$rc"
    fi
    "$annotator" --auto=no --show=Ir --threshold=0 "$cg" \
        > "$out/cachegrind/$name.annotate.txt"
    xz -T0 -9 "$cg"
}

run_profile base2-corpus corpus "" "$out/cachegrind/base2-corpus.maps"

for workload in nbody scimark_fft scimark_sparse_mat_mult; do
    run_profile "base2-$workload" kernel "$workload" \
        "$out/cachegrind/base2-$workload.maps"
done
