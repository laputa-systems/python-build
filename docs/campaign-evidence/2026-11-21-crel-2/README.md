# C-REL-2: redact Serc/parser panic locations (OD22 VM, 2026-11-21, structural only)

Plan preregistered in `PLAN-CREL2.md` before any build. Parent `perf-cloc-parent`
(`da74796`); candidate `4fe72f7` (ref `campaign/recipes/od22/crel2-cand`) adds
`cargo-features = ["profile-rustflags"]` and `rustflags = ["-Zlocation-detail=none"]`
for serc and the seven ruff parser crates only. Build 575.3 s, 58 extensions verified.

Motivation: Serc adds ~20,955 relative relocations to `.data.rel.ro` (24,483 vs 3,528
for `perf-rust` without Serc), and libpython `r--p` Private_Dirty is 564 KiB vs 196 KiB
(`res-perf-rust-noserc.json`): every Serc process dirties ~368 KiB of relro at load.

| Prediction | Threshold | Parent | Candidate | Result |
|---|---:|---:|---:|---|
| P1 `.data.rel.ro` RELATIVE relocs | fall >= 4,000 | 24,486 (111 pages) | 20,474 (89 pages), -4,012 | pass (margin 12) |
| P2 `r--p` Private_Dirty, .pyc / source | fall >= 64 KiB each, modes within 32 KiB | 564 / 564 KiB | 464 / 464 KiB | pass (-100 / -100) |
| P3 compile parity; serc-verify suites | 0 diffs; pass | 2,103 files | 0 diffs; 4 suites, 574 tests OK | pass |

Parent re-run after the candidate reproduced every median exactly. Incidental:
source-mode `r-xp` Pss 9,208 -> 9,056 KiB, .pyc 7,736 -> 7,712 KiB.

Outcome: prepared UNQUALIFIED candidate (1 of 2 allowed in track C). Not timed. Landing
needs owner acceptance that Serc/parser panic (abort) messages lose file:line:col.
Remaining Serc relro: ~16k 16-byte `&str` pieces (panic/assert message pieces), not
addressed by this mechanism.
