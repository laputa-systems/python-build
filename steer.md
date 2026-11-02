# JIT attribution campaign

Find where JIT-set CPU time goes in the `rust-cpython` lane's JIT builds,
split by trace and by uop. Then land at most one optimization that the
measured ranking picks. Work on a branch from `campaign/jit` at `212822d`.
`docs/CAMPAIGN-JIT.md` on that branch holds the lane host, the timing
evidence rule, the verdict policy (PD3), and the debugging methods. All of
them apply unchanged.

## Why attribution comes first

Every ranking from the last campaign counts executed uops, not cycles. Native
sampling failed on Linux, and nobody recorded why. On macOS,
`perf.py profile --tool sample` shows JIT code as unnamed addresses. The one
optimization that was gated, the J8 register allocator, removed 8.0% of spill
uops. That came to about 0.6% of executed uops, below the 1% floor, so it was
too small to measure and says nothing about the backend in general. Uop share
also overstates cheap stencils: `_SET_IP` is about one store and
`_CHECK_VALIDITY` a load and a branch.

## Owner decisions

- **OD4: memory gates stay as written.** JIT against no-JIT keeps its REJECT
  on peak RSS as a recorded cost. Gate each optimization against
  `@incumbent-jit`, where J8 showed memory within budget. Nobody sets a
  JIT-specific RSS budget in this campaign.
- **OD5: no unboxing and no speculative tier.** Cross-uop float or int
  unboxing, escape analysis, and a new tier stay out of scope. They come
  back only through a new owner decision after J2.
- **OD6: `212822d` gets measured, then kept or reverted.** J0 decides.

## J0: measure the compact-integer fast path

`212822d` adds a fast path to `rust-cpython/overlay/Tools/jit/template.c`.
It is compiled only for Apple arm64 non-debug GIL builds. It reuses the
mechanism that the float freelist probe in `rust-cpython/ENGINEERING.md`
dropped at a 0.993 CPU ratio.

1. On a quiet Mac, run `perf.py build --jit --name perf-jit-base` in a clean
   worktree at `212822d^`.
2. Run `perf.py build --jit --name perf-jit-compact` in a clean worktree at
   `212822d`.
3. Run `perf.py bench --baseline perf-jit-base --candidate perf-jit-compact
   --jit-set --gate`.
4. If the verdict is not ACCEPT, revert `212822d` and record the ratios in
   `rust-cpython/ENGINEERING.md`.

**Accept:** a recorded verdict, and a branch that either keeps or reverts the
commit to match it.

## J1: attribute JIT code to traces and uops

Upstream 3.16.0a1 already publishes JIT code to `perf`, but only under
`-X perf_jit`. `_PyJit_RegisterCode` in `Python/jit_publish.c` names every
executor `jit:executor`, and plain `-X perf` publishes no JIT code. The
per-uop offsets already exist: `_PyJIT_Compile` in `Python/jit.c` fills
`state.instruction_starts`.

1. Linux: run one `perf record` attempt in the lane container. Record the
   exact error and its cause, such as `perf_event_paranoid`, a seccomp
   denial, or a jitdump file that `perf inject --jit` cannot see. Fix the
   cause, or park Linux sampling with the cause written down.
2. Linux: through the overlay, publish one jitdump entry per uop. Name each
   entry after its trace (code object qualname, start offset, executor
   index) and its uop name.
3. macOS: through the overlay, write each executor's address range and
   per-uop offsets to a file named by `PYTHON_JIT_MAP` when that variable is
   set. Add a `perf.py profile` step that joins `sample` addresses against
   the file offline.
4. Add tests that compile one known trace and check the published names and
   ranges against `instruction_starts`.

**Accept:** for `nbody`, `float`, `richards`, `spectral_norm`, and
`scimark_lu`, at least 90% of the samples that land in JIT memory map to a
named trace and uop, on each platform J1 did not park.

## J2: rank costs by measured CPU

Rebuild the cost table from J1 samples. Rank by each uop's and runtime
call's share of kernel CPU. Keep the pystats counts beside it as context.

**Accept:** a table of at most 10 rows in the campaign doc. Each row cites
the command that produced it. The row for float allocation and deallocation
appears even when it does not rank, so the owner can weigh OD5.

## J3: one optimization

Take the top J2 row that can be implemented in the tier-2 optimizer, the
uop definitions, or the stencil templates. The J8 constraints from the last
campaign apply. Gate it with `perf.py bench --baseline @incumbent-jit
--candidate <build> --jit-set --gate`.

**Accept:** ACCEPT against `@incumbent-jit`, with the failed-test set and
digests matching the JIT-off sibling (PD8). Revert anything else and record
its ratios.

## J4: explain the macOS CPU and wall gap

The macOS gates reported a wall ratio of 0.749 against a CPU ratio of 0.870
for upstream JIT, and 0.747 against 0.883 for the Rust overlay. A
single-threaded kernel should not split like that. Find out what each
metric covers in `perf.py`. Then rerun the two comparisons with five
interleaved runs.

**Accept:** a stated cause backed by a measurement, and the five-run table
in the campaign doc.

## Boundary

The execution boundary from `docs/CAMPAIGN-JIT.md` holds, including no
pushes, the locked LLVM, and no `perf_verdict.py` thresholds. Each new
overlay file under `rust-cpython/overlay/Python/` is a contract change for
the owner to review. Linux arm64 and production builds stay out of scope.
