#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
out=$root/wt-jbitcast1-run/docs/campaign-evidence/2026-11-23-j-bitcast/run
external=/home/ubuntu/exp-jbitcast1
annotator=$out/support/cg_annotate-3.18.1

repo_for_arm() {
    case "$1" in
        parent) printf '%s\n' "$root/wt-calloc3-parent" ;;
        cand) printf '%s\n' "$root/wt-jbitcast1" ;;
        *) printf 'unknown arm: %s\n' "$1" >&2; exit 2 ;;
    esac
}

stage_for_arm() {
    repo=$(repo_for_arm "$1")
    if [ "$1" = parent ]; then
        printf '%s\n' "$repo/rust-cpython/stage-perf-calloc3-parent"
    else
        printf '%s\n' "$repo/rust-cpython/stage-perf-jbitcast1-cand"
    fi
}

record_debug_ids() {
    install_db=/lib/apk/db/installed
    package_version() {
        awk -v package="$1" '
            $0 == "P:" package { in_package = 1; next }
            in_package && /^V:/ { sub(/^V:/, ""); print; exit }
            in_package && /^P:/ { exit }
        ' "$install_db"
    }
    musl_version=$(package_version musl)
    debug_version=$(package_version musl-dbg)
    [ "$musl_version" = "1.2.6-r3" ]
    [ "$debug_version" = "1.2.6-r3" ]
    {
        printf 'musl=%s\nmusl-dbg=%s\n' "$musl_version" "$debug_version"
        loader=$(file /lib/ld-musl-x86_64.so.1)
        debug=$(file /usr/lib/debug/lib/ld-musl-x86_64.so.1.debug)
        loader_id=$(printf '%s\n' "$loader" | sed -n 's/.*BuildID\[sha1\]=\([0-9a-f]*\).*/\1/p')
        debug_id=$(printf '%s\n' "$debug" | sed -n 's/.*BuildID\[sha1\]=\([0-9a-f]*\).*/\1/p')
        printf 'loader: %s\n' "$loader"
        printf 'debug: %s\n' "$debug"
        printf 'loader_build_id=%s\ndebug_build_id=%s\n' "$loader_id" "$debug_id"
        [ -n "$loader_id" ] && [ "$loader_id" = "$debug_id" ]
    } > "$out/musl-dbg-buildid.txt"
}

mkdir -p "$out/cachegrind" "$out/precompile" "$external/cachegrind"
valgrind --version > "$out/support/valgrind-version.txt"
record_debug_ids
: > "$out/cachegrind/musl-dbg-per-run.txt"

for arm in cand; do
    if [ -f "$out/precompile/$arm.exit" ]; then
        continue
    fi
    repo=$(repo_for_arm "$arm")
    stage=$(stage_for_arm "$arm")
    if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" "$stage/bin/python3.16" \
        -m compileall -q "$repo/benchmarks" "$stage/lib/python3.16") \
        > "$out/precompile/$arm.stdout" 2> "$out/precompile/$arm.stderr"; then
        rc=0
    else
        rc=$?
    fi
    printf '%s\n' "$rc" > "$out/precompile/$arm.exit"
done

run_profile() {
    arm=$1
    workload=$2
    kind=$3
    run=$4
    repo=$(repo_for_arm "$arm")
    stage=$(stage_for_arm "$arm")
    name=$arm-$workload-$run
    cg=$external/cachegrind/$name.cg
    stdout=$out/cachegrind/$name.stdout
    stderr=$out/cachegrind/$name.stderr
    exit_file=$out/cachegrind/$name.exit

    if [ "$kind" = corpus ]; then
        if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" valgrind --tool=cachegrind \
            --cache-sim=no --smc-check=all --cachegrind-out-file="$cg" \
            "$stage/bin/python3.16" -S -W ignore \
            "$out/support/compile_corpus.py" compile "$stage/lib/python3.16") \
            > "$stdout" 2> "$stderr"; then
            rc=0
        else
            rc=$?
        fi
    elif [ "$kind" = whole ]; then
        if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" PYTHON_JIT=1 valgrind \
            --tool=cachegrind --cache-sim=no --smc-check=all \
            --cachegrind-out-file="$cg" "$stage/bin/python3.16" -S -c \
            'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' \
            "$workload") > "$stdout" 2> "$stderr"; then
            rc=0
        else
            rc=$?
        fi
    else
        case "$workload" in
            setup-nbody) module=nbody ;;
            setup-scimark_fft|setup-scimark_sparse_mat_mult) module=scimark ;;
            *) printf 'unknown setup workload: %s\n' "$workload" >&2; exit 2 ;;
        esac
        if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" PYTHON_JIT=1 valgrind \
            --tool=cachegrind --cache-sim=no --smc-check=all \
            --cachegrind-out-file="$cg" "$stage/bin/python3.16" -S -c \
            'import sys; from benchmarks.workloads import jit_kernels; jit_kernels._load(sys.argv[1]); print("setup-only")' \
            "$module") > "$stdout" 2> "$stderr"; then
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
    printf '%s %s\n' "$name" "$(sed -n 's/^loader_build_id=//p' "$out/musl-dbg-buildid.txt")" \
        >> "$out/cachegrind/musl-dbg-per-run.txt"
}

for arm in cand; do
    for workload in corpus scimark_fft nbody scimark_sparse_mat_mult; do
        if [ "$workload" = corpus ]; then kind=corpus; else kind=whole; fi
        for run in 1 2; do
            run_profile "$arm" "$workload" "$kind" "$run"
        done
    done
    for workload in setup-nbody setup-scimark_fft setup-scimark_sparse_mat_mult; do
        for run in 1 2; do
            run_profile "$arm" "$workload" setup "$run"
        done
    done
done

pair_delta() {
    first=$1
    second=$2
    one=$(awk '/PROGRAM TOTALS/ {gsub(/,/, "", $1); print $1; exit}' "$first")
    two=$(awk '/PROGRAM TOTALS/ {gsub(/,/, "", $1); print $1; exit}' "$second")
    python3 -c 'import sys; a,b=map(int,sys.argv[1:]); print(abs(a-b)/((a+b)/2)*100)' "$one" "$two"
}

retry_candidate_if_needed() {
    workload=$1
    kind=$2
    first=$out/cachegrind/cand-$workload-1.annotate.txt
    second=$out/cachegrind/cand-$workload-2.annotate.txt
    delta=$(pair_delta "$first" "$second")
    printf 'initial cand %s %s %s\n' "$workload" "$delta" \
        "$(awk -v d="$delta" 'BEGIN {if (d > 0.1) print "miss"; else print "within"}')" \
        >> "$out/repeatability-candidate.txt"
    if awk -v d="$delta" 'BEGIN {exit !(d > 0.1)}'; then
        printf 'fresh candidate pair: %s\n' "$workload" \
            >> "$out/repeatability-candidate-reruns.txt"
        run_profile cand "$workload" "$kind" retry1-1
        run_profile cand "$workload" "$kind" retry1-2
        first=$out/cachegrind/cand-$workload-retry1-1.annotate.txt
        second=$out/cachegrind/cand-$workload-retry1-2.annotate.txt
        delta=$(pair_delta "$first" "$second")
        printf 'retry cand %s %s %s\n' "$workload" "$delta" \
            "$(awk -v d="$delta" 'BEGIN {if (d > 0.1) print "non-repeatable"; else print "within"}')" \
            >> "$out/repeatability-candidate.txt"
    fi
}

: > "$out/repeatability-candidate.txt"
: > "$out/repeatability-candidate-reruns.txt"
for workload in corpus scimark_fft nbody scimark_sparse_mat_mult; do
    if [ "$workload" = corpus ]; then kind=corpus; else kind=whole; fi
    retry_candidate_if_needed "$workload" "$kind"
done
for workload in setup-nbody setup-scimark_fft setup-scimark_sparse_mat_mult; do
    retry_candidate_if_needed "$workload" setup
done
