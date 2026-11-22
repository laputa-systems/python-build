# C-ALLOC-2 plan: mimalloc for the Serc staticlib, with retention capped

Track C (Serc). This is the one retry of C-ALLOC-1 that the owner authorized on
2026-11-22, and its attempt budget is 1/1. Written before any candidate code or build.

## Why the retry

C-ALLOC-1 (`c411d42`) routed the staticlib's Rust allocations to libpython's mimalloc
(2.5.2). It cut glibc allocator `Ir` by 97.9%, but failed A4 source (+762 KiB) and A5
(+11.94% peak RSS). This lane's default allocator is pymalloc: `sys._debugmallocstats()`
reports pymalloc size classes. So mimalloc serves only the Rust allocations, and its
process-wide options affect nothing else. The host's THP mode is `madvise`.

## Mechanism

Apply C-ALLOC-1's `GlobalAlloc` unchanged. Then, once, before the first mimalloc
allocation that goes through it (guarded by an atomic flag that does not allocate), call
`mi_option_set`:

| Option | Value | Effect |
| --- | --- | --- |
| `mi_option_purge_delay` | 0 | Purge freed pages immediately. |
| `mi_option_eager_commit` | 0 | Commit segment memory per page, on demand. |
| `mi_option_allow_thp` | 0 | Never use transparent huge pages. |

The option enum values are taken from the bundled `mimalloc.h`, with a test that pins
them. The change is Linux and `serc` feature only, as in C-ALLOC-1. Nothing else changes.

## Stages

- Parent: `perf-ctls2-cand` (`04124a0`; `main` at `f6383c4` has no code drift). Reuse its
  existing cachegrind runs, residency-probe baseline, compile-parity JSON and correctness
  results from C-TLS-2.
- Candidate: `perf-calloc2-cand` on `campaign/calloc2-cand`, built with
  `jit=False serc=True pystats=False`.

## Measurements

The C-ALLOC-1 recipe. Run fresh on the parent only what has no parent result yet: the
three `/usr/bin/time -f %M` corpus runs.

- Cachegrind: two runs each of the corpus, nbody and sparse; fft is informational.
- If a control pair misses the 0.1% repeatability rule, rerun one fresh pair on both
  stages, as in C-TLS-2 amendment A. If it misses again, that control is recorded as
  non-repeatable.

## Predictions (thresholds unchanged from C-ALLOC-1)

- A1: compile-corpus `Ir` at least 4% lower.
- A2: glibc allocator-family self `Ir` at least 75% lower.
- A3: nbody and sparse within ±0.5%, with operation counts and digests unchanged.
- A4: residency-probe private-memory median at most +100 KiB, in both `.pyc` and
  source modes.
- A5: peak-RSS median over three corpus runs at most +5%.

## Correctness (any failure rejects)

- Compile parity, with path-only `_sysconfigdata` differences normalised.
- `--serc-verify` on the 4 modules.
- The 7 suites, with failure IDs equal to the parent's.
- dlopen from `/usr/bin/python3`.
- The four-thread compile smoke test.

## Decision

- Correctness failure: REJECT.
- Any of A1–A5 fails: retire. This closes the allocator mechanism.
- All hold: prepared and unqualified, in Track C slot 2 of 2.
