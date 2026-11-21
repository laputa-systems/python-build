# J-ES-1: loop-types support for array subscripts (preregistered 2026-11-21, before any code)

Evidence (perf-jit-pystats @da74796, collect/*/stats-0.txt): scimark_sparse_mat_mult runs
5,183,996 `_CHECK_PERIODIC` and 5,178,962 generic `_STORE_FAST_FUSED` against 95,903
`_TIER2_RESUME_CHECK`: `plan_loop_types` (loop_types.rs) returns `None` on its inner loop
because `_BINARY_OP_SUBSCR_ARRAY_I`/`_D` (and their container type-version guards) reach
the final `else { return Ok(None) }`.

Change: add the array reads and the container type-version guard(s) found in the
pre-pass trace to `LocalOpcodes`/`_PyJitLocalOpcodes` (update the size static_assert).
Transfer: guard = no stack change; read `(array, sub -- res, ls, ss)` requires sub = Int,
container any kind, pushes Float (`_D`) or Int (`_I`), then Unknown, Int. Nothing else.
Parent `da74796`; build `perf.py build --name perf-jes1-pystats --jit --pystats`.

Predictions:
P1 sparse_mat_mult: `_CHECK_PERIODIC` and generic `_STORE_FAST_FUSED` executions each fall
   >= 90% vs parent.
P2 no JIT_SET kernel's `_CHECK_PERIODIC` or generic `_STORE_FAST_FUSED` count rises > 1%.
P3 correctness: new Rust unit tests (accepted D and I reads; refused non-Int index;
   refused unknown guard); existing _jit_opt_rs tests; lane JIT tests; JIT fuzz seed 0
   (2,000 programs) CORRECT; test_array, test_sys_settrace, test_monitoring, test_frame,
   test_capi.test_opt on the candidate.
Decision: P3 fails -> REJECT; P1 or P2 fails -> retire (attempt 1/1). Structural only.
