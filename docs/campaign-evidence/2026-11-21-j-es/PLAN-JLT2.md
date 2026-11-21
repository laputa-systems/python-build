# J-LT-2: loop-types proof accepts in-place float binaries (preregistered 2026-11-21, before code)

Evidence: instrumented J-ES-1 run (`jes1-diag/loop-types.log`) shows `plan_loop_types`
refusing sparse_mat_mult's inner loop at `_BINARY_OP_MULTIPLY_FLOAT_INPLACE`; the
transfer function knows only `_BINARY_OP_{ADD,SUBTRACT,MULTIPLY,TRUEDIV}_FLOAT`. Parent
pystats: in-place float uops execute on nbody 192,397, fft 1,548,991, lu 3,298,475, sor
549,895, spectral_norm 990,830, raytrace 332,633; nbody has 0 `_TIER2_RESUME_CHECK`.
Distinct mechanism from J-ES-1 (array reads); J-ES-1 stays retired and is not stacked.

Change (parent `da74796`): treat the eight `_BINARY_OP_*_FLOAT_INPLACE{,_RIGHT}` uops
exactly like the float binary they specialize, only if their stack effect equals the
non-in-place op's `(left, right -- res, l, r)`; otherwise stop. Rust tests: accepted cycle
with each in-place form; refusal when an operand is not Float.
Build `perf.py build --name perf-jlt2-pystats --jit --pystats`; collect all 16 kernels.

Predictions:
P1 at least 2 of {nbody, scimark_fft, spectral_norm, scimark_sor, scimark_lu} have
   `_CHECK_PERIODIC` executions fall >= 50% vs parent `perf-jit-pystats`.
P2 no JIT_SET kernel's `_CHECK_PERIODIC` or generic `_STORE_FAST_FUSED` rises > 1%.
P3 `_jit_opt_rs` tests; JIT fuzz seed 0 (2,000 programs) CORRECT; test_array,
   test_sys_settrace, test_monitoring, test_frame pass; test_capi.test_opt failure IDs
   equal to the parent control.
Decision: P3 fails -> REJECT; P1 or P2 fails -> retire (attempt 1/1). Structural only.
