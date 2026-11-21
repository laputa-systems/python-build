#!/usr/bin/env bash
# Diagnostic pystats + JIT-map collection on the 16 JIT_SET training kernels (one run each).
set -euo pipefail
REPO=/home/ubuntu/repos/python-build/wt-cloc-parent
STAGE=/home/ubuntu/repos/python-build/wt-jes1-cand/rust-cpython/stage-perf-jes1-pystats
OUT=/home/ubuntu/exp-jit/collect-jes1
mkdir -p "$OUT"
for w in nbody spectral_norm float richards raytrace fannkuch deltablue chaos go nqueens hexiom \
         scimark_sor scimark_sparse_mat_mult scimark_fft scimark_lu scimark_monte_carlo; do
    d=$OUT/$w; rm -rf "$d"; mkdir -p "$d"; rm -rf /tmp/py_stats; mkdir -p /tmp/py_stats
    start=$(date +%s)
    (cd "$REPO" && LD_LIBRARY_PATH=$STAGE/lib PYTHON_JIT=1 PYTHONSTATS=1 PYTHON_JIT_MAP=$d/jit-map.tsv \
        "$STAGE/bin/python3.16" -S -c 'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' "$w" \
        > "$d/stdout.txt" 2> "$d/stderr.txt") || echo "FAIL $w $?" | tee -a "$OUT/failures.txt"
    i=0; for f in /tmp/py_stats/*; do [ -f "$f" ] && mv "$f" "$d/stats-$i.txt" && i=$((i+1)); done
    echo "$w $(( $(date +%s) - start ))s stats_files=$i map_lines=$(wc -l < "$d/jit-map.tsv" 2>/dev/null || echo 0)" | tee -a "$OUT/progress.txt"
done
echo DONE | tee -a "$OUT/progress.txt"
