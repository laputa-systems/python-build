The `_ssl_rs` TLS engine builds on the pinned `rustls 0.23.45` and
`rustls-webpki 0.103.15` forks at
<https://github.com/laputa-systems/rustls> (rev `4610aea3ac06e966e2b05315a845c2fa51e69e2d`) and
<https://github.com/laputa-systems/webpki> (branch `laputa/0.103.15`, commit
`92337a51e6c87bc6dff66092b42070cbfb2a006f`). The forks carry the maintained
session-API and certificate-policy patches. The workspace resolves every
`rustls` and `rustls-webpki` requirement to these commits (see the workspace
`[patch.crates-io]`), so the engine, its Graviola provider, and its verifier
share one copy. `fetch` caches the two git checkouts; `build` consumes them
offline. No OpenSSL, AWS-LC, or C/C++ TLS source enters the host build
closure below.

Direct TLS dependencies (pinned in the overlay `Cargo.lock`):

| Package | Version | License expression |
| --- | --- | --- |
| `rustls` (laputa-systems fork, locally patched session API) | 0.23.45 | Apache-2.0 OR ISC OR MIT |
| `rustls-webpki` (laputa-systems fork, locally patched policy) | 0.103.15 | ISC |
| `rustls-graviola` | 0.4.0 | Apache-2.0 OR ISC OR MIT-0 |
| `graviola` (via `rustls-graviola`) | 0.4.5 | Apache-2.0 OR ISC OR MIT-0 |
| `rustls-pki-types` | 1.15.1 | MIT OR Apache-2.0 |
| `rustls-pemfile` | 2.2.0 | Apache-2.0 OR ISC OR MIT |
| `x509-parser` (signature-verification feature) | 0.18.1 | MIT OR Apache-2.0 |
| `ring` (via `x509-parser` verification) | 0.17.14 | Apache-2.0 AND ISC |
| `pkcs8` (encryption, pem, std) | 0.11.0 | Apache-2.0 OR MIT |
| `pem-rfc7468` (alloc) | 1.0.0 | Apache-2.0 OR MIT |
| `sha1` | 0.11.0 | MIT OR Apache-2.0 |

The fork trees retain their upstream license notices (`LICENSE-*`, `LICENSE`,
`NOTICE` where shipped).
`ring` and `untrusted` enter only through `x509-parser`'s verification
feature for RSA/SHA1 self-signed trust-anchor classification; SHA1 remains
unavailable for leaf, intermediate, and TLS signature verification.
