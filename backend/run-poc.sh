#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work_parent="$repo_root/.work/tmp"

case "$(uname -s):$(uname -m)" in
    Darwin:arm64) ;;
    *) printf '%s\n' 'This replay covers native Apple Silicon macOS only.' >&2; exit 2 ;;
esac

mkdir -p "$work_parent"
work_dir=$(mktemp -d "$work_parent/rtls-graviola.XXXXXX")
checkout="$work_dir/rtls"

cp -R "$repo_root/backend/vendor/rtls" "$checkout"

for patch_name in rtls-graviola.patch rtls-pyo3-0.30.patch; do
    patch -d "$checkout" -p1 --batch --forward --fuzz=0 --dry-run \
        -i "$repo_root/backend/$patch_name"
    patch -d "$checkout" -p1 --batch --forward --fuzz=0 \
        -i "$repo_root/backend/$patch_name"
done

mkdir -p "$work_dir/tmp"
export TMPDIR="$work_dir/tmp"
export CARGO_HOME="$work_dir/cargo-home"
export CARGO_TARGET_DIR="$work_dir/target"
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
export PYO3_BUILD_EXTENSION_MODULE=1
export RUSTFLAGS='-C link-arg=-undefined -C link-arg=dynamic_lookup'

webpki_archive="$work_dir/rustls-webpki.crate"
python3.15 "$repo_root/backend/prepare_webpki.py" fetch "$webpki_archive"
python3.15 "$repo_root/backend/prepare_webpki.py" prepare "$webpki_archive" "$checkout"
rustls_archive="$work_dir/rustls.crate"
python3.15 "$repo_root/backend/prepare_rustls.py" fetch "$rustls_archive"
python3.15 "$repo_root/backend/prepare_rustls.py" prepare "$rustls_archive" "$checkout"
rustup run "$rust_toolchain" cargo build \
    --manifest-path "$checkout/rust/Cargo.toml" --locked
cp "$CARGO_TARGET_DIR/debug/lib_rustls.dylib" "$checkout/src/rtls/_rustls.abi3.so"
env -u PYTHONHOME -u PYTHONPATH -u PYTHONUSERBASE PYTHONNOUSERSITE=1 \
    python3.15 "$repo_root/backend/local_tls_smoke.py" "$checkout"
printf 'Prototype checkout and build tree: %s\n' "$work_dir"
