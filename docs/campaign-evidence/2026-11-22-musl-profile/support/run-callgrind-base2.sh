#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
stage=$root/wt-allocretry/rust-cpython/stage-perf-musl-base2
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-profile

mkdir -p "$out/callgrind"

run_profile() {
    workload=$1
    cg=$out/callgrind/base2-$workload.callgrind
    stdout=$out/callgrind/base2-$workload.stdout
    stderr=$out/callgrind/base2-$workload.stderr
    exit_file=$out/callgrind/base2-$workload.exit
    map_file=$out/callgrind/base2-$workload.maps
    : > "$map_file"
    LD_LIBRARY_PATH=$stage/lib PYTHON_JIT=1 valgrind --tool=callgrind \
        --smc-check=all --dump-instr=yes \
        --callgrind-out-file=$cg \
        $stage/bin/python3.16 -S -c \
        'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' \
        $workload > "$stdout" 2> "$stderr" &
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
        printf 'Callgrind failed: %s (exit %s)\n' "$workload" "$rc" >&2
        exit "$rc"
    fi
    xz -T0 -9 "$cg"
}

for workload in scimark_fft scimark_sparse_mat_mult; do
    run_profile "$workload"
done
