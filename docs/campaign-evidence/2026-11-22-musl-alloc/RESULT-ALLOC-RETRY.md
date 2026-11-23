# OD23 tuned allocator retry: result

Plan: `PLAN-ALLOC-RETRY.md` with Amendment A. Rules: `PLAN-ALLOC-SCREEN.md` S0–S2, unchanged.
Receipts: `retry/` (`raw.sha256`, 224 entries; large files under `/home/ubuntu/exp-musl-alloc-retry/`).
Stages: `perf-musl-base2` and `perf-musl-mi2` built at `975335b`; `perf-musl-je2` rebuilt at
`cdc2c68`, which differs only in the jemalloc configure flags (`retry/stage-provenance.txt`).

**Verdict: neither allocator passes. musl's malloc stays the lane allocator,** following the
owner's "One tuned retry" decision.

## CPU (S1): cachegrind `Ir`, mean of two runs, vs `base2`

| Workload | mimalloc low-retention | jemalloc low-retention |
| --- | ---: | ---: |
| Compile corpus | +4.511% | −5.142% |
| scimark_fft | +7.690% | −0.168% |
| nbody | +10.999% | −0.253% |
| sparse_mat_mult | +5.049% | −0.111% |
| Geometric mean (rule: ≤ −1%) | +7.032% FAIL | −1.442% pass |

All 12 pairs repeated within 0.1% (maximum 0.043%); no reruns were needed. Operation counts
and digests match across the stages. Allocator self-`Ir` on the compile corpus is 0.16%
(musl), 10.77% (mimalloc) and 6.49% (jemalloc).

## Memory (S2)

| Measure | base2 | mimalloc | jemalloc | Rule |
| --- | ---: | ---: | ---: | --- |
| A4 private, `.pyc` | 4,054 KiB | +622 KiB | +324 KiB | ≤ +1,024 KiB |
| A4 private, source | 4,500 KiB | **+2,126 KiB** | **+1,138 KiB** | ≤ +1,024 KiB |
| A5 peak RSS | 56,384 KiB | +3.02% | +0.57% | ≤ +10% |

## Correctness (S0)

- All three stages pass compile parity, `--serc-verify`, fuzz (seed 0, 2,000 programs), the
  four-thread compile and Alpine `ctypes.CDLL`.
- **mimalloc** adds one `test_capi` failure, `test_pyobject_freed_is_freed`, as in the first
  screen. That fails S0.
- **jemalloc** has no failure-ID difference from `base2`.

## Disposition

- **mimalloc:** fails S0, S1 and S2.
- **jemalloc:** passes S0 and S1, but fails S2: source-mode private memory is 114 KiB over
  the 1 MiB limit.
- The retry was the one authorized attempt. Allocator replacement is closed unless the
  owner reopens it.

**Unexplained:** jemalloc's compile-corpus delta moved from +0.61% in the first screen to
−5.14% here. The changes between the runs are the arm settings (`narenas:1`, zero decay,
global-dynamic TLS) and the base's dlopen-safe TLS. These are instruction counts on an
unqualified host, not timing.
