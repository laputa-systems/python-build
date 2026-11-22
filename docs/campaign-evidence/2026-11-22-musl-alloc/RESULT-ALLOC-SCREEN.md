# OD23 allocator screen result: no arm passes

Plan: `PLAN-ALLOC-SCREEN.md` (`e57f00b`). Stages `perf-musl-{base,mi,je}` at
`campaign/musl-lane` `3117002`, in the pinned Alpine container. All 12 Cachegrind pairs
repeated within 0.1%. Kernel operation counts and digests are identical across arms.

## S1 CPU (Cachegrind `Ir`, run 1 against base run 1)

| Workload | base | mimalloc 3.7.0 | jemalloc 5.4.0 |
| --- | ---: | ---: | ---: |
| compile corpus | 48,393,625,052 | +3.64% | +0.61% |
| fft | 4,327,878,256 | +2.04% | +0.11% |
| nbody | 3,059,356,368 | +2.87% | +0.17% |
| sparse_mat_mult | 6,627,646,003 | +1.33% | +0.08% |
| geometric mean | | **+2.47%** (fail) | **+0.24%** (fail) |

The rule required a mean at least 1% lower. Both replacements execute more instructions
than musl's mallocng here.

## S2 memory

| Measure | base | mimalloc | jemalloc | Limit |
| --- | ---: | ---: | ---: | --- |
| A4 private, `.pyc` (median KiB) | 3,916 | 6,268 (+2,352) **fail** | 4,364 (+448) | +1,024 |
| A4 private, source (median KiB) | 5,064 | 13,212 (+8,148) **fail** | 10,518 (+5,454) **fail** | +1,024 |
| A5 peak RSS (median KiB) | 54,888 | 80,108 (+45.9%) **fail** | 62,332 (+13.6%) **fail** | +10% |

## S0 correctness

- **Compile parity, Serc verification, fuzz and the threaded compile:** clean on both
  arms. That is: compile parity apart from path-only differences, `--serc-verify` on 4
  modules, fuzz seed 0 with 2,000 programs and 0 mismatches, and the four-thread compile.
- **The 7 suites:** jemalloc's failure IDs equal the base's. mimalloc adds one failure,
  `test_capi.test_mem.PyMemMallocDebugTests.test_pyobject_freed_is_freed` (**fail**).
- **`ctypes.CDLL` from Alpine `python3`:** fails on **all three arms, base included**:
  "initial-exec TLS resolves to dynamic definition". The lane libpython carries
  `DF_STATIC_TLS` and 13–14 `TPOFF` relocations, from the initial-exec TLS model of
  J-TLS-1 (PR #22) and C-TLS-2 (PR #23). musl refuses to dlopen such an object. This is
  a lane defect independent of the allocator, and it is handled under
  `PLAN-REBASELINE.md` (correctness on musl).

## Decision

No arm passes S0–S2: mimalloc fails S0, S1 and S2, and jemalloc fails S1 and S2. Under
the plan, the results go to the owner, and musl's native malloc is not selected by
default.

Caveat: Cachegrind counts user-space instructions only. Syscalls, page faults, lock
contention and cache misses are invisible to it, and those are where mallocng is known
to be weak. Timing on a qualified host could rank the arms differently. Raw receipts:
`receipts/` (small files) and `raw.sha256`. Profiles over 200 KiB are kept off-repo in
`/home/ubuntu/exp-musl-alloc/` and are covered by the same manifest.
