# OD23 musl rebaseline: result

Plan: `PLAN-REBASELINE.md`. The head is `H2 = 4472113`, and every stage uses musl malloc.
Receipts: `rebaseline/` (`raw.sha256`, 325 entries; large files under `/home/ubuntu/exp-musl-rebase/`).
All 12 cachegrind stage/workload pairs repeated within 0.1% (maximum 0.0049%).
These are instruction counts, structural counters and memory on an unqualified host;
none is a timing claim.

| Candidate | Result on musl | Disposition |
| --- | --- | --- |
| C-REL-3 (H2−CREL3 → H2) | **P1:** `.data.rel.ro` RELATIVE relocations 27,332 → 11,905 (−15,427; rule ≥12,000), pages 140 → 80 (−60; rule ≥40). **P2:** libpython `r--p` Private_Dirty 692 → 452 KiB (−240; rules ≥128 `.pyc`, ≥96 source), and the parent-after value repeats 692. **P3:** compile parity 2,103/0; table equality 56 tables, 18,420 elements; `--serc-verify` passes. | Holds; stays merged. |
| J-ES-2 (H2−JES2 → H2) | **P1:** sparse `_CHECK_PERIODIC` −96.37%, `_STORE_FAST_FUSED` −94.54%. **P2/P3:** FFT and the other 15 kernels unchanged. **I1–I3:** sparse `Ir` −2.981% (rule ≥1%); FFT −0.003% and nbody −0.009% (rule ±0.5%); compile corpus −0.010%. **P4:** fuzz clean; Rust suite passes; `test_capi` adds only the superseded guard-shape test, as on glibc, and `jit_loop_types_integration.py` passes. | Holds; stays merged. The glibc result is superseded. |
| J-TLS-1, C-TLS-2 | Their initial-exec TLS applies only to glibc since `ca3d710`, so a musl build has none of their code (`rebaseline/tls-model-noop.txt`). | Null on musl. Kept for glibc, per Ada's 2026-11-22 TLS direction, so no revert is proposed. |
| J-ES-3 (H2 → H2+JES3), attempt 1/1 | **P1:** FFT `_CHECK_PERIODIC` 874,598 → 776,199 (−11.25%; rule ≥20%): **fails**. **P2:** FFT `Ir` −0.067% (rule ≥1%): **fails**. **P3:** sparse −0.00004% and nbody +0.008% `Ir`; other kernels unchanged: holds. **P4:** Rust suite (125 tests) and the store/int-op matrices pass; fuzz clean; `test_array`, `test_sys_settrace`, `test_monitoring` and `test_frame` pass; `test_capi.test_opt` IDs equal H2's: holds. Replay: `FFT_inverse` now plans; `FFT_transform_internal` is still refused (`planner_refusal`). | **Retired** under its plan. This closes the loop-type extensions. |
