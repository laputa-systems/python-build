#!/bin/sh
set -eu

root=/home/ubuntu/repos/python-build
repo=$root/wt-calloc3-parent
stage=$repo/rust-cpython/stage-perf-calloc3-parent
run=$root/wt-jitnet/docs/campaign-evidence/2026-11-23-jit-net-profile
external=/home/ubuntu/exp-jitnet
out=$run/callgrind

mkdir -p "$out" "$external/profiles"
valgrind --version > "$run/valgrind-version.txt"
PATH="$run/support:$PATH"
export PATH
callgrind_annotate-3.18.1 --version > "$run/callgrind-annotate-version.txt" 2>&1 || true

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
    kind=$2
    name=parent-$workload-$kind
    cg=$external/profiles/$name.callgrind
    stdout=$out/$name.stdout
    stderr=$out/$name.stderr
    exit_file=$out/$name.exit

    if [ "$kind" = kernel ]; then
        command='import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))'
    else
        case "$workload" in
            nbody) module=nbody ;;
            scimark_sparse_mat_mult) module=scimark ;;
            *) printf 'unknown workload: %s\n' "$workload" >&2; exit 2 ;;
        esac
        command='import sys; from benchmarks.workloads import jit_kernels; jit_kernels._load(sys.argv[1]); print("setup-only")'
    fi

    if (cd "$repo" && LD_LIBRARY_PATH="$stage/lib" PYTHON_JIT=1 valgrind \
        --tool=callgrind --dump-instr=no --compress-strings=no \
        --callgrind-out-file="$cg" "$stage/bin/python3.16" -S -c "$command" \
        "$([ "$kind" = kernel ] && printf '%s' "$workload" || printf '%s' "$module")") \
        > "$stdout" 2> "$stderr"; then
        rc=0
    else
        rc=$?
    fi
    printf '%s\n' "$rc" > "$exit_file"
    [ "$rc" -eq 0 ]

    if callgrind_annotate-3.18.1 --auto=no --show=Ir --threshold=100 \
        --inclusive=yes --tree=caller "$cg" > "$out/$name.caller-tree.txt" 2> "$out/$name.annotate.stderr"; then
        annotate_rc=0
    else
        annotate_rc=$?
    fi
    printf '%s\n' "$annotate_rc" > "$out/$name.annotate.exit"
    xz -T0 -9 "$cg"
}

profile nbody kernel
profile nbody setup
profile scimark_sparse_mat_mult kernel
profile scimark_sparse_mat_mult setup
