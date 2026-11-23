#!/bin/sh
set -u

root=/home/ubuntu/repos/python-build
lane=$root/wt-rebase/rust-cpython
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline
support=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/receipts/support
source_hash=cpython-e891a9117985888df3e5d48145cf07ddda098335
followup=$out/correctness-followup
mkdir -p "$followup/parity" "$followup/fuzz" "$followup/capi-opt"
: > "$followup/exit-summary.txt"

record() {
    label=$1
    shift
    if "$@" > "$followup/$label.log" 2>&1; then
        status=0
    else
        status=$?
    fi
    printf '%s %s\n' "$label" "$status" >> "$followup/exit-summary.txt"
    printf '%s exit=%s\n' "$label" "$status"
}

for pair in \
    "perf-rb2-crel3-parent-serc crel3-parent" \
    "perf-rb2-h-serc h2"
do
    set -- $pair
    stage=$1
    name=$2
    stage_dir=$lane/stage-$stage
    source=$lane/work/perf/$stage/source/$source_hash
    record "$name-parity" env LD_LIBRARY_PATH="$stage_dir/lib" \
        "$stage_dir/bin/python3.16" "$support/compile_parity.py" "$source/Lib"
done

for pair in \
    "perf-rb2-h-serc h2" \
    "perf-rb2-jes3-candidate-serc jes3-candidate"
do
    set -- $pair
    stage=$1
    name=$2
    stage_dir=$lane/stage-$stage
    record "$name-fuzz" env LD_LIBRARY_PATH="$stage_dir/lib" \
        python3 "$lane/tests/jit_fuzz.py" --python "$stage_dir/bin/python3.16" \
        --programs 2000 --seed 0 --jobs 4
done

for pair in \
    "perf-rb2-jes2-parent-serc jes2-parent" \
    "perf-rb2-h-serc h2" \
    "perf-rb2-jes3-candidate-serc jes3-candidate"
do
    set -- $pair
    stage=$1
    name=$2
    stage_dir=$lane/stage-$stage
    record "$name-capi-opt-verbose" env LD_LIBRARY_PATH="$stage_dir/lib" PYTHON_JIT=1 \
        "$stage_dir/bin/python3.16" -m test -j 1 --timeout=900 -v test_capi.test_opt
done
