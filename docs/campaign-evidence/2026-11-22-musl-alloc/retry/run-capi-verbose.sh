#!/bin/sh
set -u

root=/home/ubuntu/repos/python-build
lane=$root/wt-allocretry/rust-cpython
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry/correctness/suites

for arm in base mi je; do
    stage=$lane/stage-perf-musl-${arm}2
    set +e
    LD_LIBRARY_PATH="$stage/lib" PYTHON_JIT=1 \
        "$stage/bin/python3.16" -m test -j 1 --timeout=900 -v test_capi \
        > "$out/$arm-capi-verbose.log" 2>&1
    status=$?
    set -e
    printf '%s\n' "$status" > "$out/$arm-capi-verbose.log.exit"
    printf '%s test_capi verbose exit=%s\n' "$arm" "$status"
done
