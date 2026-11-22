# J-ES-2 instruction-count screen: result

Recipe: `PLAN-JES2-IR.md`, including Addenda A and B. Valgrind 3.18.1 cachegrind with
`--cache-sim=no`. Parent `perf-ir-parent` (`da74796`) vs candidate `perf-ir-jes2`
(`0c3b192`), both JIT builds without pystats, run on debug-stripped copies. libpython
`.text` SHA-256 is identical before and after stripping: parent `5943f419…`, candidate
`c147a105…`. Ubuntu 22.04 provisional host. These are instruction counts, not timing.

| Kernel | Parent run 1 / 2 | J-ES-2 run 1 / 2 | Max pair delta | Candidate vs parent (run 1) | Prediction |
| --- | ---: | ---: | ---: | ---: | --- |
| sparse_mat_mult | 5,656,709,736 / 5,656,846,799 | 5,454,186,133 / 5,454,336,641 | 0.0028% | **−3.580%** (−202.5 M) | I1 ≤ −1.0%: held |
| fft | 4,139,068,310 / 4,139,310,376 | 4,138,974,904 / 4,138,968,535 | 0.0058% | −0.002% | I2 ±0.5%: held |
| nbody | 2,694,784,424 / 2,694,916,226 | 2,694,632,760 / 2,694,699,449 | 0.0049% | −0.006% | I3 ±0.5%: held |

Validity: every run pair is within 0.1%, so the screen is valid.

Printed-result check: the plan required the printed result to match across runs. The
literal stdout differs, because it includes the `elapsed_seconds` and `cpu_*_seconds`
fields. The computational fields match across all four runs of each kernel: operation
count and digest (sparse `2eebf23c…`, fft `605e8bed…`, nbody `3640da69…`). This is
recorded as a deviation in how the check was worded; the computation is not in question.

Where the sparse instructions went (`cg_diff`, run 1): `_Py_DeallocTstate` −119.9 M
(the generic local store's decref/dealloc of the replaced value) and JIT code (`???`)
−82.4 M. Nothing else moved by more than 0.1%.

Caveat: about 31% of the sparse workload's instructions are the harness's own digest work
(`sha256_*` plus `memcpy`), which both builds execute identically. Relative to the kernel
alone, the reduction is therefore larger than 3.58%. No kernel-only number is claimed.

Outcome: I1–I3 held. This supports the structural P1 result with an executed-instruction
reduction. It is not a timing result and does not qualify J-ES-2; that still needs a
qualified Ubuntu 24.04 host. Receipts are in `ir/`: totals, stdout, the compressed
`cg_diff`, build times, and a SHA-256 manifest of the raw `.cg` files (the raw files stay
in `/home/ubuntu/exp-jit/jes2-ir`).
