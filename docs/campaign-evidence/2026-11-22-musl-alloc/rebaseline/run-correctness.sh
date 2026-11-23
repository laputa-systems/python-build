#!/bin/sh
set -u

root=/home/ubuntu/repos/python-build
lane=$root/wt-rebase/rust-cpython
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/rebaseline
support=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/receipts/support
source_hash=cpython-e891a9117985888df3e5d48145cf07ddda098335

mkdir -p "$out/parity" "$out/correctness"
: > "$out/correctness/exit-summary.txt"

record() {
    label=$1
    shift
    if "$@" > "$out/correctness/$label.log" 2>&1; then
        status=0
    else
        status=$?
    fi
    printf '%s %s\n' "$label" "$status" >> "$out/correctness/exit-summary.txt"
    printf '%s exit=%s\n' "$label" "$status"
}

run_serc() {
    stage=$1
    name=$2
    record "$name-serc-verify" python3 "$lane/perf.py" test --name "$stage" \
        --suite test_compile --suite test_marshal --suite test_dis --suite test_import \
        --serc-verify --jobs 4
}

run_jit_suites() {
    stage=$1
    name=$2
    record "$name-jit-suites" python3 "$lane/perf.py" test --name "$stage" \
        --suite test_array --suite test_sys_settrace --suite test_monitoring \
        --suite test_frame --suite test_capi.test_opt --jobs 4
}

run_fuzz() {
    stage=$1
    name=$2
    record "$name-fuzz" env LD_LIBRARY_PATH="$lane/$stage/lib" \
        python3 "$lane/tests/jit_fuzz.py" --python "$lane/$stage/bin/python3.16" \
        --programs 2000 --seed 0 --jobs 4
}

run_verbose_opt() {
    stage=$1
    name=$2
    record "$name-capi-opt-verbose" env LD_LIBRARY_PATH="$lane/$stage/lib" PYTHON_JIT=1 \
        "$lane/$stage/bin/python3.16" -m test -j 1 --timeout=900 -v test_capi.test_opt
}

for pair in \
    "perf-rb2-crel3-parent-serc crel3-parent" \
    "perf-rb2-h-serc h2"
do
    set -- $pair
    stage=$1
    name=$2
    source=$lane/work/perf/$stage/source/$source_hash
    if env LD_LIBRARY_PATH="$lane/$stage/lib" "$lane/$stage/bin/python3.16" \
        "$support/compile_parity.py" "$source/Lib" \
        > "$out/parity/$name.json" 2> "$out/parity/$name.stderr"; then
        status=0
    else
        status=$?
    fi
    printf '%s-parity %s\n' "$name" "$status" >> "$out/correctness/exit-summary.txt"
    printf '%s-parity exit=%s\n' "$name" "$status"
    run_serc "$stage" "$name"
done

run_jit_suites perf-rb2-jes2-parent-serc jes2-parent
run_jit_suites perf-rb2-h-serc h2
run_jit_suites perf-rb2-jes3-candidate-serc jes3-candidate

run_fuzz perf-rb2-h-serc h2
run_fuzz perf-rb2-jes3-candidate-serc jes3-candidate

run_verbose_opt perf-rb2-jes2-parent-serc jes2-parent
run_verbose_opt perf-rb2-h-serc h2
run_verbose_opt perf-rb2-jes3-candidate-serc jes3-candidate

for stage in perf-rb2-h-serc perf-rb2-jes3-candidate-serc; do
    source=$lane/work/perf/$stage/source/$source_hash
    export CARGO_HOME=$lane/.cargo-home
    export RUSTUP_HOME=$lane/.cache/rustup-musl
    export RUSTUP_TOOLCHAIN=nightly-2026-09-15
    export PATH=$CARGO_HOME/bin:$PATH
    export CARGO_TARGET_DIR=$lane/work/perf/$stage/cargo-test-target
    name=h2
    [ "$stage" = perf-rb2-jes3-candidate-serc ] && name=jes3-candidate
    record "$name-jit-opt-rs-tests" "$CARGO_HOME/bin/cargo" test \
        --manifest-path "$source/Cargo.toml" -p _jit_opt_rs --locked --offline
done
