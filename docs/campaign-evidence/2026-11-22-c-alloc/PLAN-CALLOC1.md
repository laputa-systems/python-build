# C-ALLOC-1 plan: route the staticlib's Rust allocations to libpython's mimalloc

Track C (Serc). Attempt 1/1. Written after the builds and before any measurement.

## Observation

Callgrind of `compile_corpus.py compile` on `perf-crel3-gen-nodebug` (Serc, no JIT):
37,640,059,493 `Ir`; output `compile 2104 2093 e854b436…`.

Inclusive cost of the glibc entry points (`serc-compile-callers.txt.xz`):

| Entry | Inclusive | From C callers |
| --- | ---: | ---: |
| `malloc` | 5.25% | 0.22% |
| `free` | 4.30% | 0.06% |
| `realloc` | 2.45% | 0.42% |
| `calloc` | 1.11% | 0.43% |

The C callers are `_PyMem_Raw*`. Rust callers account for about 12.0% of the corpus. The
cpython-rust-staticlib installs no `#[global_allocator]`, so every staticlib crate (Serc,
ruff, the JIT planner, the Rust modules) uses glibc malloc.

## Mechanism

Commit `campaign/calloc1-cand` adds a `#[global_allocator]` to
`cpython-rust-staticlib/src/lib.rs`, gated on
`cfg(all(feature = "serc", target_os = "linux", not(test)))`. It forwards to
`mi_malloc_aligned`, `mi_zalloc_aligned`, `mi_realloc_aligned` and `mi_free` in the
mimalloc that libpython already links (`WITH_MIMALLOC 1`; the symbols are DSO-local).

- mimalloc is process-wide and needs no attached thread state, unlike
  `AttachedThreadAllocator`.
- No staticlib crate frees C-allocated memory, and no C code frees Rust memory:
  `_PySerc_ReleaseBuffer` releases Serc's `Box` in Rust.
- Non-Serc stages are unchanged.

## Stages

- Parent: `perf-calloc1-parent` at `bf6f4f4`.
- Candidate: `perf-calloc1-cand` at `campaign/calloc1-cand`.
- Both built with `jit=False serc=True pystats=False`, like `perf-crel3-gen`.

## Measurement recipe

The `PLAN-JES2-IR.md` recipe (Addendum B, all-ELF strip, `.text` hash check) on
`-nodebug` copies. Two cachegrind `--cache-sim=no --smc-check=all` runs per stage for each
of:
- the compile corpus;
- `scimark_fft`, `nbody` and `scimark_sparse_mat_mult`.

Every within-stage pair must be within 0.1%.

## Predictions (all must hold)

- A1: compile-corpus `Ir` falls by at least 4.0% (run 1 vs run 1).
- A2: candidate corpus self `Ir` in glibc's `malloc` family falls by at least 75% vs the
  parent. The family is `malloc`, `free`, `realloc`, `calloc`, `_int_malloc`, `_int_free`,
  `_int_realloc`, `malloc_consolidate` and `unlink_chunk.constprop.0`, read from
  `cg_annotate`.
- A3 (controls): fft, nbody and sparse each stay within ±0.5%, with operation counts and
  digests unchanged.
- A4 (memory): measured with `residency_probe2.py <stage-python> payload-argparse.py 6`
  (`2026-11-22-probe2/`), in `.pyc` and source modes. The process-private median rises by
  at most 100 KiB.
- A5 (memory): peak RSS of the compile corpus (`/usr/bin/time -f %M`, non-stripped stage,
  3 runs, median) rises by at most 5%.

## Correctness (any failure rejects)

- Compile parity: zero differing marshal digests over the 2,104-file stage
  `lib/python3.16`, using the procedure that produced `parity-perf-crel3-gen.json`.
- `perf.py test --serc-verify`: `test_compile`, `test_marshal`, `test_dis` and
  `test_import` pass.
- `test_threading`, `test_thread`, `test_embed`, `test_capi`, `test_sys`,
  `test__interpreters` and `test_interpreters` fail on exactly the same IDs as the parent.
- ctypes dlopen of the candidate libpython from `/usr/bin/python3` succeeds.

## Decision

- Correctness failure: REJECT.
- A1, A2 or A3 fails: retire (1/1).
- A4 or A5 fails: retire (1/1). The memory cost is recorded.
- All hold: prepared and unqualified, in Track C slot 1 of 2. Timing needs the qualified
  host, and production (musl) is unaffected.
