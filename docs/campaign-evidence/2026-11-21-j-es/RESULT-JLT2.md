# J-LT-2 result: REJECT (attempt 1/1)

Candidate `1f45e7c` (ref `campaign/recipes/od22/jlt2-cand`, parent `da74796`): the eight
`_BINARY_OP_*_FLOAT_INPLACE{,_RIGHT}` uops are accepted as float binaries in `plan_loop_types`.
Stack effects match the plain ops (checked before code).

- P1 fail: `_CHECK_PERIODIC`, `_TIER2_RESUME_CHECK`, generic `_STORE_FAST_FUSED`, `_BOX_FLOAT`
  unchanged on all 16 kernels (`summary-jlt2.json`, `counters-jlt2.txt`).
- P3 fail: `test_capi.test_opt` adds `test_float_truediv_speculative_guards_from_tracing`
  (trace-shape assertion expects `_GUARD_TOS_FLOAT`; the numeric assertion passes) to the
  parent's 19 failures. JIT fuzz seed 0: 2,000 programs, 0 mismatches; test_array,
  test_sys_settrace, test_monitoring, test_frame pass.

Lesson: the planner gives up at the first unknown uop, so a one-op extension cannot be
predicted from one trace. Next is a refusal census of every backedge trace on the parent
(evidence only) before any further loop-types hypothesis.
