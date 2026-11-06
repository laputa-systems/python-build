# Qualification and packaging

One controlled product-process runner supplies a scrubbed environment,
working directory, import policy, and timeout for product checks. Regression
qualification uses process status and complete CPython test accounting;
crashes, timeouts, interruption, missing results, zero executed tests, and
incomplete runs fail. Retries are bounded diagnostics and cannot erase the
initial failure.

The mandatory aggregate verdict checks the final extracted archive. It
includes exact interpreter and ABI identity, module inventory and exclusions,
required native accelerators, binary architecture and dependency closure,
relocation, extension and ABI3 loading, shared-library embedding, TLS,
SQLite, compression, dbm, multiprocessing, ctypes, launchers, and removal of
builder paths. Linux ELF validation requires ET_DYN interpreters, a read-only
`PT_GNU_RELRO` segment with eager binding, non-executable stacks, no writable
executable load segments, and no text relocations. Every ELF `DT_NEEDED`
entry must be musl (`libc.musl-<arch>.so.1`) or a library resolved inside the
product through `$ORIGIN`; `libgcc_s` and glibc are rejected whether requested
or bundled. It also verifies stack protector and fortify evidence. macOS executable Mach-O images must carry
`MH_PIE`. Broad regression tests run on disposable pre-prune copies for all
three targets.

Packaging applies installed-tree fixes, runs regressions on a disposable copy,
strips and prunes the release tree, validates it, creates a deterministic
archive, extracts it under a path containing spaces, and validates those
exact bytes before publication. `artifact.json` is the canonical artifact
and qualification record. The archive and manifest publish together as one
immutable generation under `dist/<target>/generations/`, selected by an atomic
`dist/<target>/ACTIVE`; readers use `buildsys.generations.active_generation`
and never read loose files in `dist/<target>/`.
`SHA256SUMS` covers published
release assets. Failed work must not replace an accepted artifact or leave a
partial release.

Comparison reads completed artifacts and writes its own results. It requires
successful, complete probes for a match. Benchmarks use the shared harness
only when explicitly requested. Reproduction is opt-in and compares two
independent clean builds, including payload entry types, modes, symlink
targets, content, archives, and normalized Mach-O identity.

Two clean builds at the same path reproduce compiled content; the only
differences are Mach-O UUIDs and ad-hoc signatures. Builds at different paths
do not reproduce: the build directory is baked into sysconfigdata, pkg-config
files, `python-config`, `config.c`, `build-details.json`, and some extension
images. CI uses fixed run-directory names so equal-runner builds share a path;
the builder path still ships in the archive until a separate path-mapping
change removes it. The Linux x86-64 reproduction is open.

A uv install rewrites install names, which invalidates an ad-hoc signature and
kills the interpreter at launch. Images are signed linker-style so the result
survives that rewrite. The qualifier checks the installed bytes for this.

The final release additionally requires P1-P5 receipts, the Linux wave, a
fresh three-platform qualification at the release revision, exact uv-installed
archive checksums, source and tag identity, and no stale production claims.
See [ci-and-releases.md](ci-and-releases.md) and
[open-work.md](open-work.md).
