# Experiment C-LOC-1: package-local size profile for Serc/parser (preregistered 2026-11-21, before any build)

Hypothesis: Serc's in-process compile touches a large share of Serc/parser text
(cloud probe: Serc-only executable libpython PSS 2.50 -> 5.99 MiB from .pyc to -c source).
Optimizing only Serc/parser packages for size shrinks that code and the pages a
source compile makes resident, without changing compiler output.

Parent: integ/od22-reconcile HEAD at build time (record SHA). Candidate: parent + one
commit adding to rust-cpython/overlay/Cargo.toml, after [profile.release]:
  [profile.release.package.<p>] opt-level = "s"  for p in
  serc, ruff_python_parser, ruff_python_ast, ruff_python_trivia, ruff_python_literal,
  ruff_text_size, ruff_source_file, ruff_index
Global lto="fat", codegen-units=1, panic="abort" and every other crate unchanged.
Stages (both `perf.py build --serc`): perf-cloc-parent, perf-cloc-cand, separate worktrees.

Predictions (all must hold):
P1 structural: serc+ruff .text bytes (elf_attrib.py) fall >= 15% vs parent; C + rust_other
   .text changes <= 1%.
P2 locality: residency_probe.py, 6 fresh-subprocess observations per stage and mode:
   source-mode median executable-libpython Pss falls >= 0.5 MiB vs parent; .pyc-mode
   (control) median changes <= 0.1 MiB.
P3 semantics: compile_parity.py over the stage's Lib/ tree: every marshalled code object
   from compile(source, path, "exec") is byte-identical between parent and candidate;
   candidate passes `perf.py test --name perf-cloc-cand --serc-verify --suite test_compile
   --suite test_marshal --suite test_dis --suite test_import`.
Decision: P3 fails -> REJECT. P1 or P2 fails -> retire "package-local size profile"
(attempt 1 of 1 for this mechanism); next: cold error-path outlining. All hold ->
prepared, UNQUALIFIED Track C candidate; timing (G5-style C3 set) deferred until a
qualified host. Smaller text is structural evidence only, never acceptance.
