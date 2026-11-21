# C-LOC-1: package-local size profile for Serc/parser (OD22 VM, 2026-11-21)

Preregistered in `PLAN.md` before any build. Parent `da74796` (integ/od22-reconcile);
candidate `6e5568c` (`campaign/recipes/od22/cloc-cand`, pushed): `opt-level = "s"`
for serc, ruff_python_parser, ruff_python_ast, ruff_python_trivia,
ruff_python_literal, ruff_text_size, ruff_source_file, ruff_index; global fat LTO,
codegen-units=1 and all other crates unchanged. Stages `perf-cloc-parent` /
`perf-cloc-cand`, both `perf.py build --serc`, built 552.8 s / 488.1 s, 58 Rust
extensions verified each. Host: provisional Ubuntu 22.04 VM (see ../2026-11-21-od22-vm).

| Prediction | Threshold | Parent | Candidate | Result |
| --- | --- | --- | --- | --- |
| P1 Serc+ruff `.text` | fall >= 15% | 6,880,857 B | 5,093,551 B (-25.97%) | pass |
| P1 C + rust_other `.text` | change <= 1% | 3,586,066 B | 3,552,451 B (-0.94%) | pass (rust_other alone -3.99%) |
| P2 source-mode exec libpython Pss (median of 6) | fall >= 0.5 MiB | 9,208 KiB | 7,868 KiB (-1,340) | pass |
| P2 `.pyc` control | change <= 0.1 MiB | 7,736 KiB | 6,972 KiB (-764) | **fail** |
| P3 compile parity over parent `Lib/` | 0 differences | 2,103 files | 2,103 files, 0 differ | pass |
| P3 `--serc-verify` test_compile/marshal/dis/import | pass | — | OK | pass |

Parent re-run after the candidate (`res-perf-cloc-parent-2.json`) reproduced
every median exactly. Whole `.text` 10,581,279 -> 8,713,855 B; `.eh_frame`
+35,944 B; `.rodata` -76,080 B; `.rela.dyn` +3,456 B.

**Decision: prediction failed; mechanism retired (attempt 1 of 1).** The
residency fall is not specific to source compilation: the no-compile control
fell 57% as much, so the declared locality mechanism is not shown. Compile-
attributable residency (source minus `.pyc`) fell 1,472 -> 896 KiB, recorded as
an observation only; it is not a reframed acceptance. No timing was run; smaller
text is structural evidence only. Next per runbook: cold error-path outlining.

Files: `PLAN.md`, the three probe scripts and `elf_attrib.py`, `elf-*.json` and
program headers, `res-*.json`, `ldd-*.txt`, `parity-*.json.xz`, build reports,
compressed build and test logs. `copies.sha256` covers every other file here.
