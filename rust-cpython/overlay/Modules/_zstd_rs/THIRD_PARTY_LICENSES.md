# Third-party license record

The Rust codec dependency closure is pinned in the overlay `Cargo.lock`.

| Package | Pinned version | Declared license |
| --- | --- | --- |
| `zstd` | 0.14.3 | BSD-3-Clause |
| `zstd-safe` | 8.0.0 | BSD-3-Clause |
| `zstd-sys` | 2.1.0+zstd.1.5.7 | BSD-3-Clause |
| `cc` | 1.5.1 | MIT OR Apache-2.0 |
| `find-msvc-tools` | 0.1.14 | MIT OR Apache-2.0 |
| `jobserver` | 0.1.35 | MIT OR Apache-2.0 |
| `libc` | 0.2.191 | MIT OR Apache-2.0 |
| `pkg-config` | 0.3.34 | MIT OR Apache-2.0 |
| `shlex` | 2.0.1 | MIT OR Apache-2.0 |
| `getrandom` | 0.4.3 | MIT OR Apache-2.0 |
| `r-efi` | 6.0.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later |

`getrandom` and `r-efi` are in the locked `jobserver` build-dependency tree;
`r-efi` is only selected for EFI targets outside this lane's macOS arm64 and
Linux x86-64 hosts. The pinned `zstd-sys` package includes the Zstandard C
source and its `zstd/LICENSE` BSD grant. These licenses are available in the
crate sources published at `https://crates.io/crates/<package>/<version>`.
