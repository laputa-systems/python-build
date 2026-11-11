# zlib codec dependency licenses

The DEFLATE codec uses the locked `libz-rs-sys` Rust crate, the C-API surface
of the `zlib-rs` port of zlib. Its enabled dependency closure is:

| Crate | Version | Declared license |
| --- | --- | --- |
| `libz-rs-sys` | 0.6.8 | Zlib |
| `zlib-rs` | 0.6.8 | Zlib |

Every crate version and registry checksum is pinned in the overlay
`Cargo.lock`. The Zlib license is permissive and compatible with the
project's licensing.
