# J-ES-2: loop-types proof with array reads and in-place float ops combined (preregistered 2026-11-22, before code)

Status: AUTHORIZED, NOT YET BUILT. On 2026-11-22 the owner authorized exactly one combined
activation experiment on the provisional host. This supersedes the earlier pause, but only
for this experiment; the larger escape-synced frame representation stays paused.

Precondition: the captured sparse and FFT planner inputs are replayed through the parent and
candidate Rust planners. The full build runs only if the parent refuses sparse, the candidate
plans it, FFT stays refused, and invalid index or operand kinds refuse.

This is one attempt beyond the 1/1 budgets of J-ES-1 (retired) and J-LT-2 (rejected), which
keep their own outcomes. A pass shows only activation and changed check/store behaviour. It
does not show allocation removal or speed, and it does not authorize the frame project.

Evidence: refusal census (`ltcensus/`, `ltcensus-closure.txt`) at `da74796`. sparse_mat_mult
`SparseCompRow_matmult` line 62 (trace len 66, `_JUMP_TO_TOP`) misses exactly
{`_BINARY_OP_SUBSCR_ARRAY_D`, `_BINARY_OP_SUBSCR_ARRAY_I`, `_GUARD_NOS_OBJECT_TYPE_VERSION`,
`_BINARY_OP_MULTIPLY_FLOAT_INPLACE`, `_BINARY_OP_ADD_FLOAT_INPLACE_RIGHT`} = J-ES-1 ∪ J-LT-2.
Every scimark_fft backedge trace also misses `_STORE_SUBSCR_ARRAY_D` (and the transform
traces int in-place ops), which this attempt does not add.

Change: parent `da74796` (no overlay/perf.py/tests drift to the integration head);
cherry-pick `7b15bee` (J-ES-1 transfer) and `1f45e7c` (J-LT-2 transfer) unchanged. Nothing
else in the planner. Guard-shape failure handled under the owner ruling of 2026-11-10: add
`test.test_capi.test_opt.TestUopsOptimization.test_float_truediv_speculative_guards_from_tracing`
to `perf.CLIMB_SUPERSEDED_UOP_ASSERTIONS`, valid only if (a) its numeric assertion passes and
(b) its sole failing assertions are `_GUARD_TOS_FLOAT`/`_GUARD_NOS_FLOAT`, which the
loop-types rewrite replaces with entry `_GUARD_LOCAL_FLOAT`; add a lane integration case
(same function) asserting JIT-on == JIT-off result and `_GUARD_LOCAL_FLOAT` present.
Build `perf.py build --name perf-jes2-pystats --jit --pystats`; collect all 16 JIT_SET kernels
with the parent `collect.sh` protocol (same args, same run count).

Parent counters (perf-jit-pystats @da74796):
sparse_mat_mult `_CHECK_PERIODIC` 5,183,996; generic `_STORE_FAST_FUSED` 5,178,962;
`_BOX_FLOAT` 4,991,982. scimark_fft 874,598 / 2,081,851 / 2,019,628.

Predictions:
P1 sparse_mat_mult: `_CHECK_PERIODIC` and generic `_STORE_FAST_FUSED` executions each fall
   >= 90% vs parent.
P2 scimark_fft (negative control from the census): `_CHECK_PERIODIC` and generic
   `_STORE_FAST_FUSED` each within ±1% of parent.
P3 no JIT_SET kernel's `_CHECK_PERIODIC` or generic `_STORE_FAST_FUSED` rises > 1%.
P4 correctness: `_jit_opt_rs` tests incl. a new sparse-shaped cycle test (array reads +
   in-place multiply/add accepted; refused on non-Int index and non-Float in-place operand);
   JIT fuzz seed 0 (2,000 programs) CORRECT; test_array, test_sys_settrace, test_monitoring,
   test_frame pass; test_capi.test_opt JIT-only failure IDs = parent's 19 plus at most the
   one listed superseded assertion meeting (a) and (b); new integration case passes.
Recorded, not decided on: `_BOX_FLOAT`, allocations, `_TIER2_RESUME_CHECK` per kernel.
Decision: P4 fails -> REJECT; P1, P2 or P3 fails -> retire (attempt 1/1, no further J-ES
loop-types extension). Structural only; no timing claim on this host.
