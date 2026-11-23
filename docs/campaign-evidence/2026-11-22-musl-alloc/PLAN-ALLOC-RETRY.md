# OD23 allocator screen: one tuned retry (preregistered)

Owner decision (2026-11-22 17:06 PST, relayed): "One tuned retry". Retry mimalloc and
jemalloc once each, with lower memory-retention settings, under the same rules as
`PLAN-ALLOC-SCREEN.md`. If neither passes, musl's malloc stays the lane allocator.

## Arms

All arms are built at the lane head after the PR #24 review fixes merge, so the reference
is rebuilt on that head too.

| Stage | Allocator | Retention settings |
| --- | --- | --- |
| `perf-musl-base2` | musl mallocng | reference |
| `perf-musl-mi2` | mimalloc v3.7.0 | `-DMI_DEFAULT_ARENA_EAGER_COMMIT=0`, `-DMI_DEFAULT_PAGE_FULL_RETAIN=0`; one lane patch to `src/util/options.c` that changes the `purge_delay` default from 1000 to 0 |
| `perf-musl-je2` | jemalloc 5.4.0 | `--with-malloc-conf=dirty_decay_ms:0,muzzy_decay_ms:0,narenas:1` |

- Every other setting matches the first screen.
- The settings are compile-time defaults inside the static archive. No environment
  variables are involved.
- Each stage's identity records them as `allocator_config`.
- The runtime-allocation proof must also show that the effective settings are active:
  `mi_option_get` for mimalloc, and `mallctl("opt.dirty_decay_ms")` and `opt.narenas` for
  jemalloc.

## Rules (unchanged from the screen)

- **S0, correctness:** compile parity, `--serc-verify` on 4 modules, the 7 suites (failure
  IDs no worse than the reference), fuzz seed 0 with 2,000 programs, the four-thread
  compile, and Alpine `ctypes.CDLL`.
- **S1, CPU:** across the compile corpus, fft, nbody and sparse_mat_mult, the geometric
  mean of Cachegrind `Ir` must be at least 1% below the reference. No single workload may
  be more than 1% above it. Each pair must repeat within 0.1%; a pair that misses gets one
  fresh pair.
- **S2, memory:** A4 private memory (`.pyc` and source medians) may be at most 1 MiB above
  the reference, and A5 peak RSS at most 10% above it.
- **Decision:** if one arm passes, it becomes the lane allocator. If both pass, the owner
  picks. If neither passes, `musl` stays. There are no further attempts.

## Prediction

The retry targets S2. S1 failed in the first screen for both arms (geometric mean +2.47%
and +0.24%), and the retention settings do not reduce user-space instructions. Immediate
purging adds `madvise` calls, which Cachegrind counts only as the user-side instructions
around the syscalls. Prediction: both arms fail S1 again, and jemalloc may pass S2. The
retry is still run in full, as directed.
