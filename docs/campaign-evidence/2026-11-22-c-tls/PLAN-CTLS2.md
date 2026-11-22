# C-TLS-2 plan: initial-exec TLS for the staticlib's Rust thread-locals

Track C (Serc). Attempt 1/1. Written before any candidate code or build.

## Observation

J-TLS-1 (`bf6f4f4`) moved libpython's C thread-locals to initial-exec. In a Serc
(`jit=False serc=True`) stage at `bf6f4f4` (`perf-calloc1-parent`, run 1), compile-corpus
self `Ir` is still spent in glibc's dynamic TLS path: `_dl_update_slotinfo` 619.2 M,
`update_get_addr` 270.9 M and `__tls_get_addr` 232.2 M. That is about 1.12 G, or 3.0%
of 37.02 G. The callers are Rust (`serc` AST visitors and other staticlib crates). PIC
Rust code uses the global-dynamic TLS model.

## Mechanism

Build only the cpython-rust-staticlib's Rust code (its crates and the `build-std` std)
with `-Z tls-model=initial-exec` on Linux, lane only. Extension-module cdylibs are
unchanged.

## Stages

- Parent: the existing `perf-calloc1-parent` (`bf6f4f4`) and its existing
  `-nodebug` copy, plus that copy's corpus, nbody and sparse cachegrind runs from
  `/home/ubuntu/exp-jit/calloc1/ir/` (same stage, same recipe).
- Candidate: `perf-ctls2-cand` at `campaign/ctls2-cand`, built with
  `jit=False serc=True pystats=False`.

## Measurement recipe

The `PLAN-JES2-IR.md` recipe, Addendum B. Two cachegrind runs on the candidate for each
of the corpus, nbody and sparse. Every within-stage pair must be within 0.1%. fft is run
and reported for information only: Serc-stage fft pairs were not repeatable (0.60–0.67%,
`RESULT-CALLOC1.md`).

## Predictions (all must hold)

- S1: compile-corpus `Ir` falls by at least 2.0%.
- S2: corpus self `Ir` in `__tls_get_addr`, `__tls_get_addr_slow`,
  `_dl_update_slotinfo` and `update_get_addr` falls by at least 90%.
- S3: nbody and sparse each stay within ±0.5%, with operation counts and digests
  unchanged.
- S4: static `call … <__tls_get_addr@plt>` sites in libpython fall to at most 5% of the
  parent's count.
- S5 (memory): `residency_probe2.py` with 6 observations; the process-private median
  rises by at most 100 KiB in `.pyc` and source modes.

## Correctness (any failure rejects)

- Compile parity over the 2,104-file stage library, with path-only `_sysconfigdata`
  differences normalised.
- `--serc-verify` `test_compile`, `test_marshal`, `test_dis` and `test_import` pass.
- `test_threading`, `test_thread`, `test_embed`, `test_capi`, `test_sys`,
  `test__interpreters` and `test_interpreters` fail on exactly the same IDs as the
  parent.
- ctypes dlopen of the candidate libpython from `/usr/bin/python3` succeeds, with no
  static-TLS error. Record the `readelf -d` FLAGS and the size of the libpython
  `PT_TLS` segment.

## Decision

- Correctness failure: REJECT.
- S1–S5 fails: retire (1/1).
- All hold: prepared and unqualified, in Track C slot 1 of 2.
