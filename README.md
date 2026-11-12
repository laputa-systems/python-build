# python-build

python-build builds a relocatable, fully OpenSSL-free CPython 3.15.1
distribution from locked sources for:

- `aarch64-apple-darwin` (Apple Silicon, macOS 27)
- `x86_64-unknown-linux-musl`
- `aarch64-unknown-linux-musl`

It keeps the GIL, shared libpython, private native dependencies, ThinLTO, and
useful extension development metadata. PGO is deferred. No glibc enters any
artifact: Linux binaries use the musl loader, with musl statically linked into
the Rust extensions. The exact components and CPU baselines are in
[docs/product-targets.md](docs/product-targets.md).

The distribution includes CPython 3.15.1, its standard library, SQLite with
FTS3/4/5, geopoly, rtree, dbstat, and loadable extensions, and multithreaded
`compression.zstd`. It excludes pip, ensurepip, venv, Tcl/Tk, `_gdbm`,
regression tests, bytecode caches, and `sampling-profiler` static web assets.

## Install

Install through uv, the consumer of the release assets. Each release,
tagged `release-<commit>`, carries python-build-standalone-compatible
archives named
`cpython-<version>+<date>-<target>-install_only_stripped.tar.gz`, plus
`download-metadata.json`, which `uv python install` reads through
`--python-downloads-json-url`. `<date>` is the UTC build date. The
smoke-tested archive bytes are exactly the published bytes: the release
workflow installs them through a local uv mirror into fresh directories before
it publishes.

Every release also carries the standard build-repository assets for each
target: `cpython-<version>-<target>.tar.xz`, the same installation recompressed,
and `cpython-<version>-<target>.manifest.json` with the source revision and the
SHA-256 of `sources.lock.json`. A `SHA256SUMS` file covers every other asset.
The assembly and publication contract is in
[docs/ci-and-releases.md](docs/ci-and-releases.md).

Building from source requires the locked toolchain and, on Linux, Docker
BuildKit. Run `make build TARGET=<triple>` (`fetch` and `qualify`),
`make verify`, `make package`, `make repro`, and `make clean`; `TARGET`
defaults to the host's triple. The commands, inputs, build records, and
isolation boundaries are in [docs/build-system.md](docs/build-system.md); the
final-byte qualification and publication contract is in
[docs/qualification-and-packaging.md](docs/qualification-and-packaging.md).

## TLS trust

TLS and hashing run on the RTLS/rustls-graviola backend with a TLS 1.2/1.3-only
policy. OpenSSL source, linkage, and symbols are absent from the payload. The
default TLS context tries configured platform trust sources first, then the
packaged Mozilla CA bundle. Set `PYTHON_BUILD_NO_DEFAULT_CA_BUNDLE=1` to
disable that fallback. The bundle is pinned at package time and does not
update at runtime; there is no online revocation checking. The backend design
and per-architecture evidence are in [backend/README.md](backend/README.md).

## Help

Report build, packaging, or runtime problems in the repository's issue
tracker: <https://github.com/laputa-systems/python-build/issues>.

## License

CPython and the bundled native dependencies keep their own licenses; the
notices and source-acquisition details installed with each artifact are
recorded in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). This
repository does not carry a separate top-level LICENSE file.
