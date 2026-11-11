# Third-party licenses

The extension uses RustCrypto digest, HMAC, PBKDF2, and scrypt crates
behind raw `cpython-sys` bindings (no PyO3). `Cargo.lock` pins every
package version and registry checksum. License expressions below come
from Cargo metadata for those locked versions. The dependency sources
include the applicable license texts.

| Package | Version | License expression |
| --- | --- | --- |
| `blake2` | 0.11.0 | MIT OR Apache-2.0 |
| `block-buffer` | 0.12.1 | MIT OR Apache-2.0 |
| `cfg-if` | 1.0.4 | MIT OR Apache-2.0 |
| `cipher` | 0.5.2 | MIT OR Apache-2.0 |
| `cmov` | 0.5.4 | Apache-2.0 OR MIT |
| `const-oid` | 0.10.3 | Apache-2.0 OR MIT |
| `cpufeatures` | 0.3.1 | MIT OR Apache-2.0 |
| `crypto-common` | 0.2.2 | MIT OR Apache-2.0 |
| `ctutils` | 0.4.2 | Apache-2.0 OR MIT |
| `digest` | 0.11.3 | MIT OR Apache-2.0 |
| `hmac` | 0.13.0 | MIT OR Apache-2.0 |
| `hybrid-array` | 0.4.15 | MIT OR Apache-2.0 |
| `inout` | 0.2.6 | MIT OR Apache-2.0 |
| `keccak` | 0.2.2 | Apache-2.0 OR MIT |
| `libc` | 0.2.191 | MIT OR Apache-2.0 |
| `md-5` | 0.11.0 | MIT OR Apache-2.0 |
| `pbkdf2` | 0.13.0 | MIT OR Apache-2.0 |
| `ripemd` | 0.2.0 | MIT OR Apache-2.0 |
| `salsa20` | 0.11.0 | MIT OR Apache-2.0 |
| `scrypt` | 0.12.0 | MIT OR Apache-2.0 |
| `sha1` | 0.11.0 | MIT OR Apache-2.0 |
| `sha2` | 0.11.1 | MIT OR Apache-2.0 |
| `sha3` | 0.12.0 | MIT OR Apache-2.0 |
| `sm3` | 0.5.0 | MIT OR Apache-2.0 |
| `sponge-cursor` | 0.1.0 | MIT OR Apache-2.0 |
| `typenum` | 1.20.1 | MIT OR Apache-2.0 |
| `zeroize` | 1.9.0 | Apache-2.0 OR MIT |
