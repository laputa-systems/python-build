# bzip2 engine dependency license

CPython's `_bz2module.c` links the locked `libbz2-rs-sys` 0.2.6 Rust crate, a
port of bzip2 with the libbz2 C API, as a static library. The crate's own
`bzlib.h` is the build's header. It declares the bzip2-1.0.6 license, which
permits source and binary redistribution with its notice. The license text is
`LICENSE-bzip2-1.0.6` beside the private `_bz2_rs` route's sources. `Cargo.lock`
pins the crate version and archive checksum.
