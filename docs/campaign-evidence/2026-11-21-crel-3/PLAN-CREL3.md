# Experiment C-REL-3: relocation-free Serc intern/codec string tables (preregistered 2026-11-21, before any build)

Observation: same-commit no-Serc control (stage-perf-jit-pystats @da74796) has 6,407
.data.rel.ro RELATIVE relocs / 48 pages vs 24,486 / 111 for perf-cloc-parent. The
.data.rel.ro reloc stride histogram has 16,142 16-byte (&str) entries. Serc links
generated string tables for every CPython target (3.12.14, 3.13.15, 3.14.5,
3.15.0rc2, 3.16.0a1): PREINTERNED/COMPILER/STARTUP/BOOTSTRAP/LINUX_PREINTERNED
`static X: &[&str]` in src/target/cpython_*.rs and eight codec `const` tables in each
src/target/cpython_*_source_codecs.rs (sample reloc targets such as "isdevdrive",
"SC_INT_MAX" occur only in these files). Each &str element is one relocated pointer,
written at load time on every process start, whether or not Serc compiles anything.

Mechanism (semantics-preserving representation change, all versions): each such
table becomes one `&'static str` blob of the concatenated elements plus a
`&[u32]` offset array (no pointers, no relocations), exposed as
`static X: LazyLock<Box<[&'static str]>>` built on first use. Callers keep slice
semantics through deref (`&X[..]`). Element order and contents are unchanged.
scope.rs SPELLINGS and all non-generated code are out of scope.

Parent: perf-cloc-parent (da74796, already built). Candidate: da74796 + one commit that
adds the transformed generated files under rust-cpython/serc-overlay/crates/serc/src/target/
(legacy versions as new overlay copies; 3.16 overlay files edited in place), plus the
minimal caller edits needed to compile. Independent of C-REL-2 (not stacked).

Predictions (all must hold):
P1 .data.rel.ro RELATIVE relocs fall >= 12,000 and their distinct pages fall >= 40.
P2 libpython r--p Private_Dirty median (residency_probe, 6 obs, parent->cand->parent)
   falls >= 128 KiB in .pyc mode and >= 96 KiB in source mode.
P3 compile parity 0 differences over parent Lib/; --serc-verify test_compile,
   test_marshal, test_dis, test_import pass; every table's element list in the
   candidate equals the parent's (generated-equality check over all tables).
Decision: P3 fails -> REJECT. P1 or P2 fails -> retire (attempt 1/1). All hold ->
prepared UNQUALIFIED candidate (track C slot 2 of 2). Landing would move the change
into the generators (scripts/generate_cpython_target.py,
generate_source_codec_interns.py, generate_legacy_target.py). Heap cost of the
lazily built 3.16 tables in source mode is not covered by the probe; report it as
unmeasured. No timing here.
