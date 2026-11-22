# J-TLS-1: initial-exec TLS model for libpython's own thread-locals (preregistered 2026-11-22)

This is Status ranking item 2, "TLS access". Its Linux bucket was re-derived first, from
cachegrind instruction counts on `perf-ir-parent` (Ubuntu 22.04, glibc 2.35):

| Workload | `__tls_get_addr` + slow path + `_dl_update_slotinfo` share of instructions |
| --- | ---: |
| scimark_fft | 8.70% |
| nbody | 2.79% |
| sparse_mat_mult | 2.32% |
| stdlib compile corpus (C compiler; `perf-ir-jes2`, `serc: false`) | 9.27% |

FFT call-site census (callgrind, `tls-callers-fft.txt`): 5,746,230 calls from 486 callers.
The top 5 callers (`float_dealloc`, `_PyCompactLong_Add`, `long_dealloc`,
`_PyCompactLong_Multiply`, `_PyObject_Free`) account for 72.1%. Every call takes glibc's
slow path (`__tls_get_addr_slow` 5,597,052×).

The Status falsifier for the "pass tstate explicitly" mechanism needs ≥ 80% of calls from
≤ 5 call sites. The census gives 72.1%, so that mechanism is **dropped** as written.

**J-TLS-1 is a different mechanism.** It changes the TLS model of every libpython-owned
C thread-local, so all call sites change at once and no call site is edited. It does not
mirror interpreter state (eval 11), and it does not change JIT-emitted tstate loads (row 13).

## Change (lane overlay only; production is untouched)
Copy two files unchanged from the pinned source `e891a911` into `rust-cpython/overlay`,
then edit them:
1. `Include/pyport.h`: inside `#ifdef WITH_THREAD`, after `_Py_thread_local` is chosen,
   add the following. `_Py_thread_local` is used only by libpython-internal declarations;
   the `cpython/pystate.h` use is already guarded by `!Py_BUILD_CORE_MODULE`.

       #if defined(Py_BUILD_CORE) && !defined(Py_BUILD_CORE_MODULE) \
           && defined(__linux__) && (defined(__GNUC__) || defined(__clang__))
       #  undef _Py_thread_local
       #  define _Py_thread_local __thread __attribute__((tls_model("initial-exec")))
       #endif

2. `Include/internal/mimalloc/mimalloc/internal.h`: in the GCC/clang branch, change
   `mi_decl_thread` from `__thread` to
   `__thread __attribute__((tls_model("initial-exec")))` under `#if defined(__linux__)`.
   mimalloc is compiled only into libpython. Its own comment (prim.h:355) describes
   initial-exec as the intended model.

Rust `thread_local!` in the staticlib keeps its default model; it is not in the top callers.

## Builds
`perf.py build --jit`, without pystats:
- parent `perf-tls-parent` at `origin/main` `77d6a4d` (its `rust-cpython` code equals `db4ec7a`);
- candidate `perf-tls-cand` = parent + the change above, on branch `campaign/jtls1-cand`.

## Measurement recipe
This is `PLAN-JES2-IR.md` with Addenda A and B, unchanged: cachegrind `--cache-sim=no
--smc-check=all` on `-nodebug` copies, with every ELF file debug-stripped. libpython `.text`
SHA-256 must be equal before and after stripping. Two runs per (stage, workload); each run
pair must agree within 0.1%, or the screen is INVALID. Workloads:
- `scimark_fft`, `nbody`, `scimark_sparse_mat_mult`, run through `jit_kernels.run` from
  `wt-cloc-parent` with `PYTHON_JIT=1`;
- the compile corpus `compile_corpus.py compile <stage>/lib/python3.16`
  (`/home/ubuntu/exp-serc/ir-profile/compile_corpus.py`). Its digest must be equal across
  stages and runs; kernels must have equal `operation_count` and `digest` fields.

## Predictions (candidate vs parent, run 1)
- T1: fft `Ir` falls ≥ 5.0%.
- T2: nbody `Ir` falls ≥ 1.5%.
- T3: sparse `Ir` falls ≥ 1.0%.
- T4: compile-corpus `Ir` falls ≥ 5.0%.
- T5: static `call … __tls_get_addr` sites in the candidate libpython (`objdump -d`) fall
  from the parent's count (1,478 in `perf-ir-parent`) to ≤ 5% of it, and dynamic
  `__tls_get_addr` calls in FFT (callgrind) fall by ≥ 95%.

## Correctness (all must pass, or the candidate is rejected whatever T1–T5 show)
- Digest equality above.
- `test_threading`, `test_thread`, `test_embed`, `test_capi`, `test_interpreters` (or the
  pinned equivalents `test__interpreters`/`test_interpreters` that exist), `test_ctypes`
  and `test_sys`, with failure IDs equal to the parent's.
- Fuzz seed 0, 2,000 programs, with 0 mismatches.
- dlopen check: the host's `/usr/bin/python3 -c "import ctypes; ctypes.CDLL('<cand>/lib/libpython3.16.so.1.0', ctypes.RTLD_LOCAL)"`
  loads without a static-TLS error. Record `readelf -d` `FLAGS` (STATIC_TLS) for both builds.

## Decision rule
- A correctness failure rejects the candidate.
- If correctness passes but T1 or T4 fails, the candidate is retired.
- If all of T1–T5 and correctness hold, the candidate is prepared and unqualified. It is
  counted against J-track candidate slots, and a timing claim needs a qualified 24.04 host.

Host caveat: the slow path taken on every call may be specific to glibc 2.35. On the
glibc 2.39 qualified host, the parent's TLS cost may be smaller, and so would the timing gain.
