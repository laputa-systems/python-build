#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
repo=$root/wt-jbitcast1
stage=$repo/rust-cpython/stage-perf-jbitcast1-cand
run=$root/wt-integ/docs/campaign-evidence/2026-11-23-jit-uop-profile
external=/home/ubuntu/exp-jituop
out=$run/callgrind

mkdir -p "$out" "$external/profiles"
valgrind --version > "$run/valgrind-version.txt"

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
        printf 'loader: %s\ndebug: %s\n' "$loader" "$debug"
        printf 'loader_build_id=%s\ndebug_build_id=%s\n' "$loader_id" "$debug_id"
        [ -n "$loader_id" ] && [ "$loader_id" = "$debug_id" ]
    } > "$run/musl-dbg-buildid.txt"
}

record_debug_ids

profile() {
    workload=$1
    name=cand-$workload-kernel
    cg=$external/profiles/$name.callgrind
    map=$external/$workload.jitmap
    stdout=$out/$name.stdout
    stderr=$out/$name.stderr
    exit_file=$out/$name.exit
    command='import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))'

    if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" PYTHON_JIT=1 \
        PYTHON_JIT_MAP="$map" valgrind \
        --tool=callgrind --dump-instr=yes --compress-strings=no --compress-pos=no \
        --callgrind-out-file="$cg" "$stage/bin/python3.16" -S -c "$command" "$workload") \
        > "$stdout" 2> "$stderr"; then
        rc=0
    else
        rc=$?
    fi
    printf '%s\n' "$rc" > "$exit_file"
    [ "$rc" -eq 0 ]

    if [ ! -s "$map" ]; then
        printf 'missing or empty jitmap: %s\n' "$map" > "$out/$name.jitmap-check.stderr"
        printf '1\n' > "$out/$name.jitmap-check.exit"
        exit 1
    fi
    u_count=$(awk -F '\t' '$1 == "U" { count++ } END { print count + 0 }' "$map")
    printf '%s\n' "$u_count" > "$out/$name.jitmap-u-lines.txt"
    if [ "$u_count" -gt 0 ]; then
        map_rc=0
    else
        printf 'jitmap has no U lines: %s\n' "$map" > "$out/$name.jitmap-check.stderr"
        map_rc=1
    fi
    printf '%s\n' "$map_rc" > "$out/$name.jitmap-check.exit"
    [ "$map_rc" -eq 0 ]

    xz -T0 -9 "$cg"
}

profile nbody
profile scimark_fft
profile scimark_sparse_mat_mult
