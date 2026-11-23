# J-BITCAST-1 result: holds

Plan: `PLAN-JBITCAST1.md` (`dc25c9f`), attempt 1/1. Parent `perf-calloc3-parent` at `a8c5e2a`,
candidate `perf-jbitcast1-cand` at `92a1111`. Both use musl malloc, JIT + Serc and no pystats.
Receipts: `run/` (`8a010e6`, `323f2f6`; `raw.sha256` covers in-tree files and the large files
under `/home/ubuntu/exp-calloc3/` and `/home/ubuntu/exp-jbitcast1/`). Every cachegrind pair,
including the setup-only pairs, repeated within 0.1%. These are instruction counts on an
unqualified host; none is a timing claim.

| Prediction | Parent | Candidate | Change | Rule | Verdict |
| --- | ---: | ---: | ---: | --- | --- |
| B1 stencil `memcpy` relocations | 96 | 0 | −96 | 0 | holds |
| B2 nbody net kernel `Ir` | 1,566,286,985 | 723,409,714 | −53.81% | ≤ −30% | holds |
| B2 sparse net kernel `Ir` | 5,129,794,585 | 2,454,217,284 | −52.16% | ≤ −30% | holds |
| B2 fft net kernel `Ir` | 2,871,550,955 | 1,731,805,730 | −39.69% | ≤ −15% | holds |
| B3 corpus whole `Ir` | 37,961,426,456 | 37,965,079,982 | +0.010% | ±0.5% | holds |
| B4 JIT-code `memcpy` calls, nbody | 18.5 M | 0 | −100% | ≤ −99% | holds |
| B4 JIT-code `memcpy` calls, sparse | 59.9 M | 0 | −100% | ≤ −99% | holds |

The whole-process changes are nbody −28.25%, fft −26.49% and sparse −40.78%. Setup-only
`Ir` moves at most 0.008%. Operation counts and digests match on every kernel.

The saving is larger than the `memcpy` body alone: 54% on nbody, against memcpy's 40% share
of the kernel. Each call also spilled its operand to the stack, went through the GOT, and
reloaded the result. The cast is now a register move (`_UNBOX_FLOAT_TOS_r11`: `movq
0x10(%rax), %rdi`, with no stack frame on the fast path).

## Correctness (all pass)

- **Fuzzing:** JIT fuzz seed 0, 2,000 programs: 0 mismatches. Added after the verdict on the same candidate stage: seeds 1 and 2, 2,000 programs each, 0 mismatches and 0 nondeterministic programs, JIT coverage 0.9775 and 0.9725 (`run/correctness/fuzz-seeds12/`).
- **Lane Rust JIT suite:** 120 unit and 2 integration tests pass on both stages. The first
  attempts, with a stage-local Cargo index, exited 101 for lack of `bindgen` and are kept in
  `correctness/rust-jit/`.
- **CPython suites:** `test_capi.test_opt` has the same 20 failure IDs on both stages.
  `test_array`, `test_sys_settrace`, `test_monitoring` and `test_frame` pass.
- **Compile parity and loading:** 2,104 entries; the only difference is a path inside
  `_sysconfigdata`, equal after normalisation. Alpine `ctypes.CDLL` passes.

## Decision

All of B1–B4 hold, and correctness is clean, so J-BITCAST-1 is a prepared unqualified Track J
candidate. It takes the slot that J-TLS-1, null on musl, freed. Timing still needs a qualified
host.

The same `memcpy` calls exist in the glibc build. glibc's small-copy `memcpy` is cheaper, so
the glibc-era J-ES-2 figures understated the unboxing gain there too.
