# Experiment C-REL-2: drop Serc/parser panic Location detail (preregistered 2026-11-21, before any build)

Observation (reloc_attrib.py, reloc-sections.json, res-perf-rust-noserc.json): Serc adds
~20,955 R_X86_64_RELATIVE relocations into .data.rel.ro (24,483 vs 3,528 without Serc),
touching +92 pages; libpython r--p Private_Dirty is 564 KiB with Serc vs 196 KiB without
(+368 KiB per process, in .pyc and source mode alike). Stride histogram of those relocs:
16 B x 16,142 (&str pieces, mostly panic/assert message pieces), 24 B x 5,206 (panic
Location records: file &str + line + col; file strings are serc/crates/... paths).

Hypothesis: with -Zlocation-detail=none, every Location in the size-affected crates has
the same redacted file and zero line/col, so the records merge and their relocations
disappear, shrinking dirtied relro pages at load.

Parent: perf-cloc-parent (da74796, already built). Candidate: da74796 + one commit to
rust-cpython/overlay/Cargo.toml: `cargo-features = ["profile-rustflags"]` at the top and
`[profile.release.package.<p>] rustflags = ["-Zlocation-detail=none"]` for serc,
ruff_python_parser, ruff_python_ast, ruff_python_trivia, ruff_python_literal,
ruff_text_size, ruff_source_file, ruff_index. Global profile unchanged. Stage
perf-crel2-cand (`perf.py build --serc`).

Predictions (all must hold):
P1 .data.rel.ro RELATIVE relocs fall >= 4,000 vs parent (reloc-sections method).
P2 r--p Private_Dirty median (residency_probe, 6 obs) falls >= 64 KiB in BOTH .pyc and
   source modes, and the two modes' falls differ by <= 32 KiB (load-time mechanism).
P3 compile parity 0 differences over parent Lib/; --serc-verify test_compile,
   test_marshal, test_dis, test_import pass.
Decision: P3 fails -> REJECT. P1 or P2 fails -> retire (attempt 1/1). All hold ->
prepared UNQUALIFIED candidate; landing additionally needs owner acceptance of
redacted Serc panic locations (abort messages lose file:line). No timing here.
