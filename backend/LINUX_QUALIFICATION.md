# OpenSSL-free Linux arm64 qualification

**Historical research qualification:** This predates product adoption.
Statements below that production was unchanged apply to that earlier
checkout, not current main. See [docs/open-work.md](../docs/open-work.md).

## Result

The isolated CPython 3.15.1 distribution is fully OpenSSL-free. The native
Docker run at `af1e789b87b5a6ae892910ca53c29e3b0ead6568` passes all 53 archive
checks and the separate minimal-runtime check. The prerelease workflow uses
this same build and qualification procedure. The native ARM64
[CI run](https://github.com/joshuarli/python-build/actions/runs/40046301806)
also passes all 53 checks at source
`86f0635020fc94ce75f3449b2efb817726adf623`.
Production defaults, production
locks, CPU floors, and the production release workflow remain unchanged.

The local run uses an arm64 Linux VM on an Apple M5 Max through OrbStack.
The Docker daemon, toolchain image, build container, qualification container,
and extracted-archive runtime all report arm64. No amd64 container ran.

The qualified local archive has these identities:

| Item | Value |
| --- | --- |
| Archive SHA-256 | `54cc552fa971bfb10a03e7fc470d01d8b46b60ce307b6243c850d5452243694f` |
| Compressed archive | 13,048,600 bytes |
| Payload | 44,390,400 bytes |
| Stripped RTLS extension | 3,746,416 bytes |
| Stripped hashing extension | 465,840 bytes |

The qualification archive and reports are preserved under
`.work/tmp/retained/linux-rc7/qualification/`, with run receipts under
`.work/tmp/retained/linux-rc7/`.
The [Docker procedure](LINUX.md) describes the report and receipt paths.

The [verified prerelease](https://github.com/joshuarli/python-build/releases/tag/research-openssl-free-cp315-arm64-86f0635020fc94ce75f3449b2efb817726adf623)
publishes `experimental-no-openssl-candidate.tar.gz` with SHA-256
`33b2282a9aca7e163d0f8d3a06234d074cde5eec71b09a0f0ace8b38cae52f32`.
The compressed archive is 13,047,754 bytes; its payload is 44,400,640 bytes.
CI downloads the published assets and verifies their checksums, source
identity, manifest, and native Docker receipts. An independent download
passes the same checks. The release tag points to the exact source commit.

The first preview at `46606f7` is superseded. GitHub normalized spaces in its
asset filenames, invalidating the downloaded checksum paths. Commit `86f0635`
uses canonical filenames without spaces and adds the public download gate.
The earlier local run predates that filename change.

## Implementation

`build-no-openssl.py` excludes OpenSSL from the candidate dependency graph.
`Modules/Setup.local` disables compiled `_ssl` and `_hashlib` before building.
The normal Python imports use the RTLS and hashing adapters described in the
[macOS implementation report](QUALIFICATION.md). CPython's built-in guaranteed
digests remain available. MD5 and SHA-1 hashing APIs remain supported.

The Linux profile uses ARMv8-A with crypto extensions on arm64 and
Broadwell on x86-64, ThinLTO, `ld.lld`, shared libpython, and the GIL.
Rust extensions link the musl dynamic CRT (`-crt-static`; static CRT cannot emit cdylib or proc-macro artifacts). Every arm64 CPU must expose AES,
PMULL, SHA2, and ASIMD; every x86-64 CPU must expose AES, PCLMULQDQ, AVX,
AVX2, BMI1, BMI2, FMA, and MOVBE.
Linux PGO, pip, ensurepip, and venv remain disabled. Build containers have no
network interface except loopback and no default route.

The inputs pin nightly Rust 2026-09-15, LLVM 23.1.3, RTLS commit
`302423b49e494863d07630d50b15a9488ec85ef7`, rustls 0.23.45,
rustls-webpki 0.103.15, rustls-graviola 0.4.0, and Graviola 0.4.1.
The recorded Rust closure contains 109 packages and 215 installed license
files. The separate reference uses CPython 3.15.1 and OpenSSL 3.5.7.

The Linux follow-up repairs include:

- `a61e3c4`: preserve `ENOTCONN` before creating handshake state.
- `8e311e1`: preserve fatal certificate alerts on Linux. After rejection,
  one nonblocking syscall consumes at most 65,536 queued bytes. Socket flags,
  timeouts, and verification decisions stay intact.
- `cca56b3` and `3562110`: identify musl despite unrelated libgcc version tags
  named `GLIBC_2.0`. Candidate and reference apply the isolated CPython patch.
- `f67616a` and `805ba5d`: defer symlink metadata until referents exist during
  product copies, including relocation and launcher probes.
- `2f6aa24`: use Python's TOML library inside the qualification application,
  removing an external parser dependency.
- `af1e789`: carry the verified ELF linkage results into the manifest.

The Docker wrapper keeps disposable tests on a Linux disk volume with short
paths. The qualification account matches the host UID and has zero effective
capabilities. A separate root initializer has only `CAP_CHOWN`. Its account
files are mounted read-only. The volume is removed after qualification.

## Security and compatibility

All 82 differential certificate cases pass against pinned OpenSSL 3.5.7,
including the original strict missing-AKI and malformed trailing-PEM cases.
All 12 verification-code checks match. The selected-path verification design
and its documented restrictions remain in the [implementation report](QUALIFICATION.md).

All 16 trust-source tests pass on the extracted archive using the real locked
Mozilla bundle. Six bounded-cleanup tests pass. The modern transport matrix
passes three positive cases, including forced X25519MLKEM768, and six
reference-negotiated retired-feature cases. No case is unavailable.

The complete default-resource regression run executes 50,488 tests across
547 of 556 selected files, with nine resource-denied files. Raw accounting
retains 181 failures and 2,587 skips. The three failing files are `test_ssl`,
`test_ctypes`, and `test_tools`.

The exact 219-case SSL inventory records 11 failures, six errors, and 37 skips.
Every failure matches the [declared modern TLS policy](MODERN_TLS_POLICY.md).
The other accepted cases are the existing `test_ctypes:test_lists_system`
static-libffi/musl expectation and `test_tools:test_makefile_test_folders`
build-tree expectation. There are zero unexpected failures. No upstream test
expectation is weakened. The hashing and HMAC suites pass in the full run.

All 79 ELF images pass the OpenSSL symbol audit. The dependency record lists
musl and libgcc as platform libraries, with bundled shared libpython.
Relocation, native extension loading, ABI3 loading, and embedding pass.
The minimal Alpine runtime executes SHA-256, HMAC, PBKDF2, scrypt, and a TLS
ClientHello without the build toolchain or networking.

## Limits and adoption

This qualifies the experimental modern contract. Direct native consumers of
OpenSSL objects or symbols retain the limitations in
[NATIVE_COMPATIBILITY.md](NATIVE_COMPATIBILITY.md). Production adoption remains
a separate decision.

The earlier [macOS archive](QUALIFICATION.md) remains a separate qualification.
The new alert cleanup also passes its six focused tests on macOS. This follow-up
does not repeat the complete macOS archive qualification. Linux amd64 remains
untested and outside this run.

Some locked Alpine edge APK URLs have expired. The run restores existing
cached bytes and verifies every locked hash and size. The workflow can restore
the existing arm64 input cache. A cold fetch needs a durable source for those
same bytes. Production bootstrap pins are unchanged.

The corresponding stripped OpenSSL reference extensions measure 7,962,320
bytes for TLS and 6,521,368 bytes for hashing. Their combined size is
14,483,688 bytes, compared with 4,212,256 bytes for the candidate extensions.
Both use CPython 3.15.1, the same ARM crypto floor, ThinLTO, and LLVM 23.1.3
`--strip-unneeded`. The reference uses OpenSSL 3.5.7 with `no-apps no-tests`.

Build-time, peak-memory, and complete distribution-size comparisons against
that baseline are unmeasured. No performance gate or claimed speedup determines
this result.
