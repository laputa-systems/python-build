# C-ALLOC-3 plan: Serc's Rust allocations off musl mallocng

Track C (Serc). The owner authorized this as one attempt on the musl lane (2026-11-22, relayed
by Ada), so its budget is 1/1. It uses the mechanism and memory limits of C-ALLOC-2, with a
control that Serc cannot touch. Kernel-only instructions are reported next to totals. Written
before any candidate code or build.

## Why

On `perf-musl-base2`, musl mallocng (every function under musl's `src/malloc/`) is 5.05 G
`Ir`, 13.3% of the compile corpus (`2026-11-22-musl-profile/`). Serc makes about 14 M
`malloc`/`free` pairs over the corpus. Under glibc, C-ALLOC-2 cut corpus `Ir` 8.33% and met
both memory limits. It was retired only because its nbody control moved −0.83%: `jit_kernels`
compiles the kernel source through Serc in-process, so nbody was not independent of Serc.

## Mechanism (unchanged from C-ALLOC-2)

C-ALLOC-2's `GlobalAlloc`, which routes the Serc staticlib's Rust allocations to libpython's
bundled mimalloc, plus its one-time `mi_option_set` calls (`purge_delay=0`,
`eager_commit=0`, `allow_thp=0`) behind a non-allocating atomic flag. It is Linux and `serc`
feature only, and the option-enum pin test is kept. Ported onto current `main` with no other
change. The process allocator stays musl mallocng; pymalloc and libpython are unchanged.

## Stages

Built with `--jit --serc` and allocator `musl`, without pystats:
- parent `perf-calloc3-parent`, at `main` when this plan lands;
- candidate `perf-calloc3-cand`, the parent plus the port.

## Measurements

The retry recipe (`2026-11-22-musl-alloc/retry/`) runs in the pinned Alpine container, with
`musl-dbg` installed only for symbolization.

- **Cachegrind:** two runs of each of the corpus, nbody, sparse_mat_mult and scimark_fft.
- **Repeatability:** a pair must agree within 0.1%. If a pair misses, one fresh pair is rerun
  on both stages; a second miss makes that workload non-repeatable, and it fails.
- **Setup-only control:** for each kernel, two cachegrind runs of the same process up to and
  including `jit_kernels._verify_wheels()` and `_load(<module>)`, without running the
  kernel. Net kernel `Ir` = whole `Ir` − setup-only `Ir`, each the mean of its two runs.
- **Precompiled imports:** before any measurement, each stage's own interpreter runs
  `compileall` once over `benchmarks/` and the stage `lib/python3.16`, so imports load
  `.pyc`. The only Serc work left in a kernel process is `_load()`'s compile of the kernel
  source, which the setup-only run contains.
- **Memory:** A4 residency probe (`2026-11-22-probe2`), private-memory median of six
  observations in each of `.pyc` and source modes. A5 peak-RSS median over three
  `/usr/bin/time -f %M` corpus runs.

## Predictions (thresholds unchanged from C-ALLOC-1/2)

- A1: compile-corpus whole `Ir` at least 4% lower. Expected: −8% to −12%.
- A2: musl allocator-family self `Ir` on the corpus at least 75% lower. The family is
  every function whose `musl-dbg` source path is under `src/malloc/`.
- A3: nbody and sparse_mat_mult **net kernel** `Ir` within ±0.5%, with operation counts
  and digests unchanged. Whole `Ir` is reported for both, and fft is informational.
- A4: private-memory median at most +100 KiB, in both `.pyc` and source modes.
- A5: peak-RSS median at most +5%.

## Correctness (any failure rejects)

- Compile parity over the stage stdlib, with path-only `_sysconfigdata` differences
  normalised.
- `--serc-verify` on `test_compile`, `test_marshal`, `test_dis` and `test_import`.
- The 7 suites, with failure IDs equal to the parent's.
- Alpine `ctypes.CDLL` loading the stage libpython and calling `Py_GetVersion`.
- The four-thread compile smoke test.
- JIT fuzz, seed 0, 2,000 programs, no mismatches.

## Decision

- Correctness fails: REJECT.
- Any of A1–A5 fails: retire. This closes the Serc allocator mechanism.
- All hold: prepared unqualified candidate (Track C). These are instruction counts and
  memory on an unqualified host; no timing claim.
