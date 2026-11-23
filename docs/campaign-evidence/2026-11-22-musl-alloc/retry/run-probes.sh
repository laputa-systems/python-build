#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
stage_root=$root/wt-allocretry/rust-cpython
out=$root/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry

mkdir -p "$out/a4" "$out/a5"

for arm in base mi je; do
    stage=$stage_root/stage-perf-musl-${arm}2
    probe_out=$out/a4/$arm
    mkdir -p "$probe_out"
    if python3 "$out/support/residency_probe2.py" \
        "$stage/bin/python3.16" "$out/support/payload-argparse.py" 6 "$probe_out" \
        > "$out/a4/$arm.json" 2> "$out/a4/$arm.stderr"; then
        printf '0\n' > "$out/a4/$arm.exit"
    else
        rc=$?
        printf '%s\n' "$rc" > "$out/a4/$arm.exit"
        printf 'A4 probe failed: %s (exit %s)\n' "$arm" "$rc" >&2
        exit "$rc"
    fi
done

for arm in base mi je; do
    stage=$stage_root/stage-perf-musl-${arm}2
    for run in 1 2 3; do
        name=$arm-corpus-$run
        if LD_LIBRARY_PATH=$stage/lib /usr/bin/time -f %M \
            $stage/bin/python3.16 -S -W ignore \
            $out/support/compile_corpus.py compile $stage/lib/python3.16 \
            > "$out/a5/$name.stdout" 2> "$out/a5/$name.time"; then
            printf '0\n' > "$out/a5/$name.exit"
        else
            rc=$?
            printf '%s\n' "$rc" > "$out/a5/$name.exit"
            printf 'A5 corpus run failed: %s (exit %s)\n' "$name" "$rc" >&2
            exit "$rc"
        fi
    done
done
