# C-ALLOC-2 result: retired, A3 (nbody control) failed

Plan: `PLAN-CALLOC2.md`. Candidate `perf-calloc2-cand` at `5e96109` on
`campaign/calloc2-cand`, which carries two commits on top of C-ALLOC-1's `GlobalAlloc`:

- `520fcc9` sets the mimalloc options once: `purge_delay=0`, `eager_commit=0` and
  `allow_thp=0`. A build-time `_Static_assert` pins the option enum values.
- `5e96109` adds an acquire-load fast path so that later allocations skip the atomic
  read-modify-write.

Parent: `perf-ctls2-cand` (`04124a0`), reusing its C-TLS-2 runs.

| Workload | Parent run 1 / 2 | Candidate run 1 / 2 | Δ run 1 |
| --- | ---: | ---: | ---: |
| corpus | 35,702,028,574 / 35,701,679,129 | 32,728,672,951 / 32,727,496,210 | **−8.33%** |
| nbody | 2,493,943,211 / 2,493,933,347 (amendment A) | 2,473,332,713 / 2,473,416,689 | **−0.83%** |
| sparse | 5,713,597,741 / 5,713,429,178 | 5,698,936,697 / 5,698,970,927 | −0.26% |
| fft (info) | — | 3,814,209,154 / 3,813,919,839 | — |

All pairs are within 0.1%.

| Gate | Result | Limit | Verdict |
| --- | ---: | --- | --- |
| A1 corpus `Ir` | −8.33% | at least 4% lower | pass |
| A2 glibc allocator-family self `Ir` | 4.49 G → 0.093 G (−97.94%) | at least 75% lower | pass |
| A3 nbody | −0.83% | within ±0.5% | **fail** |
| A3 sparse | −0.26% | within ±0.5% | pass |
| A4 private memory, `.pyc` | +12 KiB | at most +100 KiB | pass |
| A4 private memory, source | −1,000 KiB | at most +100 KiB | pass |
| A5 corpus peak RSS | −5.09% | at most +5% | pass |

For A3, operation counts and digests are unchanged. The capped options fixed C-ALLOC-1's
memory failures: source-mode private memory went from +762 KiB to −1,000 KiB, and peak RSS
from +11.94% to −5.09%.

Correctness passed:
- Compile parity: 0 differences after path normalisation.
- `--serc-verify` (4 modules) passed.
- The 7 suites have no failures on either stage.
- dlopen succeeded, and the four-thread compile smoke test passed.

Outcome under the plan: A3 failed, so C-ALLOC-2 is retired. This was the single
authorized retry, which closes the allocator mechanism.

The nbody control moved *down*, beyond the control band. A plausible cause, not
verified: the benchmark's startup imports compile through Serc, so this control is not
independent of the mechanism. Re-admitting the candidate is the owner's decision. The
threshold is not reinterpreted here.
