#!/usr/bin/env bash
set -euo pipefail

repo=/home/ubuntu/repos/python-build
integ=$repo/wt-integ
lane=$repo/wt-rebase/rust-cpython
raw=$integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline/pystats
workloads="nbody spectral_norm float richards raytrace fannkuch deltablue chaos go nqueens hexiom scimark_sor scimark_sparse_mat_mult scimark_fft scimark_lu scimark_monte_carlo"
stages="h-pystats jes2-parent-pystats jes3-candidate-pystats"

mkdir -p "$raw"
for stage in $stages; do
    python=$lane/stage-perf-rb2-$stage/bin/python3.16
    stage_out=$raw/$stage
    mkdir "$stage_out"
    for workload in $workloads; do
        out=$stage_out/$workload
        mkdir "$out"
        rm -rf /tmp/py_stats
        mkdir -p /tmp/py_stats
        started=$(date +%s)
        set +e
        (
            cd "$integ"
            LD_LIBRARY_PATH="$lane/stage-perf-rb2-$stage/lib" \
                PYTHON_JIT=1 PYTHONSTATS=1 PYTHON_JIT_MAP="$out/jit-map.tsv" \
                "$python" -S -c \
                'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' \
                "$workload" > "$out/stdout.txt" 2> "$out/stderr.txt"
        )
        status=$?
        set -e
        index=0
        for file in /tmp/py_stats/*; do
            if [ -f "$file" ]; then
                mv "$file" "$out/stats-$index.txt"
                index=$((index + 1))
            fi
        done
        printf '%s %s exit=%s elapsed=%ss stats_files=%s map_lines=%s\n' \
            "$stage" "$workload" "$status" "$(( $(date +%s) - started ))" \
            "$index" "$(wc -l < "$out/jit-map.tsv" 2>/dev/null || echo 0)" \
            | tee -a "$integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline/pystats-progress.txt"
    done
done
printf 'DONE\n' | tee -a "$integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline/pystats-progress.txt"
