# Rust dependency licenses

`_plistlib_rs` has no runtime crate dependency. Its only locked crates are
the build-time bindgen graph shared with `cpython-sys`, pinned by
`overlay/Cargo.lock`. Its registry packages declare these license expressions:

| Package | Version | License |
| --- | --- | --- |
| bindgen | 0.73.2 | BSD-3-Clause |
| bitflags | 2.13.1 | MIT OR Apache-2.0 |
| cexpr | 0.6.0 | Apache-2.0/MIT |
| cfg-if | 1.0.4 | MIT OR Apache-2.0 |
| clang-sys | 1.9.1 | Apache-2.0 |
| either | 1.17.0 | MIT OR Apache-2.0 |
| glob | 0.3.4 | MIT OR Apache-2.0 |
| libc | 0.2.191 | MIT OR Apache-2.0 |
| libloading | 0.8.9 | ISC |
| log | 0.4.35 | MIT OR Apache-2.0 |
| memchr | 2.8.3 | Unlicense OR MIT |
| minimal-lexical | 0.2.1 | MIT/Apache-2.0 |
| nom | 7.1.3 | MIT |
| prettyplease | 0.3.0 | MIT OR Apache-2.0 |
| proc-macro2 | 1.0.107 | MIT OR Apache-2.0 |
| quote | 1.0.47 | MIT OR Apache-2.0 |
| regex | 1.13.1 | MIT OR Apache-2.0 |
| regex-automata | 0.4.18 | MIT OR Apache-2.0 |
| regex-syntax | 0.8.11 | MIT OR Apache-2.0 |
| rustc-hash | 2.1.3 | Apache-2.0 OR MIT |
| shlex | 2.0.1 | MIT OR Apache-2.0 |
| syn | 3.0.10 | MIT OR Apache-2.0 |
| unicode-ident | 1.0.24 | (MIT OR Apache-2.0) AND Unicode-3.0 |

All declared licenses in the locked dependency graph are permissive and
compatible with this coverage overlay.
