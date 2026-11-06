# Run the Linux candidates

See the [native qualification results](LINUX_QUALIFICATION.md) for measured
artifact sizes, executed tests, and remaining limits.

Use a clean checkout and a native Linux Docker daemon of the target
architecture. On Apple Silicon, use the daemon's arm64 VM for arm64 builds.
The driver rejects a daemon whose architecture does not match `--target` and
never uses emulation.

Add `.work/` to Git's local exclusions before creating scratch files:

```sh
printf '\n.work/\n' >> "$(git rev-parse --git-path info/exclude)"
mkdir -p .work/tmp
```

Build and qualify a fresh candidate:

```sh
python3.15 backend/docker-linux.py \
    --phase all \
    --target aarch64-unknown-linux-musl \
    --run-dir .work/tmp/openssl-free-linux-arm64 \
    --jobs 6 \
    --source-revision "$(git rev-parse HEAD)"
```

For x86-64, pass `--target x86_64-unknown-linux-musl` with a native amd64
daemon; per-architecture inputs live in `linux-bootstrap.lock.json` (arm64)
and `linux-bootstrap-x86_64.json` (x86-64).

The command fails if the checkout is dirty or the run directory is populated.
Use a new run directory after a failed build. Keep the failed run for diagnosis.

The fetch phase verifies the production C inputs and the per-architecture
experimental bootstrap lock (`linux-bootstrap.lock.json` for arm64,
`linux-bootstrap-x86_64.json` for x86-64). The native
build consumes these inputs with networking disabled. Rust sources stay pinned
by the experiment's source checks and Cargo locks. Production locks remain
unchanged.

The candidate uses `-march=armv8-a+crypto` on arm64 (`-march=broadwell` on
x86-64), ThinLTO, `ld.lld`, shared libpython, and the GIL, with musl linked
through the musl dynamic CRT into the Rust extensions. Linux PGO is disabled. Every arm64 CPU
must expose AES, PMULL, SHA2, and ASIMD.
No OpenSSL source enters the candidate dependency graph. The separate reference
build uses the locked OpenSSL 3.5.7 source.

The driver runs the complete default-resource CPython regression suite on a
copy inside a Docker-managed Linux disk volume. Short temporary paths preserve
Unix-socket support. Linux filename semantics do not depend on the macOS bind
mount. The volume remains available through archive validation and is then
removed.

Inspect the resulting evidence under the run directory:

- `linux-build.json` and `docker-build.json` identify the sealed native build.
- `qualification/regression/report.json` accounts for the upstream suite and
  retains failures under the declared [modern TLS policy](MODERN_TLS_POLICY.md).
- `qualification/archive/qualification-report.json` records checks against the
  extracted archive.
- `qualification/archive/EXPERIMENTALartifact.json` binds sources, tools,
  licenses, and archive bytes.
- `qualification/archive/experimental-no-openssl-candidate.tar.gz` contains the
  experimental distribution.
- `docker-runtime.json` records execution of those archive bytes in a separate
  locked Alpine image with no libgcc and no build toolchain.

The minimal runtime executes SHA-256, HMAC, PBKDF2, scrypt, and a TLS ClientHello.
Certificate, trust-source, HTTP, asyncio, native-extension, and relocation
checks run during archive qualification. A nonzero command status is a failed
qualification, even if an archive or partial report exists.

## Run the prerelease workflow

After pushing the tested revision, dispatch the official build workflow:

```sh
gh workflow run build.yml --ref main
gh run list --workflow build.yml
gh run watch <run-id> --exit-status
```

Linux jobs run on native `ubuntu-24.04` (amd64) and `ubuntu-24.04-arm`
runners using the same Docker driver; the macOS job runs natively on
`xcode-27`. Assembly validates the three artifacts and publishes them as an
official date-tagged prerelease.

## Verify a published prerelease

Download the release tag into a fresh directory:

```sh
gh release download <tag> --dir .work/tmp/published-candidate
```

Check the downloaded bytes against the published checksums:

```sh
(cd .work/tmp/published-candidate && shasum -a 256 -c SHA256SUMS)
```

Per-target manifests (`artifact-<triple>.json`) bind the archive bytes to
the qualified source revision.
