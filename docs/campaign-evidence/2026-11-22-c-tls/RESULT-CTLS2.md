# C-TLS-2 result: all predictions held after amendment A; prepared, unqualified

Plan: `PLAN-CTLS2.md`. Candidate `perf-ctls2-cand` at `04124a0` (`campaign/ctls2-cand`):
one Linux-guarded line in the overlay `Makefile.pre.in` that appends
`-Z tls-model=initial-exec` to the staticlib cargo recipe's target rustflags. The build
log shows the flag on the staticlib cargo command and not on the cdylib `rustc`
commands. Parent: `perf-calloc1-parent` (`bf6f4f4`), reusing its existing runs.

| Workload | Parent run 1 / 2 | Candidate run 1 / 2 | Candidate pair | Δ run 1 |
| --- | ---: | ---: | ---: | ---: |
| corpus | 37,015,593,392 / 37,016,080,576 | 35,702,028,574 / 35,701,679,129 | 0.001% | **−3.55%** |
| nbody | 2,493,941,125 / 2,494,025,455 | 2,513,460,196 / 2,487,896,299 | **1.017%** | +0.78% |
| sparse | 5,719,762,677 / 5,719,865,190 | 5,713,597,741 / 5,713,429,178 | 0.003% | −0.11% |
| fft (info) | — | 3,828,470,162 / 3,828,513,364 | 0.001% | — |

- S1 corpus at least 2% lower: −3.55%, numeric pass.
- S2 TLS-function self `Ir` at least 90% lower: 1,238,441,152 → 0, pass.
- S3 nbody/sparse within ±0.5%: sparse passes; nbody fails numerically (+0.78%), and its
  candidate pair fails the 0.1% repeatability rule.
- S4 `__tls_get_addr@plt` sites: 688 → 0, pass.
- S5 private memory: `.pyc` −4 KiB, source −20 KiB, pass. `PT_TLS` MemSiz is
  unchanged (0xb8), and FLAGS is `STATIC_TLS` on both stages.

Correctness passed:
- Compile parity: 0 differences after normalising the path-only `_sysconfigdata`
  difference.
- `--serc-verify` (4 modules) passed.
- The 7 suites have no failures on either stage.
- dlopen from `/usr/bin/python3` succeeded.

Because the nbody pair is invalid, the screen is not valid as run. Neither retirement nor
preparation follows from it.

## Amendment A (written before any further run)

Run one fresh nbody cachegrind pair on each of the parent and candidate `-nodebug`
stages. If both pairs are within 0.1%, S3 is judged on run 1 of the fresh pairs. If
either pair fails again, nbody is recorded as non-repeatable on this host and C-TLS-2 is
recorded as inconclusive until the qualified host. Nothing else is rerun, and the
thresholds are unchanged.

## Amendment A result

| Stage | nbody run 1 / 2 | Pair |
| --- | ---: | ---: |
| Parent | 2,493,943,211 / 2,493,933,347 | 0.0004% |
| Candidate | 2,487,567,024 / 2,487,626,814 | 0.0024% |

Both pairs are valid. Candidate vs parent on run 1 is −0.26%, within ±0.5%, so S3 holds;
operation counts and digests are unchanged. One earlier parent attempt failed before the
workload loaded because it ran from the wrong working directory (`benchmarks` was not
importable). It is excluded, and its files are kept as `parent-nbody-1.*`.

Outcome: S1–S5 and correctness hold. C-TLS-2 is prepared and unqualified, in Track C
slot 1 of 2. Timing needs the qualified host.
