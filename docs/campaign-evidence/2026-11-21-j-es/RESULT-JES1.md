# J-ES-1 result: retired (attempt 1/1)

Candidate `7b15bee` (ref `campaign/recipes/od22/jes1-cand`): `_GUARD_NOS_OBJECT_TYPE_VERSION`,
`_BINARY_OP_SUBSCR_ARRAY_D`/`_I` added to the loop-types opcode map and transfer function,
four Rust tests. Build `perf-jes1-pystats` 325.3 s.

- P1 **fail**: sparse_mat_mult `_CHECK_PERIODIC` 5,183,996 and generic `_STORE_FAST_FUSED`
  5,178,962 in both parent and candidate. All four counters are identical on all 16
  kernels (`summary-jes1.json`), so the planner still refuses the loop for a reason the
  pre-build trace reading did not find.
- P2 pass (no rises). P3 pass: `_jit_opt_rs` 118 tests; JIT fuzz seed 0, 2,000 programs,
  0 mismatches; test_array, test_sys_settrace, test_monitoring, test_frame OK;
  test_capi.test_opt shows 19 failures, the same IDs as the parent `perf-jit-pystats` control.

Retired as preregistered. The refusal cause is diagnosed separately; that diagnosis is
evidence only and does not reset this mechanism's budget.
