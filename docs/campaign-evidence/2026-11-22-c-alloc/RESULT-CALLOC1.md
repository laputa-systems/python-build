# C-ALLOC-1 result: retired (attempt 1/1) on memory

Plan: `PLAN-CALLOC1.md`. Parent `perf-calloc1-parent` at `bf6f4f4`; candidate
`perf-calloc1-cand` at `c411d42` (`campaign/calloc1-cand`). Both built with
`jit=False serc=True pystats=False`. Ubuntu 22.04, glibc 2.35, Valgrind 3.18.1.
The allocator is active: the candidate libpython reaches `mi_malloc_aligned` from 2,657
GOT-relative call sites and `mi_free` from 6,772.

| Prediction | Parent | Candidate | Result |
| --- | ---: | ---: | --- |
| A1 corpus `Ir` ≥ 4% lower | 37,015,593,392 / 37,016,080,576 | 33,852,857,890 / 33,852,799,476 | −8.54% (numeric pass) |
| A2 glibc malloc family self `Ir` ≥ 75% lower | 4,494,816,349 | 92,673,410 | −97.94% (numeric pass) |
| A3 fft / nbody / sparse within ±0.5% | — | — | −0.48% / **−0.62%** / −0.27%. The fft pairs (0.67%, 0.60%) fail the 0.1% repeatability rule, so the A1–A3 screen is invalid. |
| A4 private dirty memory, `.pyc` mode ≤ +100 KiB | 3,908 KiB | 3,916 KiB | +8 KiB (pass) |
| A4 private dirty memory, source mode ≤ +100 KiB | 6,028 KiB | 6,790 KiB | **+762 KiB (fail)**; source-x20 +1,498 KiB |
| A5 corpus peak RSS median ≤ +5% | 65,864 KiB | 73,728 KiB | **+11.94% (fail)** |

Correctness: no failures.
- `--serc-verify` `test_compile`, `test_marshal`, `test_dis` and `test_import` passed.
- The 7 listed modules had no failures on either stage.
- dlopen of the candidate from `/usr/bin/python3` succeeded.
- Compile parity found 1 differing digest out of 2,104, in
  `_sysconfigdata__linux_x86_64-linux-gnu.py`. That source file itself differs between
  the stages, and only by the stage path; after the path is normalised the two files are
  byte-identical. It is not a compiler difference.

Decision: A4 and A5 fail, so C-ALLOC-1 is retired at attempt 1/1. Moving the Rust
allocations to mimalloc removes 98% of the glibc allocator instructions and 8.5% of
compile-corpus instructions. In exchange, mimalloc keeps 0.75–1.5 MiB more private memory
after Serc compiles source, and corpus peak RSS rises by 12%. Any follow-up that bounds
mimalloc's retained memory (purge or arena options, or a per-compile arena) is a new
mechanism and needs its own plan.

Process deviation: the predictions were written after the stage builds and before any
measurement. The runbook asks for predictions before any build. No measurement preceded
the plan.

Receipts are in `receipts/`; raw files stay in `/home/ubuntu/exp-jit/calloc1/`.
