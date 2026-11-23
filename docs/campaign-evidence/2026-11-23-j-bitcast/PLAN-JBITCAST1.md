# J-BITCAST-1 plan: inline the unboxed bit casts in JIT stencils

Track J (JIT), attempt budget 1. Written before any candidate code or build.

## Finding (attribution only, `2026-11-23-jit-net-profile/`)

With setup subtracted, `memcpy` is 40% of nbody's kernel `Ir`, 30% of fft's and 40% of
sparse_mat_mult's. Callgrind puts almost all of it under JIT code, one level below
`_PyJIT_Entry`: 18.5 M calls in nbody and 59.9 M in sparse, about 34.6 `Ir` each.

The cause is in `pycore_stackref.h`: `PyStackRef_{From,As}Unboxed{Float,Int}` bit-cast through
`memcpy`. Stencils are compiled with `-fno-builtin`, so each cast becomes an out-of-line call
through the GOT. In `jit_stencils-x86_64-unknown-linux-musl.h` on `perf-calloc3-parent`,
every `memcpy` relocation is in an unboxed or box stencil (`_UNBOX_*`, `_BOX_*`,
`_BINARY_OP_*_UNBOXED*`, `_COMPARE_OP_*_UNBOXED`, `_BOX_UNBOXED_SLOTS`). The interpreter is
compiled with builtins, so its casts are already inline.

## Mechanism

The four helpers bit-cast through a union of the value type and `_PyStackRef` instead of
`memcpy`. The `static_assert`s on size stay. Nothing else changes. The change is in the lane
overlay only.

## Slot

J-TLS-1 is null on the musl lane: its code applies only to glibc. It therefore no longer holds
a prepared Track J slot on this lane. The slots are J-ES-2 and, if this holds, J-BITCAST-1.

## Stages

- Parent: `perf-calloc3-parent` (`a8c5e2a`). `main` has only documentation changes after it,
  so its cachegrind, setup-only and correctness receipts in `2026-11-23-c-alloc3/run/` are
  reused.
- Candidate: `perf-jbitcast1-cand` on `campaign/jbitcast1-cand`: musl malloc, JIT + Serc, no
  pystats, and the same recipe as C-ALLOC-3 (precompile, `musl-dbg`, two cachegrind runs per
  workload, two setup-only runs per kernel, 0.1% repeatability with one fresh pair).

## Predictions

Net kernel `Ir` is whole minus setup-only. Each prediction names the number it is judged on.

- B1, structural: the candidate's stencil header has zero `memcpy` relocations, against 108
  on the parent.
- B2, net kernel `Ir`: at least 30% lower on nbody and sparse_mat_mult, and at least 15%
  lower on scimark_fft. Expected: about −40% on nbody and sparse and −30% on fft. Whole `Ir`
  is reported for all three.
- B3, control: compile-corpus whole `Ir` within ±0.5%, since the corpus runs without the JIT.
- B4, structural: in one callgrind run of nbody and one of sparse on the candidate,
  `memcpy` calls whose immediate caller is JIT code are at least 99% fewer than on the
  parent (18.5 M and 59.9 M).
- Operation counts and digests are unchanged for every kernel.

## Correctness (any failure rejects)

- JIT fuzz, seed 0, 2,000 programs, no mismatches.
- The lane Rust JIT suite.
- `test_capi.test_opt`, with failure IDs equal to the parent's.
- `test_array`, `test_sys_settrace`, `test_monitoring` and `test_frame`.
- Compile parity and Alpine `ctypes.CDLL`.

## Decision

- Correctness fails: REJECT.
- Any of B1–B4 fails: retire.
- All hold: prepared unqualified Track J candidate. These are instruction counts; no timing
  claim.
