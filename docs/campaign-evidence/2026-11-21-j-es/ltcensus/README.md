# Loop-types refusal census (parent `da74796`, instrumented stage `perf-ltcensus`, evidence only)

One JSON line per `guard_loop_locals` call over one run of each of the 16 JIT_SET kernels.
267 calls, **0 rewritten**: the committed T3 loop-types proof (2026-11-12) does not fire
on any JIT_SET trace on this Linux build. 89 traces end in `_JUMP_TO_TOP` and reach the
planner; **0 of them use only supported uops** (`closure.txt`, supported set = the C
opcode table in `guard_loop_locals`).

- The trace closest to closed is sparse_mat_mult `SparseCompRow_matmult` line 62, which is
  missing exactly 5 uops: the J-ES-1 set (`_GUARD_NOS_OBJECT_TYPE_VERSION`,
  `_BINARY_OP_SUBSCR_ARRAY_{D,I}`) plus two J-LT-2 ops (`_BINARY_OP_MULTIPLY_FLOAT_INPLACE`,
  `_BINARY_OP_ADD_FLOAT_INPLACE_RIGHT`). Each single-mechanism attempt therefore could not
  move it. `FFT_inverse` needs the same plus `_STORE_SUBSCR_ARRAY_D`.
- Most traces need attribute access (`_GUARD_TYPE_VERSION` + `_LOAD_ATTR_INSTANCE_VALUE`,
  61–63 traces), inlined Python calls/returns (~50), or list iteration (26). None of these
  fit the planner's callback-free numeric model.
- Traces that start at `_TIER2_RESUME_CHECK` (side-exit roots) return `Err` at index 0;
  C leaves them unchanged.

Ceiling: any J-ES design that relies on a loop-types proof covers at most the sparse and
FFT array loops on JIT_SET.
