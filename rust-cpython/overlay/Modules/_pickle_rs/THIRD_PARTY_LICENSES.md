# Third-party license record

The `_pickle_rs` codec dependency closure is pinned in the overlay
`Cargo.lock`:

| Crate | Version | Declared license |
| --- | --- | --- |
| `deser` | `0.10.2` | Apache-2.0 |
| `deser-core` | `0.10.2` | Apache-2.0 |
| `deser-pickle` | `0.10.2` | Apache-2.0 |

Each declared license has a permissive grant compatible with this isolated
PSF-2.0 lane. `deser-core` declares optional dependencies (`bigdecimal`,
`indexmap`, `jiff`, `num-bigint`, `rust_decimal`) that appear in the lock
closure but are not enabled by `_pickle_rs` and are not compiled into it.
