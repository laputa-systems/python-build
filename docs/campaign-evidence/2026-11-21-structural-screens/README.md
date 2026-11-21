# Structural screens after C-LOC-1 (OD22 VM, 2026-11-21, diagnostic only)

## C-OUT-0: cold error-path outlining in Serc (not prototyped)

`cold_stubs.py` on `perf-cloc-parent` libpython (`cold-stubs-parent.json`): the 60
largest `serc::`/`ruff_` functions hold 2,438,066 B of text. Straight-line stubs ending
in panic/assert/bounds/alloc-failure calls are 2,664 sites and 60,964 B (2.5%); only
10,874 B (0.45%) lie in the first half of their function, so LLVM already sinks them.
Formatting and `CompileError` construction calls in these functions are near zero.
Serc source: 1,038 `assert_eq!`, 575 `assert!`, 796 `unwrap()`, 353 `expect(`, 2 `#[cold]`.
Ceiling is far below the 15% structural bar used for C-LOC-1: screened out, no
prototype, no attempt consumed. Not measured: drop glue on `?` error returns.

## Track J allocation/materialization (Linux structural evidence)

`perf-jit-pystats` (`build --jit --pystats`, parent `da74796`, 328 s). `collect.sh`
ran each of the 16 `JIT_SET` training kernels once with `PYTHONSTATS=1` and
`PYTHON_JIT_MAP`; `jit_analyze.py` -> `summary.json`; raw stats and maps in
`collect.tar.xz`. Instrumented counts, not timing receipts or a speed ranking.

Freelist allocations track boxing: sparse_mat_mult 10,542,345 (`_BOX_FLOAT`
4,991,982 + `_ITER_NEXT_RANGE` 5,178,962), nbody 3,093,043 (`_BOX_FLOAT`
1,623,973), fft 6,185,652, spectral_norm 3,047,772. Hot sparse_mat_mult trace:
`... _BINARY_OP_MULTIPLY_FLOAT_UNBOXED _GUARD_NOS_FLOAT _UNBOX_FLOAT_NOS
_BINARY_OP_ADD_FLOAT_UNBOXED _BOX_FLOAT _STORE_FAST_FUSED_FLOAT _JUMP_TO_TOP`, with
`_CHECK_PERIODIC` at the loop head; the range index is also boxed into a local
each iteration.

Selected shape: a loop-carried float accumulator (and range index) in a local.
This is the mechanism dropped on 2026-11-10 ("Unboxed loop-carried locals",
0.0% achievable: every store-to-next-store interval crosses a barrier). Linux
evidence confirms the barrier (`_CHECK_PERIODIC` each iteration) and adds no new
premise, so it is **not re-attempted**. Removing this traffic needs the
escape-synced frame-representation project (materialize locals on the
eval-breaker slow path, at exits, tracing/monitoring, `f_locals`/`locals()`,
tracebacks and GC traversal), which is a scope decision, not a bounded prototype.
