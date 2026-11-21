# C-REL-3: relocation-free Serc target string tables (OD22 VM, 2026-11-21, structural only)

Plan preregistered in `PLAN-CREL3.md`. Parent `perf-cloc-parent` (`da74796`). Candidate
`ef48b04` (ref `campaign/recipes/od22/crel3-cand`; `67671b6` + cfg-attribute fix after a
first build failed on `#[cfg(test)]` tables). `blobify.py` rewrote 56 generated
`&[&str]` tables (all five CPython targets plus codecs, 18,420 elements) into a
`&str` blob + `&[u32]` offsets behind `LazyLock<Box<[&'static str]>>`; callers changed
only by `&X[..]`. `table_equality.py`: PASS for every table. Build 549.5 s.

| Prediction | Threshold | Parent | Candidate | Result |
|---|---:|---:|---:|---|
| P1 `.data.rel.ro` RELATIVE relocs / pages | fall >= 12,000 / >= 40 | 24,486 / 111 | 9,059 / 51 | pass (-15,427 / -60) |
| P2 `r--p` Private_Dirty .pyc / source | fall >= 128 / >= 96 KiB | 564 / 564 KiB | 320 / 320 KiB | pass (-244 / -244) |
| P3 parity; serc-verify; table equality | 0 diffs; pass; equal | 2,103 files | 0 diffs; 574 tests OK; 56 tables equal; serc `target::` 10 tests OK | pass |

Parent re-run reproduced every median. Side effects: `rw-p` Private_Dirty 488 -> 496 KiB,
`rw-p` Pss 492 -> 524 KiB; `r--p` Pss 3,572 -> 3,072 (.pyc) / 3,136 (source) KiB.
Heap for lazily built tables in source mode is not covered by the probe (unmeasured).

Outcome: prepared UNQUALIFIED candidate (track C slot 2 of 2). Not timed. Landing moves
the representation into Serc's generators instead of transformed overlay copies.

## Landing preparation (`8a57de4`)
`generate_cpython_target.py` and `generate_source_codec_interns.py` now emit the blob form
directly, with outer attributes on all three declarations. Checks:
- an emitter check (`emitter_check.py`) re-renders all 56 original tables to the `ef48b04`
  declarations (10/10 files PASS);
- `table_equality.py` PASS (56 tables, 18,420 elements);
- Serc `target::` 10 tests pass.

The generated files differ from `ef48b04` only by the removed transformer marker and
rustfmt layout. A full regeneration of 3.12–3.15 needs the pinned CPython sources, which
are not on this host. Regenerating 3.16 against the local tree changed only the
input-hash headers, which were restored.
