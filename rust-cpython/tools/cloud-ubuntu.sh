#!/usr/bin/env bash
set -euo pipefail

tools_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd -- "${RUST_CPYTHON_CLOUD_CHECKOUT:-$tools_dir/../..}" && pwd)
git_common_dir=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)
primary=$(dirname "$git_common_dir")
image=python-build-campaign:ubuntu24.04
lane_home=${RUST_CPYTHON_CLOUD_HOME:-$(dirname "$primary")/.python-build-campaign}

case "${1:-}" in
    image)
        context=$(mktemp -d)
        trap 'rm -rf "$context"' EXIT
        cp "$repo/rust-cpython/linux-toolchain.lock.json" "$context/"
        docker build --platform linux/amd64 -t "$image" \
            -f "$tools_dir/cloud-ubuntu.Dockerfile" "$context"
        ;;
    fetch|offline)
        mode=$1
        shift
        mkdir -p "$lane_home"
        lane_home=$(cd -- "$lane_home" && pwd)
        runner=(docker run --rm --platform linux/amd64 --cap-drop ALL
            --security-opt no-new-privileges --user "$(id -u):$(id -g)"
            --volume "$primary:$primary" --volume "$lane_home:$lane_home"
            --workdir "$repo" --env "HOME=$lane_home" --env PYTHONDONTWRITEBYTECODE=1
            --env PYTHONUNBUFFERED=1 --env "CAMPAIGN_TOOLS=$primary/rust-cpython/tools")
        case "$repo/" in
            "$primary/"*) ;;
            *) runner+=(--volume "$repo:$repo") ;;
        esac
        if [[ $mode == fetch ]]; then
            ca_bundle=${RUST_CPYTHON_CLOUD_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}
            test -r "$ca_bundle"
            container_ca=/run/campaign-ca.pem
            "${runner[@]}" --volume "$ca_bundle:$container_ca:ro" \
                --env "SSL_CERT_FILE=$container_ca" --env "CURL_CA_BUNDLE=$container_ca" \
                --env "CARGO_HTTP_SSL_CAINFO=$container_ca" --env "GIT_SSL_CAINFO=$container_ca" \
                "$image" bash -euc '
                export PATH="$CAMPAIGN_TOOLS:$HOME/.cargo/bin:$PATH"
                if ! command -v rustup >/dev/null; then
                    scratch=$(mktemp -d)
                    trap '\''rm -rf "$scratch"'\'' EXIT
                    url=https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init
                    curl -fsSL "$url" -o "$scratch/rustup-init"
                    (cd "$scratch" && curl -fsSL "$url.sha256" | sha256sum -c -)
                    chmod +x "$scratch/rustup-init"
                    "$scratch/rustup-init" -y --no-modify-path --default-toolchain none
                fi
                rustup toolchain install nightly-2026-09-15 --profile minimal --component rust-src
                python3 rust-cpython/build.py fetch
                python3 rust-cpython/perf.py fetch
            '
        else
            [[ $# -gt 0 ]] || { echo "offline requires a command" >&2; exit 2; }
            "${runner[@]}" --network none --env RUST_CPYTHON_DOCKER_OFFLINE=1 \
                "$image" bash -euc 'export PATH="$CAMPAIGN_TOOLS:$HOME/.cargo/bin:$PATH"; exec "$@"' bash "$@"
        fi
        ;;
    *)
        echo "Usage: bash rust-cpython/tools/cloud-ubuntu.sh {image|fetch|offline COMMAND...}" >&2
        exit 2
        ;;
esac
