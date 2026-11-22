# J-ES-2 result: predictions held (structural; not timed)

Candidate: `campaign/jes2-cand` `0c3b192`, which contains the planner (`bdf86ef` and
`46940f3`), the replay gate (`2c107c9`) and the integration test (`0c3b192`). The parent is
the J-ES-1/J-LT-2 parent stage `perf-jit-pystats`. Host: Ubuntu 22.04, provisional. Stage
`perf-jes2-pystats` built in 385 s. Evidence is in `jes2-run/`.

## Pre-build replay gate (passed)
Exact planner inputs were captured from the real traces: sparse at source line 172, and
FFT_inverse at line 368. The census label "line 62" was not a source line.

| Trace | Parent planner | Candidate planner |
| --- | --- | --- |
| Sparse | refuses (`_GUARD_NOS_OBJECT_TYPE_VERSION`) | 68-step plan |
| FFT_inverse | refuses | refuses (`_STORE_SUBSCR_ARRAY_D`) |
| FFT_transform_internal | refuses | refuses (`_BINARY_OP_MULTIPLY_INT_INPLACE_RIGHT`) |

The invalid-type matrix ran 68 cases on each planner, with 0 failures. Its rules:
- **(a) Unused locals:** flips leave the plan identical.
- **(b) Locals feeding an index or arithmetic** (derived by stack provenance): flips must
  refuse, or keep the original stack type guard with the local boxed.
- **(c) Other used locals:** guards follow the flipped kind.
- **(d) Opcode mutations:** turning an array-double read into an int read, or an int read
  into a double read, refuses.

Rule (b) was widened once, before the commit, after one case failed the first version.
Seeding the sparse accumulator as unknown still yields a plan, but that plan keeps
`_GUARD_NOS_FLOAT` at uop 57 and leaves local 11 boxed (`plan-local11.txt`). That is the
same runtime check as upstream, so a non-float value side-exits. Both the first and the
widened rule are recorded in the replay logs.

## Predictions

| | Parent | Candidate | Result |
| --- | ---: | ---: | --- |
| P1 sparse `_CHECK_PERIODIC` | 5,183,996 | 187,997 (−96.4%) | pass (≥90%) |
| P1 sparse generic `_STORE_FAST_FUSED` | 5,178,962 | 282,883 (−94.5%) | pass |
| P2 FFT `_CHECK_PERIODIC` / `_STORE_FAST_FUSED` | 874,598 / 2,081,851 | identical | pass (±1%) |
| P3 max rise, other kernels | | +0.0% | pass |

**P4 correctness:** passed.
- Cargo: 120 unit tests and 2 replay tests pass.
- Fuzz: seed 0, 2,000 programs, 0 mismatches.
- `test_array`, `test_sys_settrace`, `test_monitoring` and `test_frame`: 1,655 tests run, 0 failures.
- `test_capi.test_opt`: the parent's 19 failures plus exactly one,
  `test_float_truediv_speculative_guards_from_tracing`. Its numeric assertion passes; it
  fails on its `_GUARD_TOS_FLOAT` assertion because the loop is now guarded by
  `_GUARD_LOCAL_FLOAT` at loop entry. The new integration case asserts both the result and
  the `_GUARD_LOCAL_FLOAT`, so the test is listed in `CLIMB_SUPERSEDED_UOP_ASSERTIONS` under
  the 2026-11-10 ruling.
- The integration test covers the sparse-shaped loop, with signal, GC-callback and
  cross-thread `f_locals` replacement, plus profile and trace. It passes. The first attempt
  failed with a harness `NameError`, recorded in `integration-first-attempt.log.xz`.

**Recorded, not decided on:**
- `_BOX_FLOAT` is unchanged in sparse and FFT, so no allocation was removed.
- Sparse `_TIER2_RESUME_CHECK` rose by 4,995,999: the per-iteration check is now the
  resume check rather than `_CHECK_PERIODIC`.

## Outcome
All of P1–P4 held, so J-ES-2 is a prepared unqualified candidate, the JIT track's only one.
Whether it is faster is unknown: it needs a qualified host. The larger escape-synced frame
representation, which would actually unbox the accumulator, stays paused.
