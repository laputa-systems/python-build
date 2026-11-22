#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
stage_root=$root/wt-musl/rust-cpython
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/receipts
annotator=$out/support/cg_annotate-3.18.1

mkdir -p "$out/cachegrind"
valgrind --version > "$out/support/valgrind-version.txt"

for arm in base mi je; do
    stage=$stage_root/stage-perf-musl-$arm
    for workload in corpus scimark_fft nbody scimark_sparse_mat_mult; do
        for run in 1 2; do
            name=$arm-$workload-$run
            cg=$out/cachegrind/$name.cg
            stdout=$out/cachegrind/$name.stdout
            stderr=$out/cachegrind/$name.stderr
            exit_file=$out/cachegrind/$name.exit

            if [ "$workload" = corpus ]; then
                if LD_LIBRARY_PATH=$stage/lib valgrind --tool=cachegrind --cache-sim=no \
                    --smc-check=all --cachegrind-out-file=$cg \
                    $stage/bin/python3.16 -S -W ignore \
                    $out/support/compile_corpus.py compile $stage/lib/python3.16 \
                    > "$stdout" 2> "$stderr"; then
                    rc=0
                else
                    rc=$?
                fi
            else
                if LD_LIBRARY_PATH=$stage/lib PYTHON_JIT=1 valgrind --tool=cachegrind \
                    --cache-sim=no --smc-check=all --cachegrind-out-file=$cg \
                    $stage/bin/python3.16 -S -c \
                    'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' \
                    $workload > "$stdout" 2> "$stderr"; then
                    rc=0
                else
                    rc=$?
                fi
            fi
            printf '%s\n' "$rc" > "$exit_file"
            if [ "$rc" -ne 0 ]; then
                printf 'Cachegrind failed: %s (exit %s)\n' "$name" "$rc" >&2
                exit "$rc"
            fi

            "$annotator" --auto=no --show=Ir --threshold=0 "$cg" \
                > "$out/cachegrind/$name.annotate.txt"
            xz -T0 -9 "$cg"
        done
    done
done
