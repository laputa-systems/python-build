# musl lane instruction profile (perf-musl-base2)

Profile of the musl base stage, using cachegrind with Alpine `musl-dbg=1.2.6-r3` (pinned in
the lane lock under `measurement.debug`; it is installed only in the measurement container).
Totals match the retry run's base2 means within 0.005%.
Full top-25 tables are in `attribution.md`; `raw.sha256` covers this directory and
`/home/ubuntu/exp-musl-profile/`. Host: unqualified; these are instruction counts, not timing.

## Compile corpus (37.96 G Ir)

| Bucket | Ir | Share |
| --- | ---: | ---: |
| musl mallocng (`__libc_malloc_impl`, `get_meta`, `__libc_free`, `alloc_slot`, …) | 5.05 G | 13.3% |
| other musl (`memcpy` 3.3%, `memcmp` 1.1%, `strstr`, …) | 2.57 G | 6.8% |
| harness `hashlib.sha256` over marshalled output | 3.23 G | 8.5% |
| still `???:???` | 0.09 G | 0.2% |

This confirms the estimate in `RESULT-ALLOC-RETRY.md`: about 13% of corpus `Ir` is musl's
malloc. The largest Serc functions are `OptimizationPipeline::optimize` (3.4%) and
`LayoutAssembler::finish` (2.4%).

## JIT kernels: fixed harness overhead

On nbody (3.01 G Ir), 54% of `Ir` is setup, not kernel:
- `memcpy` is 21.5%.
- HACL `sha256_update` is 16.3% and Rust `sha2::compress256` is 15.9%.

The cause is `benchmarks/workloads/jit_kernels.py`. `_verify_wheels()` reads and SHA-256s
both pinned wheels once per process before every kernel run, and `jit_kernels.run()` is one
process per measurement. The overhead is the same in every stage, so:

- no recorded verdict changes, because every plan was preregistered on whole-program `Ir`;
- kernel effects are diluted about 2× on nbody, and less on FFT (4.33 G) and sparse (6.59 G);
- a ±0.5% control window on whole `Ir` is about ±1% of kernel work on nbody.

For future plans, record a setup-only control per kernel. It is the same process up to
`_load()`, without running the kernel. Report net kernel `Ir` (whole minus setup) beside
whole `Ir`, and state in the plan which one a prediction is judged on.

Callgrind confirmed 706 M nbody `Ir` in anonymous executable mappings (JIT code), most of
nbody's remaining unknown bucket (721 M). The FFT and sparse JIT subtotals were not
established.

## Serc callers on musl (`callers/attribution.md`)

This is a callgrind run with call graphs. Its total is 37.79 G, which is −0.45% against
cachegrind; the gap exceeds the 0.1% comparability gate, so this run is used only for attribution.

- **Allocation volume:** 13.97 M `malloc`, 13.97 M `free`, 0.83 M `calloc` and 1.58 M
  `realloc` over 2,104 files.
- **Allocation sources:** Vec growth (`RawVecInner::finish_grow`) is the largest immediate
  source at 1.29 G inclusive. The top functions two levels up are
  `OptimizationPipeline::optimize` (1.03 G), `Assembler::finish_code_with_constants` (1.02 G)
  and `LayoutAssembler::finish` (0.49 G). No single function owns more than 2.7% of corpus `Ir`.
- **`strstr`:** `_Py_CompileString` scans every source for `"\N{"` (`pythonrun.c:1745`),
  2,104 calls, 249 M (0.66%).
- **`serc::indentation::cpython_layout_error`:** called once per file (2,063 calls) on the
  normal path, 250 M inclusive. 205 M of that is allocation.

**Screen:** no single allocation site or scan reaches the 4% Serc bar. The `strstr` and
layout-error items together are about 1.2%. Reducing allocations across the three
functions above would mean restructuring the assembler's buffers in several places. The
one mechanism large enough to matter is routing Serc's allocations away from mallocng
(C-ALLOC-3), which waits on the owner.
