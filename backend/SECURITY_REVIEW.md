# Security review record

Review of 2026-10-30, run by two read-only reviewer agents with fresh context
over the code the project owns. It is an independent second reading, not an
external human audit; an external audit remains a separate decision.

Scope: `backend/hashing/src`, `backend/hashing/_hashlib.py` (Rust and Python
hashing FFI); `backend/ssl_facade.py`, the three webpki patches, the session
patch, and the verification, trust, hostname, SNI, session, BIO, and socket
hunks of `rtls-graviola.patch`. The PyO3 0.30.1 upgrade in
`rtls-pyo3-0.30.patch` postdates this review and was not re-reviewed.

## Hashing FFI

No memory-safety, reference-count, GIL, integer, panic, or subinterpreter
defect was found. Fixed in the Python facade: shared state through
`copy.copy`, `digestmod` matched by name instead of identity, and a missing
`string=` keyword. Open and accepted: no GIL release for large `update`,
no key zeroization, out-of-memory in scrypt aborts instead of raising, and
a few message differences from CPython.

## TLS verification

Fixed: `create_default_context(Purpose.CLIENT_AUTH)` loaded the system and
Mozilla roots and omitted `VERIFY_X509_STRICT` and `VERIFY_X509_PARTIAL_CHAIN`
(CPython trusts only what the caller loads); `ssl.match_hostname`, removed in
CPython 3.12, was exported with a lax wildcard matcher.

Open, in the externally maintained RTLS sources or by design:
- `verify_mode` and `check_hostname` setters can race and leave
  `CERT_NONE` with `check_hostname=True` (needs an application-level race).
- A trust anchor without `basicConstraints` or `keyUsage` is accepted as a CA
  unless `VERIFY_X509_STRICT` is set; default client contexts set it.
- Self-issued intermediates are not counted toward path length (RFC 5280).
- `SSLCertVerificationError` is not a `ValueError` subclass; it fails closed.

No fail-open path, hostname/SNI mismatch, resumption bypass, or fd ownership
defect was found in the traced code. Not traced in depth: `sni.rs`,
`ssl_metadata.py`, session-id detection in the session patch, cipher parsing.
