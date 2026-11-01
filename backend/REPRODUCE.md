# Reproduce the RTLS and Graviola experiments

For the complete native Linux arm64 candidate, use the
[Docker and prerelease procedure](LINUX.md). The replay procedures below
retain their macOS scope.

Both replay scripts run on native Apple Silicon macOS. They copy the
RTLS source vendored at commit `302423b49e494863d07630d50b15a9488ec85ef7`
from `backend/vendor/rtls/` and apply
[`rtls-graviola.patch`](rtls-graviola.patch). They build the
checked-in Cargo lockfile with Rust nightly-2026-09-15 (`1.100.0-nightly`). The replay verifies and patches
the pinned rustls-webpki archive before Cargo builds it as a path dependency.
Cargo downloads the remaining pinned crates. Neither script connects to an
external TLS server. Builds and disposable interpreters stay under `.work/tmp`.

## Graviola socket interoperability

Run:

```sh
backend/run-poc.sh
```

This builds the extension and runs `local_tls_smoke.py` with a host
`python3.15`. The smoke tests Graviola TLS 1.3 clients against both a Graviola
server and the host Python's OpenSSL server. The recorded run used Python
3.15.0. It is a provider interoperability check, not CPython 3.15.1 or
OpenSSL-removal qualification.

The scripts use `RTLS_RUST_TOOLCHAIN` (default `stable`) through rustup and
stop unless `rustc --version` is exactly `1.100.0-nightly`. If `stable` has moved,
install Rust nightly-2026-09-15 and set `RTLS_RUST_TOOLCHAIN=nightly-2026-09-15`. `kache` is
optional: it is used when available unless `RUSTC_WRAPPER` is set explicitly.
Set `RUSTC_WRAPPER=` to disable compiler caching, or set it to a wrapper path
to select one. The script leaves the checkout and build tree available for
inspection.

## CPython 3.15.1 without `_ssl`

Build the native macOS product interpreter from the locked inputs, then pass
its staged install and the extracted source tree's CPython test certificates
to the replay:

```sh
python3 build.py fetch --target aarch64-apple-darwin
python3 build.py build --target aarch64-apple-darwin
backend/run-no-ssl-contract.sh \
    build/stage/cpython-staged/install \
    build/work/cpython/Python-3.15.1/Lib/test/certdata
```

The script verifies the reference interpreter's exact Python and OpenSSL
versions, copies the installation into `.work/tmp`, builds RTLS for that
interpreter, removes `_ssl` from the copy, and keeps `_hashlib`. It installs
`ssl_facade.py` as the interpreter's `ssl.py` and uses one CPython test CA as
a temporary `python-build-cacert.pem`. The source installation is not changed.

`no_ssl_contract.py` runs against the pinned OpenSSL reference and the
OpenSSL-free copy. It covers socket TLS 1.3, default HTTPS, asyncio TLS,
hostname and issuer rejection, an expired leaf, a malformed chain, strict
verification of a leaf missing its Authority Key Identifier, and the
product-style trust-source cases. It generates the expired leaf locally with
the host `openssl` command and the CPython test CA key. All connections are
loopback only.

The OpenSSL reference and repaired RTLS candidate must both exit successfully.
The candidate rejects the strict-invalid leaf and malformed PEM chain. These
cases remain mandatory regression checks. The script runs candidate checks with both `-I` and `-E -s`, and
prints the disposable path for inspection.

The utility compatibility contract checks `RAND_bytes` integer conversion and
C `int` overflow, `get_protocol_name()`, malformed certificate decode errors,
and the CVE-2013-4238 certificate fields. A memory-BIO handshake checks
uppercase IPv6 SAN formatting from `SSLObject.getpeercert()`. The no-OpenSSL
facade reports `_OPENSSL_API_VERSION` as `(0, 0, 0, 0, 0)`. The version-guarded
upstream CVE test therefore selects its legacy OpenSSL branch, so the direct
certificate-field contract checks IPv6 SAN formatting separately.

Graviola 0.4.1 exposes system random filling but no entropy-injection API.
`RAND_add` raises `NotImplementedError` instead of reporting a false success.
Accordingly, upstream `test_random` passes the RNG status, positive-byte, and
negative-length checks, then fails at its first `RAND_add` call with
`NotImplementedError: RAND_add entropy injection is unsupported by the selected
random source` at `test_ssl.py:509`. The pinned OpenSSL reference completes that
test.

The test bundle is a single CPython test CA, not the production certifi PEM.
The replay does not establish the production bundle contents. The patched adapter does not import `wassima` or inject OS trust stores.
Its facade applies the product fallback policy separately.

`trust_policy_contract.py` uses the real locked Mozilla bundle. Its capath case
covers the OpenSSL 3.5.7 lookup behavior for unchanged files, removal before
first use, wrong-subject replacement before first use, addition before first
use, and removal after a certificate is cached. It also tests colon-separated
`SSL_CERT_DIR` paths with missing and valid components.

The adapter parses each hash-indexed PEM file atomically. OpenSSL can retain a
valid certificate before a malformed suffix, but the adapter rejects the whole
file. The trust contract records this fail-closed difference.

No Linux musl build, x86-64 verification, production release artifact,
build-time comparison, or archive-size comparison is performed by these
commands. The checked-in patch and harness are research artifacts; the
production build keeps OpenSSL and its existing CPU baselines.

## Differential certificate policy

Run the generated corpus against the pinned reference and candidate interpreters:

```sh
python3.15 backend/certificate_contract.py \
    --reference <reference-prefix>/bin/python3.15 \
    --candidate <candidate-prefix>/bin/python3.15 \
    --certdata <cpython-source>/Lib/test/certdata \
    --workdir .work/tmp/certificate-contract
```

The harness uses one-shot reference servers to present identical chains to
both verifying clients. Certificate setup failures and timeouts fail the
harness. Each case records flags, acceptance, error category, and the required
contract. The original missing-AKI and malformed-chain cases remain mandatory.
The duplicate-critical-extension fixture currently records an earlier candidate
loader rejection than the reference.

## Fresh CPython build without OpenSSL

`build-no-openssl.py` uses the locked macOS compiler and CPython inputs to
build a fresh CPython 3.15.1 prefix. It omits OpenSSL from the dependency
graph and disables CPython's `_ssl` and original `_hashlib` modules in
`Modules/Setup.local`. The optional RTLS and Graviola adapters are installed
after the interpreter is built. The driver requires native Apple Silicon,
Python 3.12 or newer, the locked compiler, and Rust nightly-2026-09-15 (`kache` is unused).

The Rust source acquisition is a separate network-enabled step. It copies
the vendored RTLS source into a private source tree, prepares the pinned
webpki source, applies the consolidated RTLS patch, and snapshots the hashing
lane into `$run_dir/hashing-build`. That snapshot
includes its Rust and Python sources, Cargo manifest and lockfile, README, and
license records. The fetch phase runs Cargo against the snapshot and records
its content digest in `$run_dir/inputs/hashing-source.sha256`. Before the
sealed build, the driver checks both the snapshot and the supplied hashing
source against that digest; changed or incomplete source fails before build.
The sealed overlay builds and installs from the staged snapshot, not the
working tree. Use the same run directory and source path for both phases:

The overlay builds both Rust extensions with Cargo `--release`, overriding
release settings to `opt-level=3`, `lto=thin`, `codegen-units=1`, `debug=0`,
and `panic=unwind`. The build keeps the `apple-m1` Rust target and the macOS
deployment minimum. Direct calls to `build_rust_extension` keep the debug
profile unless the caller requests release mode.

```sh
run_dir=.work/tmp/openssl-free-candidate
hashing_source=backend/hashing
python3.15 backend/build-no-openssl.py \
    --phase fetch --run-dir "$run_dir" \
    --hashing-source "$hashing_source"
python3.15 backend/build-no-openssl.py \
    --phase all --run-dir "$run_dir" \
    --hashing-source "$hashing_source"
```

If the hashing source changes after fetch, fetch the new revision into a fresh
run directory. The build then runs under the macOS sandbox with network access
denied.

The build prefix is `$run_dir/prefix`. The overlay phase relocates its Mach-O
install names and sysconfig files, copies it to `$run_dir/relocated prefix`,
and verifies that copy. `--phase base` can build only CPython. `--phase overlay`
can add adapters to an existing base prefix after `--phase fetch`;
pass that base prefix with `--prefix` and use the same run directory. RTLS is
installed in the standard-library directory, so imports work with site
initialization disabled. Every run directory and prefix must remain under
`.work/`. This experiment does not change production OpenSSL settings and
does not measure production build time or qualify release archives.

The base phase omits profile-guided optimization by default. Pass `--pgo` with
`--phase base` or `--phase all` to keep CPython's `--enable-optimizations`, run
the pinned `-m test --pgo` training task, and validate the merged profile.
`--pgo` is invalid with `--phase fetch` and `--phase overlay`.

Run the TLS contract against a disposable copy of the built prefix:

```sh
reference_python=/path/to/OpenSSL-reference/bin/python3.15
backend/run-built-no-ssl-contract.sh \
    "$run_dir/relocated prefix" \
    "$run_dir/cpython-source/Python-3.15.1/Lib/test/certdata" \
    "$reference_python"
```

The runner copies only `Lib/test` into the disposable prefix, installs the
test CA bundle, and executes the TLS contract with site initialization
disabled under `-I` and `-E`. It then restores the locked bundle, runs the
trust-policy suite, and runs CPython's `test_hashlib`, `test_hmac`, and
`test_ssl` suites against that copy. The `test_ssl` run uses a 120-second
per-test timeout. This keeps the candidate's installed
`ssl.py` in the import path.

## Combined interpreter and archive checks

The normal TLS import uses the interpreter-safe native handle binding from the
consolidated overlay. After building a candidate, run its ownership and TLS
contract and the combined TLS/hashing regression:

```sh
<candidate-prefix>/bin/python3.15 -E -s -B \
    backend/subinterpreter_contract.py <cpython-test-certdata>
<candidate-prefix>/bin/python3.15 -E -s -B \
    backend/hashing/testdrivers/combined_tls.py \
    backend <cpython-test-certdata>
```

Both recreate interpreters and exercise trusted and rejected TLS 1.2 and 1.3
connections. The combined regression also checks fixed hashing, HMAC, PBKDF2,
and scrypt outputs with both native backends active.

Run the complete default-resource regression suite on an unpruned candidate:

```sh
python3.15 backend/qualify-no-openssl-regressions.py \
    --prefix <relocated-candidate-prefix> \
    --run-dir .work/tmp/rtls-regression-qualification \
    --jobs 6 --timeout 600
```

The new run directory must contain no whitespace. The runner copies the
candidate there because an upstream asyncio test constructs an unquoted
shell command from the interpreter path. It compares the runtime files,
records the complete verbose run, and classifies SSL failures from that
same run. Its `report.json` binds the full log, extracted SSL log, policy,
and runtime identities. Diagnostics never replace the initial results.
Timeouts, incomplete accounting, and unexpected failures return failure.
The upstream SSL result remains visible even when its exact failures are
outside the experimental support policy.

Exercise factory environment handling under normal and isolated startup:

```sh
<candidate-prefix>/bin/python3.15 -I -B \
    backend/ssl_factory_contract.py <cpython-test-certdata>
<candidate-prefix>/bin/python3.15 -I -B \
    backend/test_ssl_protocol_mode.py
```

The factory contract launches child interpreters normally and with `-E` and
`-I`. The protocol-mode contract checks both BIO and socket roles, deferred
unconnected wrapping, and immediate cleanup of transferred descriptors.

After the full regression suite finishes, package and validate a private copy.
Pass the reference interpreter and the same CPython certificate directory.
Pass the complete pre-prune regression report, including its modern TLS case
assessment and candidate identity digests:

```sh
reference_python=/path/to/OpenSSL-reference/bin/python3.15
certdata=/path/to/Python-3.15.1/Lib/test/certdata
python3.15 backend/qualify-no-openssl-archive.py \
    --prefix <relocated-candidate-prefix> \
    --build-run-dir <candidate-build-directory> \
    --base-build-run-dir <pgo-base-build-directory> \
    --reference-python "$reference_python" \
    --certdata "$certdata" \
    --regression-report <complete-pre-prune-regression-report.json> \
    --run-dir .work/tmp/rtls-archive-qualification
```

The output directory must be new. If the PGO base and adapter overlay share a
run directory, omit `--base-build-run-dir`. The driver strips native images,
removes tests and bytecode, creates a deterministic archive, extracts it into
a path with spaces, and validates those exact bytes. It runs the 82-case
certificate contract, the modern TLS handshake contract, and the hashing
reference comparison on the extracted interpreter. It keeps the raw
OpenSSL-specific compatibility report. The effective experimental profile
replaces only `ssl_capabilities` with the locked Rustls modern capability
check.

The regression report must account for every test and match the candidate
files in both the source prefix and its disposable test copy. Missing,
incomplete, or unexpected regression results fail qualification. A passing
run writes `EXPERIMENTALartifact.json` beside the archive and report. The
record identifies the archive, source inputs, Rust dependency closure,
toolchains, build flags, and verified PGO profile. It is a research record.
The command does not publish an artifact, qualify the production release, or
establish runtime support on the macOS deployment minimum.
