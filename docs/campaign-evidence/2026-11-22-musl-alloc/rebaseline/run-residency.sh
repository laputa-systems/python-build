#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
lane=$root/wt-rebase/rust-cpython
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline/residency
probe=$root/wt-integ/docs/campaign-evidence/2026-11-22-probe2/residency_probe2.py
payload=$root/wt-integ/docs/campaign-evidence/2026-11-22-probe2/payload-argparse.py

run_probe() {
    stage=$1
    name=$2
    stage_dir=$lane/stage-$stage
    outdir=$out/$name
    printf 'RUN %s\n' "$name"
    env LD_LIBRARY_PATH="$stage_dir/lib" python3 "$probe" \
        "$stage_dir/bin/python3.16" "$payload" 6 "$outdir" > "$out/$name.json"
}

run_probe perf-rb2-crel3-parent-serc crel3-parent-before
run_probe perf-rb2-h-serc h2-candidate
run_probe perf-rb2-crel3-parent-serc crel3-parent-after
