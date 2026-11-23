#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
out=$root/wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run
suite_label=test_threading-test_thread-test_embed-test_capi-test_sys-test__interpreters-test_interpreters
serc_label=test_compile-test_marshal-test_dis-test_import-serc-verify

repo_for_arm() {
    case "$1" in
        parent) printf '%s\n' "$root/wt-calloc3-parent" ;;
        cand) printf '%s\n' "$root/wt-calloc3-cand" ;;
        *) printf 'unknown arm: %s\n' "$1" >&2; exit 2 ;;
    esac
}

mkdir -p "$out/correctness/parity" "$out/correctness/serc" \
    "$out/correctness/suites" "$out/correctness/fuzz" \
    "$out/correctness/threaded" "$out/correctness/ctypes" \
    "$out/correctness/launch"

: > "$out/correctness/exit-summary.txt"

run_test() {
    arm=$1
    category=$2
    label=$3
    shift 3
    repo=$(repo_for_arm "$arm")
    lane=$repo/rust-cpython
    stage_name=perf-calloc3-$arm
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
    if [ "$category" = suites ] && [ "$rc" -ne 0 ] && \
        grep -q 'test_capi failed' "$out/correctness/$category/$arm.log"; then
        build_python=$lane/work/perf/$stage_name/build/python
        stage=$lane/stage-perf-calloc3-$arm
        if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" "$build_python" -m test \
            -v -j 4 --timeout=900 test_capi) \
            > "$out/correctness/suites/$arm-capi-verbose.log" 2>&1; then
            verbose_rc=0
        else
            verbose_rc=$?
        fi
        printf '%s suites-capi-verbose %s\n' "$arm" "$verbose_rc" \
            >> "$out/correctness/exit-summary.txt"
    fi
    printf '%s/%s exit=%s\n' "$arm" "$category" "$rc"
}

for arm in parent cand; do
    repo=$(repo_for_arm "$arm")
    stage=$repo/rust-cpython/stage-perf-calloc3-$arm
    if LD_LIBRARY_PATH=$stage/lib "$stage/bin/python3.16" \
        "$out/support/compile_parity.py" "$stage/lib/python3.16" \
        > "$out/correctness/parity/$arm.json" \
        2> "$out/correctness/parity/$arm.stderr"; then
        rc=0
    else
        rc=$?
    fi
    printf '%s parity %s\n' "$arm" "$rc" \
        >> "$out/correctness/exit-summary.txt"
    printf '%s/parity exit=%s\n' "$arm" "$rc"
done
python3 "$out/support/normalize_parity.py" \
    > "$out/correctness/parity/normalize.stdout" \
    2> "$out/correctness/parity/normalize.stderr"

run_test parent suites "$suite_label" \
    --suite test_threading --suite test_thread --suite test_embed \
    --suite test_capi --suite test_sys --suite test__interpreters \
    --suite test_interpreters
run_test cand suites "$suite_label" \
    --suite test_threading --suite test_thread --suite test_embed \
    --suite test_capi --suite test_sys --suite test__interpreters \
    --suite test_interpreters

run_test cand serc "$serc_label" \
    --suite test_compile --suite test_marshal --suite test_dis --suite test_import \
    --serc-verify

for arm in parent cand; do
    repo=$(repo_for_arm "$arm")
    lane=$repo/rust-cpython
    stage=$lane/stage-perf-calloc3-$arm
    if LD_LIBRARY_PATH=$stage/lib python3 "$lane/tests/jit_fuzz.py" \
        --python "$stage/bin/python3.16" \
        --programs 2000 --seed 0 \
        > "$out/correctness/fuzz/$arm.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s fuzz %s\n' "$arm" "$rc" \
        >> "$out/correctness/exit-summary.txt"
    printf '%s/fuzz exit=%s\n' "$arm" "$rc"

    if LD_LIBRARY_PATH=$stage/lib "$stage/bin/python3.16" -S \
        "$out/support/threaded_compile_smoke.py" \
        "$stage/lib/python3.16/typing.py" \
        > "$out/correctness/threaded/$arm.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s threaded %s\n' "$arm" "$rc" \
        >> "$out/correctness/exit-summary.txt"
    printf '%s/threaded exit=%s\n' "$arm" "$rc"

    if LD_LIBRARY_PATH=$stage/lib /usr/bin/python3 -c \
        'import ctypes,sys; lib=ctypes.CDLL(sys.argv[1]); lib.Py_GetVersion.restype=ctypes.c_char_p; print("Py_GetVersion", lib.Py_GetVersion().decode())' \
        "$stage/lib/libpython3.16.so.1.0" \
        > "$out/correctness/ctypes/$arm.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    printf '%s ctypes %s\n' "$arm" "$rc" \
        >> "$out/correctness/exit-summary.txt"
    printf '%s/ctypes exit=%s\n' "$arm" "$rc"
done
