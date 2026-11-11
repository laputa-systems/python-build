# Rust TOML dependencies

The overlay Cargo lock pins these package versions and checksums:

| Crate | Version | Declared license |
| --- | --- | --- |
| `toml_parser` | `1.1.5+spec-1.1.0` | MIT OR Apache-2.0 |
| `toml_datetime` | `1.1.3+spec-1.1.0` | MIT OR Apache-2.0 |
| `winnow` | `1.0.4` | MIT |

The TOML crates are maintained in [toml-rs/toml](https://github.com/toml-rs/toml)
and `winnow` in [winnow-rs/winnow](https://github.com/winnow-rs/winnow). The
license declarations come from the corresponding crates.io packages. These
permissive licenses are compatible with the lane's PSF-2.0 CPython base.
`toml_parser` supplies the lexer, event parser, and scalar decoders;
`toml_datetime` validates date-time syntax; `winnow` is `toml_parser`'s parsing
library. The route builds Python objects directly from the parser events.
