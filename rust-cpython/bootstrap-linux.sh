#!/usr/bin/env bash
# Native Ubuntu 24.04 rust-cpython lane: explicit, idempotent setup phases.
# No package or Rust installations happen unless the 'install' phase is chosen.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

usage() {
  printf 'Usage: bash rust-cpython/bootstrap-linux.sh {install|fetch|smoke|check|all}\n' >&2
  exit 2
}

require_host() {
  [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
    echo 'ERROR: native Linux x86_64 host required' >&2; exit 1;
  }
  source /etc/os-release
  [[ $ID == ubuntu ]] || {
    echo 'ERROR: Ubuntu host required by linux-toolchain.lock.json' >&2; exit 1;
  }
  python3 - "$VERSION_ID" <<'PY' || {
import json
import sys

document = json.load(open("rust-cpython/linux-toolchain.lock.json"))
allowed = {document["host"]["os_version_id"]}
allowed.update(entry["os_version_id"] for entry in document.get("provisional_hosts", ()))
sys.exit(0 if sys.argv[1] in allowed else 1)
PY
    echo 'ERROR: host must run Ubuntu 24.04 or a provisional_hosts entry in linux-toolchain.lock.json' >&2; exit 1;
  }
  HOST_SPEC=lock
  if [[ $VERSION_ID != 24.04 ]]; then
    HOST_SPEC=provisional
    echo "NOTE: Ubuntu $VERSION_ID is a provisional interim host; receipts are PROVISIONAL pending 24.04 re-measurement" >&2
  fi
  [[ -z ${RUSTUP_TOOLCHAIN:-} ]] || {
    echo 'ERROR: unset RUSTUP_TOOLCHAIN; the lane pins nightly-2026-09-15 itself' >&2; exit 1;
  }
  command -v python3 >/dev/null || { echo 'ERROR: install Python 3 first' >&2; exit 1; }
}

require_namespaces() {
  if ! /usr/bin/unshare --user --map-root-user --net -- true 2>/dev/null; then
    cat >&2 <<'EOF'
ERROR: offline build isolation requires unprivileged user+network namespaces.
Ubuntu 24.04 AppArmor often restricts unprivileged user namespaces by default.
Have the host administrator grant a narrow, reviewed AppArmor policy for the
build isolation entrypoint (or use an approved host where the check succeeds).
Do not disable AppArmor or user-namespace protections globally as a workaround.
EOF
    exit 1
  fi
}

install_packages() {
  # Python stdlib avoids a jaq/jq bootstrap dependency. The selected host
  # spec (lock or provisional) supplies the make/pkgconf and package pins.
  mapfile -t spec_lines < <(python3 - "$VERSION_ID" <<'PY'
import json
import sys

document = json.load(open("rust-cpython/linux-toolchain.lock.json"))
version = sys.argv[1]
if version == document["host"]["os_version_id"]:
    spec = {
        "system_packages": document["system_packages"],
        "make_package": document["make"]["package"].split("=", 1)[1],
        "pkgconf_package": document["pkgconf"]["package"].split("=", 1)[1],
    }
else:
    spec = next(
        entry for entry in document.get("provisional_hosts", ())
        if entry["os_version_id"] == version
    )
print(spec["make_package"])
print(spec["pkgconf_package"])
print("\n".join(spec["system_packages"]))
PY
)
  make_pin=${spec_lines[0]}
  pkgconf_pin=${spec_lines[1]}
  pinned=("${spec_lines[@]:2}")
  [[ ${#pinned[@]} -gt 0 ]] || { echo 'ERROR: empty package pin list' >&2; exit 1; }
  sudo apt-get update
  sudo apt-get install -y \
    git curl ca-certificates python3 tar xz-utils util-linux procps time dpkg-dev \
    make="$make_pin" pkgconf="$pkgconf_pin" "${pinned[@]}"
}

install_rust() {
  if ! command -v rustup >/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
      sh -s -- -y --profile minimal --default-toolchain none
  fi
  export PATH="$HOME/.cargo/bin:$PATH"
  rustup toolchain install nightly-2026-09-15 --profile minimal --component rust-src
  rustup run nightly-2026-09-15 rustc --version
}

install_gate() {
  mkdir -p "$HOME/.local/bin"
  install -m 755 rust-cpython/tools/perf-gate-ready "$HOME/.local/bin/perf-gate-ready"
  command -v perf-gate-ready
  perf-gate-ready --self-test
}

fetch_inputs() {
  require_namespaces
  python3 rust-cpython/build.py fetch
  python3 rust-cpython/build.py doctor
  python3 rust-cpython/perf.py fetch
  python3 rust-cpython/perf.py doctor
  python3 rust-cpython/perf.py status
}

smoke() {
  require_namespaces
  python3 rust-cpython/build.py doctor
  python3 rust-cpython/build.py build --jobs "${JOBS:-2}"
  rust-cpython/stage/bin/python3.16d -E -S -c \
    'import sys; assert sys.version_info[:2] == (3, 16); print(sys.version)'
}

check() {
  require_namespaces
  python3 rust-cpython/build.py doctor
  python3 rust-cpython/perf.py doctor
  command -v perf-gate-ready >/dev/null || {
    echo 'ERROR: install the repo-provided perf-gate-ready first' >&2; exit 1;
  }
  perf-gate-ready --self-test
  # A busy host fails the quiet gate, but does not invalidate setup.
  if ! perf-gate-ready -s .; then
    echo 'NOTE: host is not quiet; do not run performance acceptance gates' >&2
  fi
  python3 rust-cpython/perf.py status
  echo 'OK: Linux toolchain and lane prerequisites installed; baseline refresh next'
}

case "${1:-}" in
  install) require_host; install_packages; install_rust; install_gate ;;
  fetch) require_host; fetch_inputs ;;
  smoke) require_host; smoke ;;
  check) require_host; check ;;
  all) require_host; install_packages; install_rust; install_gate; fetch_inputs; smoke; check ;;
  *) usage ;;
esac
