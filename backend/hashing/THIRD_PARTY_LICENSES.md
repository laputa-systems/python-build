# Third-party licenses

The extension uses RustCrypto digest, HMAC, PBKDF2, and scrypt crates. The
Python boundary uses `pyo3-ffi` directly and `pyo3-build-config` for extension
link arguments; it does not depend on PyO3's runtime or generated wrappers.
`Cargo.lock` pins every package version and registry checksum. License
expressions below come from Cargo metadata for those locked versions. The
dependency sources include the applicable license texts.

| Package | Version | License expression |
| --- | --- | --- |
| `blake2` | 0.11.0 | MIT OR Apache-2.0 |
| `block-buffer` | 0.12.1 | MIT OR Apache-2.0 |
| `cfg-if` | 1.0.5 | MIT OR Apache-2.0 |
| `cipher` | 0.5.2 | MIT OR Apache-2.0 |
| `cmov` | 0.5.4 | Apache-2.0 OR MIT |
| `cpufeatures` | 0.3.1 | MIT OR Apache-2.0 |
| `crypto-common` | 0.2.2 | MIT OR Apache-2.0 |
| `ctutils` | 0.4.8 | Apache-2.0 OR MIT |
| `digest` | 0.11.3 | MIT OR Apache-2.0 |
| `hmac` | 0.13.0 | MIT OR Apache-2.0 |
| `hybrid-array` | 0.4.15 | MIT OR Apache-2.0 |
| `inout` | 0.2.5 | MIT OR Apache-2.0 |
| `keccak` | 0.2.2 | Apache-2.0 OR MIT |
| `libc` | 0.2.191 | MIT OR Apache-2.0 |
| `md-5` | 0.11.0 | MIT OR Apache-2.0 |
| `pbkdf2` | 0.13.0 | MIT OR Apache-2.0 |
| `pyo3-build-config` | 0.30.1 | MIT OR Apache-2.0 |
| `pyo3-ffi` | 0.30.1 | MIT OR Apache-2.0 |
| `ripemd` | 0.2.0 | MIT OR Apache-2.0 |
| `salsa20` | 0.11.0 | MIT OR Apache-2.0 |
| `scrypt` | 0.12.0 | MIT OR Apache-2.0 |
| `sha1` | 0.11.0 | MIT OR Apache-2.0 |
| `sha2` | 0.11.1 | MIT OR Apache-2.0 |
| `sha3` | 0.12.0 | MIT OR Apache-2.0 |
| `sm3` | 0.5.0 | MIT OR Apache-2.0 |
| `sponge-cursor` | 0.1.0 | MIT OR Apache-2.0 |
| `target-lexicon` | 0.13.5 | Apache-2.0 WITH LLVM-exception |
| `typenum` | 1.20.1 | MIT OR Apache-2.0 |
