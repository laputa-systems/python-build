#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
out=$root/wt-jbitcast1-run/docs/campaign-evidence/2026-11-23-j-bitcast/run
c3=$root/wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run/correctness
source_hash=cpython-e891a9117985888df3e5d48145cf07ddda098335
suite_label=test_array-test_sys_settrace-test_monitoring-test_frame-test_capi.test_opt

repo_for_arm() {
    case "$1" in
        parent) printf '%s\n' "$root/wt-calloc3-parent" ;;
        cand) printf '%s\n' "$root/wt-jbitcast1" ;;
        *) printf 'unknown arm: %s\n' "$1" >&2; exit 2 ;;
    esac
}

stage_name_for_arm() {
    if [ "$1" = parent ]; then
        printf '%s\n' perf-calloc3-parent
    else
        printf '%s\n' perf-jbitcast1-cand
    fi
}

stage_for_arm() {
    repo=$(repo_for_arm "$1")
    name=$(stage_name_for_arm "$1")
    printf '%s\n' "$repo/rust-cpython/stage-$name"
}

mkdir -p "$out/correctness/parity" "$out/correctness/jit-suites" \
    "$out/correctness/capi-opt" "$out/correctness/fuzz" \
    "$out/correctness/rust-jit" "$out/correctness/ctypes" \
    "$out/correctness/launch"

cp "$c3/parity/parent.json" "$out/correctness/parity/parent.json"
cp "$c3/fuzz/parent.log" "$out/correctness/fuzz/parent.log"
cp "$c3/ctypes/parent.log" "$out/correctness/ctypes/parent.log"
: > "$out/correctness/exit-summary.txt"

run_test() {
    arm=$1
    category=$2
    label=$3
    shift 3
    repo=$(repo_for_arm "$arm")
    lane=$repo/rust-cpython
    stage_name=$(stage_name_for_arm "$arm")
    if (cd "$repo" && python3 "$lane/perf.py" test --name "$stage_name" "$@") \
        > "$out/correctness/launch/$arm-$category.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s %s %s\n' "$arm" "$category" "$rc" \
        >> "$out/correctness/exit-summary.txt"
    harness_log=$lane/logs/$stage_name-$label.log
    if [ -f "$harness_log" ]; then
        cp "$harness_log" "$out/correctness/$category/$arm.log"
    else
        printf 'missing harness log: %s\n' "$harness_log" \
            > "$out/correctness/$category/$arm.log"
    fi
    printf '%s/%s exit=%s\n' "$arm" "$category" "$rc"
}

stage=$(stage_for_arm cand)
if LD_LIBRARY_PATH="$stage/lib" "$stage/bin/python3.16" \
    "$out/support/compile_parity.py" "$stage/lib/python3.16" \
    > "$out/correctness/parity/cand.json" \
    2> "$out/correctness/parity/cand.stderr"; then
    rc=0
else
    rc=$?
fi
printf 'cand parity %s\n' "$rc" >> "$out/correctness/exit-summary.txt"
if python3 "$out/support/normalize_parity.py" \
    > "$out/correctness/parity/normalize.stdout" \
    2> "$out/correctness/parity/normalize.stderr"; then
    rc=0
else
    rc=$?
fi
printf 'normalized parity %s\n' "$rc" \
    >> "$out/correctness/exit-summary.txt"

for arm in parent cand; do
    run_test "$arm" jit-suites "$suite_label" \
        --suite test_array --suite test_sys_settrace --suite test_monitoring \
        --suite test_frame --suite test_capi.test_opt --jobs 4

    stage=$(stage_for_arm "$arm")
    if LD_LIBRARY_PATH="$stage/lib" PYTHON_JIT=1 \
        "$stage/bin/python3.16" -m test -j 1 --timeout=900 -v test_capi.test_opt \
        > "$out/correctness/capi-opt/$arm.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s capi-opt %s\n' "$arm" "$rc" \
        >> "$out/correctness/exit-summary.txt"
done

: > "$out/correctness/capi-opt-failure-ids.txt"
for arm in parent cand; do
    printf '%s:\n' "$arm" >> "$out/correctness/capi-opt-failure-ids.txt"
    matches=$(awk '/^(FAIL|ERROR): / { print }' \
        "$out/correctness/capi-opt/$arm.log")
    if [ -n "$matches" ]; then
        printf '%s\n' "$matches" >> "$out/correctness/capi-opt-failure-ids.txt"
    else
        printf '  (none)\n' >> "$out/correctness/capi-opt-failure-ids.txt"
    fi
done

stage=$(stage_for_arm cand)
lane=$root/wt-jbitcast1/rust-cpython
if LD_LIBRARY_PATH="$stage/lib" python3 "$lane/tests/jit_fuzz.py" \
    --python "$stage/bin/python3.16" --programs 2000 --seed 0 \
    > "$out/correctness/fuzz/cand.log" 2>&1; then
    rc=0
else
    rc=$?
fi
printf 'cand fuzz %s\n' "$rc" >> "$out/correctness/exit-summary.txt"

for arm in parent cand; do
    repo=$(repo_for_arm "$arm")
    lane=$repo/rust-cpython
    stage_name=$(stage_name_for_arm "$arm")
    source=$lane/work/perf/$stage_name/source/$source_hash
    export CARGO_HOME=$lane/.cargo-home
    export RUSTUP_HOME=$lane/.cache/rustup-musl
    export RUSTUP_TOOLCHAIN=nightly-2026-09-15
    export PATH=$CARGO_HOME/bin:$PATH
    export CARGO_TARGET_DIR=$lane/work/perf/$stage_name/cargo-test-target
    if "$CARGO_HOME/bin/cargo" test --manifest-path "$source/Cargo.toml" \
        -p _jit_opt_rs --locked --offline \
        > "$out/correctness/rust-jit/$arm.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s rust-jit %s\n' "$arm" "$rc" \
        >> "$out/correctness/exit-summary.txt"
done

stage=$(stage_for_arm cand)
if LD_LIBRARY_PATH="$stage/lib" /usr/bin/python3 -c \
    'import ctypes,sys; lib=ctypes.CDLL(sys.argv[1]); lib.Py_GetVersion.restype=ctypes.c_char_p; print("Py_GetVersion", lib.Py_GetVersion().decode())' \
    "$stage/lib/libpython3.16.so.1.0" \
    > "$out/correctness/ctypes/cand.log" 2>&1; then
    rc=0
else
    rc=$?
fi
printf 'cand ctypes %s\n' "$rc" >> "$out/correctness/exit-summary.txt"
