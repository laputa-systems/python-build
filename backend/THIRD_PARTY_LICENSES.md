# Third-party licenses

The prototype vendors the pinned RTLS source under `backend/vendor/rtls/`
and adds no production dependency. Its `rust/Cargo.lock` delta adds or changes
these packages:

| Package | Version | License expression |
| --- | --- | --- |
| `rustls-webpki` (locally adapted verified-path API) | 0.103.15 | ISC |
| `rustls` (session metadata accessors) | 0.23.45 | Apache-2.0 OR ISC OR MIT |
| `x509-parser` (signature-verification feature) | 0.16.0 | MIT OR Apache-2.0 |
| `ring` (RSA/SHA1 implicit-anchor verification) | 0.17.14 | Apache-2.0 AND ISC |
| RTLS source (`backend/vendor/rtls/`, commit `302423b49e494863d07630d50b15a9488ec85ef7`) | 2026.10.3 | MIT |
| `pyo3` (upgrade, ABI3-py315) | 0.30.1 | MIT OR Apache-2.0 |
| `rustls-graviola` | 0.4.0 | Apache-2.0 OR ISC OR MIT-0 |
| `graviola` | 0.4.1 | Apache-2.0 OR ISC OR MIT-0 |
| `getrandom` | 0.3.4 | MIT OR Apache-2.0 |
| `r-efi` | 5.3.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later |
| `wasip2` | 1.0.4+wasi-0.2.12 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `wit-bindgen` | 0.57.1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| `block-buffer` | 0.12.1 | MIT OR Apache-2.0 |
| `cpufeatures` | 0.3.1 | MIT OR Apache-2.0 |
| `crypto-common` | 0.2.2 | MIT OR Apache-2.0 |
| `digest` | 0.11.3 | MIT OR Apache-2.0 |
| `hybrid-array` | 0.4.15 | MIT OR Apache-2.0 |
| `sha1` | 0.11.0 | MIT OR Apache-2.0 |

The `getrandom`, `r-efi`, `wasip2`, and `wit-bindgen` entries are target
support dependencies recorded by Cargo. The lockfile also removes the AWS-LC
native build dependencies. The PyO3 upgrade also moves `cc` to 1.10.0 and
`find-msvc-tools` to 0.1.15; both remain MIT OR Apache-2.0. Exact registry
checksums remain in [`rtls-graviola.patch`](rtls-graviola.patch) and
[`rtls-pyo3-0.30.patch`](rtls-pyo3-0.30.patch).

The no-`_ssl` contract harness uses certificate and key fixtures from the
CPython 3.15.1 `Lib/test/certdata` directory, including its PSF-licensed test
CA key to generate a local expired leaf. These files are not copied into this
repository or the product.

`prepare_webpki.py` pins the original rustls-webpki 0.103.15 crate archive by
SHA-256. `webpki-policy.patch` adapts path-length counting and exposes existing
DNS validation to the policy layer. The extracted source retains its ISC
license notices. The CPython protocol-message enums in `ssl_metadata.py` are
covered by the Python Software Foundation license.

The x509-parser verification feature exercises the already locked Ring package
for RSA/SHA1 self-signatures when classifying implicit trust anchors. It adds a
dependency edge without adding a package version. SHA1 remains unavailable for
leaf, intermediate, and TLS signature verification.

The modern private-key policy removes the direct `md-5`, `des`, `aes`, `cbc`,
`cipher`, and `base64ct` dependencies and the PKCS#8 `3des` feature. The
`md-5` 0.10.6 and `des` 0.8.1 packages leave the resolved TLS dependency
closure. AES, CBC, cipher, and base64ct remain transitive dependencies of
modern PKCS#8 parsing and PBES2 encryption. No package versions or new
licenses are added by this removal. The separate hashing backend and
trusted-root SHA1 classification are unchanged.
