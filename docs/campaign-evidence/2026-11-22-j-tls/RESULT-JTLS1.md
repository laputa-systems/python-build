# J-TLS-1 result: initial-exec TLS for libpython thread-locals

Plan: `PLAN-JTLS1.md`. Parent `perf-tls-parent` at `ba1bd12`; its `rust-cpython` is
identical to `5d90909`. Candidate `perf-tls-cand` at `4a4bf84` (`campaign/jtls1-cand`).
Both are JIT builds without pystats. Ubuntu 22.04, glibc 2.35, Valgrind 3.18.1.
Instruction counts only; this is not timing.

| Workload | Parent run 1 / 2 | Candidate run 1 / 2 | Max pair delta | Δ run 1 | Prediction |
| --- | ---: | ---: | ---: | ---: | --- |
| fft | 4,139,299,652 / 4,139,214,685 | 3,750,341,207 / 3,750,200,242 | 0.004% | **−9.40%** | T1 ≥ 5%: held |
| nbody | 2,695,031,828 / 2,694,879,278 | 2,614,077,991 / 2,613,811,652 | 0.010% | **−3.00%** | T2 ≥ 1.5%: held |
| sparse_mat_mult | 5,454,501,252 / 5,454,370,934 | 5,318,770,423 / 5,318,757,772 | 0.002% | **−2.49%** | T3 ≥ 1%: held |
| compile corpus (C compiler) | 54,474,755,635 / 54,482,285,916 | 49,085,350,191 / 49,090,114,938 | 0.014% | **−9.89%** | T4 ≥ 5%: held |

T5 held:
- Static `__tls_get_addr` call sites fell from 1,478 to 1. The remaining site is Rust
  `std::panicking::panic_with_hook`.
- Dynamic FFT calls fell from 5,746,231 to 0.

The libpython `.text` hash was equal before and after stripping in both stages. Kernel
`operation_count` and `digest` fields matched across all runs; the corpus digests matched
within each stage.

Correctness, all passed:
- `test_threading`, `test_thread`, `test_embed`, `test_capi`, `test__interpreters`,
  `test_interpreters`, `test_ctypes`, `test_sys`: 3,006 tests on each build. The failure
  IDs are identical (the same 20 pre-existing `test_capi.test_opt` IDs); there is no
  candidate-only failure.
- Fuzz seed 0: 2,000 programs, 0 mismatches, JIT coverage 0.9745.
- dlopen of the candidate libpython from the host `/usr/bin/python3` (ctypes, RTLD_LOCAL):
  no static-TLS error. `readelf -d` shows `FLAGS STATIC_TLS` on the candidate only.

Outcome: prepared and unqualified. J-TLS-1 takes the second J-track candidate slot, next
to J-ES-2, so the J track is now full. The every-call slow path seen in the parent may be
specific to glibc 2.35, so the timing effect on the glibc 2.39 qualified host is unknown
until it is measured there. Receipts are in `receipts/`; raw files stay in
`/home/ubuntu/exp-jit/jtls1/`.
