# J-ES-3 plan: loop-type proof through array stores and in-place integer ops

Track J. This is the one further loop-type attempt that the owner authorized on
2026-11-22, and its budget is 1/1. Written before any code. The track already holds two
prepared candidates (J-ES-2 and J-TLS-1). The owner authorized this attempt with the track
full, so a pass is recorded as prepared above the two-slot cap, and the owner decides the
slot.

## Evidence

The refusal census (`2026-11-21-j-es/ltcensus/closure.txt`) was taken before J-ES-2. J-ES-2
has since added array reads, the in-place float multiply and add ops, and
`_GUARD_NOS_OBJECT_TYPE_VERSION`. On that basis:

- `FFT_inverse` (line 45) lacks only `_STORE_SUBSCR_ARRAY_D`.
- The `FFT_transform_internal` traces (lines 192 and 243) also need
  `_BINARY_OP_MULTIPLY_INT_INPLACE_RIGHT`, `_BINARY_OP_ADD_INT_INPLACE` and in-place float
  subtract.
- `FFT_bitreverse` needs generic `_BINARY_OP`, which is out of scope.

## Change

Base: `main`. In the Rust loop-types planner, add these as accepted typed transfers:

- `_STORE_SUBSCR_ARRAY_D`: an `Int` index and a `Float` value into a guarded array; the
  array's element kind is unchanged.
- The int in-place ops: `_BINARY_OP_ADD_INT_INPLACE[_RIGHT]`,
  `_BINARY_OP_SUBTRACT_INT_INPLACE[_RIGHT]` and `_BINARY_OP_MULTIPLY_INT_INPLACE[_RIGHT]`,
  `Int × Int → Int`.
- Any in-place float subtract variants that J-ES-2 did not already add.

Each refuses on any other operand kind. Nothing else changes.

## Precondition (no build until it passes)

Replay the captured FFT and sparse planner inputs (the J-ES-2 fixture) through the parent
and candidate planners. The gate requires all of:

- the parent refuses `FFT_inverse`, and the candidate plans it;
- sparse is still planned;
- a store matrix refuses a non-`Int` index, a non-`Float` stored value and an unguarded
  container;
- an int-op matrix refuses non-`Int` operands.

Record which `FFT_transform_internal` traces plan.

## Stages

`perf-jes3-parent` (`main`) and `perf-jes3-cand`. Each is built with `--jit --pystats` for
the counters, and once more with `--jit` without pystats for cachegrind.

## Predictions

- P1: scimark_fft `_CHECK_PERIODIC` falls at least 20% vs parent.
- P2: scimark_fft cachegrind `Ir` falls at least 1%, with operation counts and digests
  unchanged.
- P3: no other JIT_SET kernel's `_CHECK_PERIODIC` or generic `_STORE_FAST_FUSED` rises by
  more than 1%. sparse and nbody `Ir` stay within ±0.5%.
- P4 correctness:
  - the `_jit_opt_rs` tests, including the new store and int-op matrices;
  - JIT fuzz seed 0, 2,000 programs, no mismatches;
  - `test_array`, `test_sys_settrace`, `test_monitoring` and `test_frame` pass;
  - the `test_capi.test_opt` JIT-only failure IDs equal the parent's.

Recorded but not decided on: `_BOX_FLOAT`, `_TIER2_RESUME_CHECK`, and the planned
transform traces.

## Decision

- The precondition fails: stop; no build.
- P4 fails: REJECT.
- P1, P2 or P3 fails: retire. This closes the loop-type extensions.

This is structural only; it makes no timing claim on this host.
