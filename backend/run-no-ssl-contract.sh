#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work_parent="$repo_root/.work/tmp"

case "$(uname -s):$(uname -m)" in
    Darwin:arm64) ;;
    *) printf '%s\n' 'This replay covers native Apple Silicon macOS only.' >&2; exit 2 ;;
esac

if [ "$#" -ne 2 ]; then
    printf 'usage: %s <cpython-3.15.1-install> <cpython-test-certdata>\n' \
        "$0" >&2
    exit 2
fi

source_prefix=$(CDPATH= cd -- "$1" && pwd)
certdata=$(CDPATH= cd -- "$2" && pwd)
python="$source_prefix/bin/python3.15"
version=$(
    "$python" -I -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
)
if [ "$version" != 3.15.1 ]; then
    printf 'Expected CPython 3.15.1, found %s\n' "$version" >&2
    exit 1
fi

mkdir -p "$work_parent"
work_dir=$(mktemp -d "$work_parent/rtls-no-ssl.XXXXXX")
candidate="$work_dir/python"
checkout="$work_dir/rtls"
harness="$work_dir/no_ssl_contract.py"
utilities_harness="$work_dir/ssl_utilities_contract.py"
mkdir -p "$work_dir/tmp"
export CARGO_HOME="$work_dir/cargo-home"
/usr/bin/ditto "$source_prefix" "$candidate"
cp "$repo_root/backend/no_ssl_contract.py" "$harness"
cp "$repo_root/backend/ssl_utilities_contract.py" \
    "$utilities_harness"

cp -R "$repo_root/backend/vendor/rtls" "$checkout"

for patch_name in rtls-graviola.patch rtls-pyo3-0.30.patch; do
    patch -d "$checkout" -p1 --batch --forward --fuzz=0 --dry-run \
        -i "$repo_root/backend/$patch_name"
    patch -d "$checkout" -p1 --batch --forward --fuzz=0 \
        -i "$repo_root/backend/$patch_name"
done

rust_toolchain=${RTLS_RUST_TOOLCHAIN:-stable}
expected_rust_version=1.100.0-nightly
rust_version_output=$(rustup run "$rust_toolchain" rustc --version) || {
    printf '%s\n' 'Install rustup and Rust 1.100.0-nightly to replay this prototype.' >&2
    exit 1
}
rust_version=$(printf '%s\n' "$rust_version_output" | awk '{print $2}')
if [ "$rust_version" != "$expected_rust_version" ]; then
    printf 'Expected Rust %s, found %s.\n' \
        "$expected_rust_version" "$rust_version" >&2
    exit 1
fi
if [ "${RUSTC_WRAPPER+x}" != x ]; then
    if command -v kache >/dev/null 2>&1; then
        RUSTC_WRAPPER=$(command -v kache)
    else
        RUSTC_WRAPPER=
    fi
    export RUSTC_WRAPPER
fi

export TMPDIR="$work_dir/tmp"
export CARGO_TARGET_DIR="$work_dir/target"
export PYO3_BUILD_EXTENSION_MODULE=1
export PYO3_PYTHON="$candidate/bin/python3.15"
export RUSTFLAGS='-C link-arg=-undefined -C link-arg=dynamic_lookup'
webpki_archive="$work_dir/rustls-webpki.crate"
"$python" "$repo_root/backend/prepare_webpki.py" fetch "$webpki_archive"
"$python" "$repo_root/backend/prepare_webpki.py" prepare "$webpki_archive" "$checkout"
rustls_archive="$work_dir/rustls.crate"
"$python" "$repo_root/backend/prepare_rustls.py" fetch "$rustls_archive"
"$python" "$repo_root/backend/prepare_rustls.py" prepare "$rustls_archive" "$checkout"
rustup run "$rust_toolchain" cargo build \
    --manifest-path "$checkout/rust/Cargo.toml" --locked
cp "$CARGO_TARGET_DIR/debug/lib_rustls.dylib" \
    "$checkout/src/rtls/_rustls.abi3.so"

stdlib="$candidate/lib/python3.15"
module_dir="$stdlib"
dynload="$stdlib/lib-dynload"
mkdir -p "$module_dir"
if [ -e "$module_dir/rtls" ]; then
    mv "$module_dir/rtls" "$work_dir/original-rtls"
fi
cp -R "$checkout/src/rtls" "$module_dir/rtls"
cp "$repo_root/backend/ssl_facade.py" "$stdlib/ssl.py"
cp "$repo_root/backend/ssl_native_compat.py" "$stdlib/_ssl.py"
cp "$repo_root/backend/ssl_metadata.py" "$stdlib/_ssl_metadata.py"
cp "$certdata/capath/ceff1710.0" "$stdlib/python-build-cacert.pem"

extension_count=0
mkdir -p "$work_dir/removed"
for extension in "$dynload"/_ssl*.so; do
    if [ -f "$extension" ]; then
        extension_count=$((extension_count + 1))
        mv "$extension" "$work_dir/removed/$(basename "$extension")"
    fi
done
if [ "$extension_count" -ne 1 ]; then
    printf 'Expected one _ssl extension, found %s\n' "$extension_count" >&2
    exit 1
fi

export PYTHONPYCACHEPREFIX="$work_dir/pycache"
"$python" -B -I "$harness" "$certdata" --openssl-reference
nullbytecert="$certdata/nullbytecert.pem"
"$python" -B -I "$utilities_harness" "$nullbytecert"

candidate_status=0
"$candidate/bin/python3.15" -B -I \
    "$harness" "$certdata" \
    || candidate_status=$?
utilities_candidate_status=0
"$candidate/bin/python3.15" -B -I \
    "$utilities_harness" "$nullbytecert" \
    || utilities_candidate_status=$?
environment_status=0
"$candidate/bin/python3.15" -B -E -s \
    "$harness" "$certdata" \
    || environment_status=$?
utilities_environment_status=0
"$candidate/bin/python3.15" -B -E -s \
    "$utilities_harness" "$nullbytecert" \
    || utilities_environment_status=$?
printf 'OpenSSL-free isolated candidate exit status: %s\n' "$candidate_status"
printf 'OpenSSL-free utility contract exit status: %s\n' \
    "$utilities_candidate_status"
printf 'OpenSSL-free -E candidate exit status: %s\n' "$environment_status"
printf 'OpenSSL-free -E utility contract exit status: %s\n' \
    "$utilities_environment_status"
printf 'Disposable interpreter and RTLS build: %s\n' "$work_dir"
if [ "$candidate_status" -ne 0 ]; then
    exit "$candidate_status"
fi
if [ "$utilities_candidate_status" -ne 0 ]; then
    exit "$utilities_candidate_status"
fi
if [ "$environment_status" -ne 0 ]; then
    exit "$environment_status"
fi
exit "$utilities_environment_status"
