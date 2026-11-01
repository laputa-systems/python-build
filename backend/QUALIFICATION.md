# OpenSSL-free macOS arm64 candidate

**Policy change (2026-10-30):** the macOS deployment minimum is now 27.0 and PGO is deferred on every target. Results below predate that and describe macOS 26 and PGO builds.

**Historical research qualification:** This predates product adoption.
Statements below that production was unchanged apply to that earlier
checkout, not current main. See [docs/open-work.md](../docs/open-work.md).

## Result

The experimental distribution is fully OpenSSL-free. All 42 declared archive
qualification checks pass. This qualifies the isolated
[modern TLS policy](MODERN_TLS_POLICY.md), not complete OpenSSL API parity or
production adoption. Production defaults, source locks, release workflows,
and published artifacts are unchanged.

The qualification checkout is `19a99dc`. The runtime includes the factory
repairs in `568f84b` and context-role/socket cleanup repairs in `383fc57`.
Later documentation changes do not change the qualified payload.

Local outputs are under `.work/tmp/openssl-free-modern-rc2-final-archive/`:

- `experimental no-openssl candidate.tar.gz`
- `EXPERIMENTALartifact.json`
- `qualification report.json`

Archive SHA-256:
`82f5ef1225d5eefce9c1569da831d4895539f3423f38157433ba2fc0d0288cd6`.
The same archive identity resulted from three independent packaging passes.
This is packaging repeatability, not an independent clean-build reproduction.

## Implementation and inputs

`build-no-openssl.py` uses the existing build system with OpenSSL removed from
the experimental dependency graph. `Modules/Setup.local` explicitly disables
compiled `_ssl` and `_hashlib`; extensions are not deleted after building an
OpenSSL-linked interpreter. The GIL, shared libpython, no-pip policy, ThinLTO,
PGO, Apple M1 code generation, and macOS 26 deployment target remain enabled.

The separate fetch phase verifies the complete prepared RTLS source tree and
hashing snapshot. The build consumes locked Cargo inputs offline inside the
macOS network-denial sandbox. Boundary probes pass before and after building.
This macOS containment boundary is weaker than container isolation.
The manifest records the actual compiler, flags, source and patch hashes,
Cargo locks, 109 active Rust packages, licenses, and PGO receipt. PGO training
completed 10,590 tests across all 43 selected files.

| Component | Source or integration |
| --- | --- |
| TLS sockets, BIO, contexts, C handle binding | `backend/vendor/rtls/` (RTLS `302423b49e494863d07630d50b15a9488ec85ef7`), `rtls-graviola.patch` |
| Python bindings | `rtls-pyo3-0.30.patch` (PyO3 0.30.1, ABI3-py315) |
| Selected-path verification | `webpki-policy.patch`, `webpki-strict-policy.patch`, `webpki-crl-policy.patch` |
| Sessions and TLS controls | `rustls-session-api.patch` |
| Normal `ssl` and `_ssl` imports | `ssl_facade.py`, `ssl_native_compat.py`, `ssl_metadata.py` |
| Hashing native module and shim | `hashing/src/lib.rs`, `hashing/_hashlib.py` |
| Qualification | `qualify-no-openssl-regressions.py`, `regression_policy.py`, `qualify-no-openssl-archive.py`, `modern_compat.py` |
| Shared qualification correction | `buildsys/testsuite.py`, `tests/test_testsuite.py` |

TLS uses rustls 0.23.45, rustls-webpki 0.103.15, rustls-graviola 0.4.0, and
Graviola 0.4.1 with Rust nightly-2026-09-15 and LLVM 23.1.4. Hashlib uses CPython's
built-in guaranteed digests and narrowly selected RustCrypto implementations
for the missing native interfaces, HMAC, PBKDF2, scrypt, and additional
compatible digests. MD5 and SHA-1 hashing APIs remain supported. Their
availability does not enable legacy TLS signatures or suites.

The maintained PKCS#8 implementation handles modern encrypted keys. Traditional
encrypted PEM, PBES1, SHA-1 password derivation, and DES/3DES decryptors were
removed. Cargo lockfiles and license records stay inside this experiment.

## Security results

The extracted, stripped archive passes all 82 differential certificate cases
and all 12 verification-code checks against CPython 3.15.1/OpenSSL 3.5.7.
The original missing-AKI strict-verification failure now rejects with code 85.
Malformed PEM appended to a valid certificate chain now fails loading.

Strict policy runs on the actual webpki-verified leaf, intermediates, and
selected trust anchor. Maintained implementations verify signatures.
`TRUSTED_FIRST` controls issuer preference; `PARTIAL_CHAIN` separately permits
termination at an explicitly trusted intermediate. Parse failure never proves
self-signing. Failed loads preserve the previous usable configuration.

The archive also passes three positive modern transport cases, including
forced X25519MLKEM768, and six reference-negotiated retired-feature cases
which the candidate rejects. No retired case is counted as rejected because
of a certificate failure or timeout.

The full release validation wave additionally passed modern encrypted-key,
PEM-boundary, trust-source, SNI, BIO, group, signature, callback,
subinterpreter, and combined TLS/hash ownership contracts. The real pinned
Mozilla bundle retains all 120 roots. Explicit CA overrides, hashed certificate
directories, fallback suppression, relocation, `-I`, and `-E` are covered.
No OS root store is injected.

Documented restrictions remain: malformed hashed CA files fail atomically where
OpenSSL can retain a valid prefix; corrupted root self-signatures are rejected
where OpenSSL can accept an explicit trust anchor. Failed verification does
not fabricate a verified chain from the unverified peer chain. Offline CRLs
work through `cafile`; hashed `.r0`, delta, indirect, and online CRLs are not
supported. These limits must be reviewed for adoption.

## Compatibility and regression accounting

The complete serial default-resource run executed 50,058 tests: 547 of 556
selected files ran, with nine resource-denied files and 2,942 individual skips.
The experimental verdict passes with no unexpected failures. Raw CPython
reports `FAILURE`: 180 failure events remain visible rather than rewritten.

- `test_ssl`: 219 tests, 11 failures, six errors, 38 skips. Its 17 failure/error
  events match the exact policy classifier. They concern OpenSSL identity and
  version branches, private C type assumptions, retired DH/renegotiation, and
  explicitly unsupported option, entropy-injection, and proxy-certificate
  controls.
- `test_tools`: 169 makefile-folder subtest failures match the repository's
  existing product exclusion.
- Complete `test_hashlib` and `test_hmac` suites pass: 240 tests, 18 skips.
  The extracted archive also matches 39 naming/error cases, 19 HMAC/PBKDF2
  algorithms, four buffer cases, and 12 scrypt parameter bounds.

The initial parallel failures were retained and investigated. They exposed
missing key-log environment handling, ignored keyfile-only arguments, omitted
default trust loading in verification-enabled factories, missing context-role
validation, and a transferred-descriptor leak. All have permanent regressions.

A later parallel run timed out in plain-socket
`NetworkConnectionAttributesTest.testSourceAddress`. Its source-port fixture
releases a discovered port before the client binds it. A bounded collision
reproducer shows the same client error and waiting server on the pinned
reference and candidate. The initial client error was not observable behind
the blocked server; the collision mechanism is evidence, not a recovered
exception from that run. The serial full run completes that module unchanged.

The timeout also exposed a controller accounting defect. `c4330d6` makes the
shared parser reject embedded CPython worker timeouts even when file totals
look complete. All 27 focused controller tests pass. The timed-out run remains
recorded as incomplete and is not the qualifying run. A separate mailbox
timestamp flake reproduced nine times in 100 runs on the unchanged reference;
the qualifying serial run has no mailbox failure. Neither issue gained an
exclusion or changed an upstream test expectation.

Post-handshake authentication, external PSK, ECH, proxy certificates, manual
entropy injection, selected OpenSSL options, and private OpenSSL/C object
interfaces are unsupported. Unsupported requests fail explicitly. Initial
client authentication, TLS 1.2/1.3, sessions/resumption, SNI, ALPN, HTTPS, and
asyncio TLS are exercised. See [native compatibility](NATIVE_COMPATIBILITY.md)
for independently linked applications and consumers of OpenSSL pointers.

## Targets and artifact size

| Target | Evidence |
| --- | --- |
| macOS arm64 | Native pass on Apple M5 Max/macOS 27.0.1; 79 Mach-O images have the required architecture, deployment floor, dependencies, signatures, and RPATH policy |
| Apple M1 / macOS 26 runtime | M1 code generation and macOS 26 load-command floor checked; physical M1 and macOS 26 execution not performed |
| Linux x86_64 musl | Untested; outside this campaign's scope |
| Linux arm64 musl | Untested; outside this campaign's scope |

No audited OpenSSL-family unresolved symbols occur in the 79 native images.
The TLS adapter exports its initializer and six owned-handle C functions;
the hashing adapter exports only its initializer. Direct adapter-handle
lookups of the tested OpenSSL functions fail. Both adapters depend only on
`libSystem` and `libiconv`. Native extension, ABI3, shared-library embedding,
isolated startup, and relocation checks pass on the extracted archive.

| Measurement | Bytes |
| --- | ---: |
| TLS native extension, stripped | 3,631,360 |
| Hashing native extension, stripped | 527,408 |
| Shipped regular files, total | 39,084,309 |
| Uncompressed tar payload | 40,181,760 |
| Compressed distribution | 11,589,326 |

Sizes come from archive headers, excluding files generated by validation
after extraction. Performance gates were waived. Comparable cold/cached build
time, peak memory, scratch usage, and OpenSSL baseline economics were not
measured; no build-speed or size-improvement claim is made.

## Recommendation

Use this as an opt-in modern TLS candidate. OpenSSL removal is implemented
and demonstrated across the selected macOS product checks. Keep production
unchanged until the owner accepts the explicit API/credential restrictions,
application and native-consumer compatibility, and deployment evidence.
Further work has a concrete starting point: a passing source-bound artifact,
not another handshake proof of concept. Follow [REPRODUCE.md](REPRODUCE.md)
to fetch, build, run the complete regressions, and qualify a fresh archive.
