# Rust pattern-cache dependency licenses

The `_fnmatch_rs` bridge keeps compiled patterns in a hash table instead of an
ordered tree. The crate is `no_std`, so `rustc-hash` map aliases (std-gated) and
the `std` hash collections are unavailable; the cache uses `hashbrown` with its
default hasher, and only the features `default-hasher` is enabled. Both crates
were already pinned in the overlay `Cargo.lock`; adding the direct dependency
added `foldhash` and no other package:

| Crate | Version | Declared license |
| --- | --- | --- |
| `hashbrown` | 0.17.1 | MIT OR Apache-2.0 |
| `foldhash` | 0.2.0 | Zlib |

Every version and registry checksum is recorded in that lockfile. The selected
licenses are compatible with the lane's PSF-2.0 CPython base.
