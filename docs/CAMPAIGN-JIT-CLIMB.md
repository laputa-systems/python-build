# JIT hill-climb campaign

This campaign drives the `rust-cpython` lane JIT on macOS arm64 toward a
3-4x kernel speedup over the lane's own interpreter. It runs unattended as a
loop: measure, pick the mechanism with the largest measured ceiling, build a
candidate, gate it, keep it or discard it, and repeat. Every rule below is
written so that an agent can apply it without asking. The owner steps in only
at the escalations listed in **Stop and escalate**.

`docs/CAMPAIGN-JIT.md` holds the history, the lane host, the debugging methods,
and the attribution tooling (`--jit-map`). This document supersedes its
attribution J3 goal ("one optimization, 10% CPU") and its OD5.

## Handoff (2026-11-02)

P0 is done. The loop has not started.

- **Done, P0.1 (`a7d0de5`):** kernel-region CPU. Training and held-out
  kernels report `getrusage` deltas over their timed loop. Process CPU
  stays recorded as `process_total_seconds`. On host 3.15, nbody's kernel
  CPU is 0.070 s against 0.18 s for the whole process.
- **Done, P0.2 (`7d62ac3`):** eight held-out kernels, `--jit-heldout`, and
  the profile refusal.
- **Done, P0.3:** `rust-cpython/tests/jit_fuzz.py` and
  `rust-cpython/tests/test_jit_fuzz.py`.
  - The committed fuzzer ran every batch with `-I`, which makes CPython
    ignore `PYTHON_JIT`; all three sides ran with the JIT on, so the
    comparison was never differential. It now runs `-s -P` with a minimal
    environment and passes `PYTHON_JIT` explicitly.
  - The leak oracle compares pymalloc growth over the loop's last fifth,
    after the tracing and monitoring windows, and allows the JIT side at
    most `LEAK_BLOCKS` (1,000) beyond the interpreter side. A per-side
    threshold was calibrated against each program's own retention and
    flipped on a 3-block difference. Two JIT-off runs of the same batch
    differ by a few blocks, so that comparison allows `LEAK_NOISE_BLOCKS`
    (100).
  - Monitoring line sets are not compared across the JIT. The JIT reports
    LINE events only at side exits, so enabling monitoring mid-loop sees
    fewer lines than the interpreter. `perf-upstream-jit` behaves
    identically, so this is upstream JIT behavior, not lane drift.
    Monitoring lines are still compared between the two JIT-off runs.
  - On `perf-rust-jit`, seeds 0-2 with 2,000 programs each gave 0
    mismatches, 0 nondeterministic programs, and 97.3-97.8% JIT coverage.
    Each run takes about 28 s.
  - Detection proof: a worktree under `.work/wt/` with a
    `rust-cpython/overlay/Tools/jit/template.c` whose `PyFloat_FromDouble`
    macro calls a static helper that `Py_XINCREF`s the result reported
    1,370 mismatches in seed 0, all leak excesses above the 1,000-block
    allowance (up to 20,000). The worktree was discarded.
  - Lessons from building the oracle:
    - `python -I` implies `-E`, which silently discards `PYTHON_JIT`. Any
      harness that selects the JIT through the environment must not pass
      `-I`.
    - LINE and trace event counts at `set_events` boundaries vary between
      identical runs, even on pristine upstream with `PYTHON_JIT=0`. The
      fuzzer compares line sets instead.
    - Monitoring is process-wide, so lines from other threads (for
      example a main thread blocked in `join`) are timing-dependent and
      filtered out.
    - JIT warmup takes about 4,000 iterations.
    - A loop that raises on every iteration never gets a trace, so 80% of
      divisor operands are wrapped as `(x or 1)`.
- **Done, P0.4:** `perf.py climb-step --candidate <name>` runs steps 1-5 of
  Per-step acceptance and prints one JSON verdict. `--screen` first runs one
  training-set pass and gates only when its kernel-CPU point estimate is at
  or below 0.99. The gate runs `perf-gate-ready` before and `-1` after, the
  incumbent is `@incumbent-jit`, held-out digests come from a one-run
  `--jit-heldout` pass, the fuzzer runs 2,000 programs, and the targeted
  suites run with and without `PYTHON_JIT` and must fail the same tests. The
  command refuses a candidate whose diff touches `benchmarks/`,
  `perf_verdict.py`, or `tests/jit_fuzz.py`.
- **Done, P0.5:** `climb-ref` (no JIT), `perf-rust-jit`, and a second clean
  JIT build `perf-rust-jit-aa` were rebuilt clean at `51f529f`.
  - **A/A:** `perf-rust-jit` vs `perf-rust-jit-aa`, `--jit-set --gate
    --runs 5`, INCONCLUSIVE with no kernel replicated as improved or
    regressed. Kernel-CPU geomean 1.00025; worst kernel `richards` at
    1.0019; widest pooled CI 1.04%. Some runs read a metric inconclusive
    because a per-run CI upper bound crossed 1.01, so a single 5-run pass
    cannot always bound every kernel inside the 1% floor. Identical builds
    also read peak RSS +0.3% to +1.2%, so the memory floor is inside the
    A/A spread.
  - **`S0` = 0.7474** over the training set against `climb-ref`, range
    `richards` 0.591 to `go` 0.864
    (`results/perf-bench/20261102T174650Z-perf-climb-ref-vs-perf-rust-jit`).
    The JIT build reads peak RSS +1.3% to +5.3% over `climb-ref`, matching
    the pre-P0 attribution.
  - `climb-step --candidate perf-rust-jit-aa --screen` on the A/A pair
    returned SCREEN-REJECTED at 1.0005, as expected.
- **No lane bug found.** The only incumbent mismatch was monitoring LINE
  events, which `perf-upstream-jit` reproduces identically.

## Why the last campaign stalled

- **Its metric hid most of the gain.** The gate's CPU number is `wait4`
  process CPU. It includes about 0.104 s of startup, imports, wheel
  verification, and loading. For nbody with the JIT, the kernel itself is
  0.045 s of a 0.149 s process. A 10% process-CPU cut on nbody needs a 33%
  kernel cut.
- **Its scope ruled out every mechanism big enough.** OD5 excluded
  unboxing, escape analysis, and a new tier, and the campaign allowed one
  optimization. The largest sampled cost left, the macOS thread-local
  lookup `_tlv_get_addr` (16.1% of samples), was attacked one call site at
  a time, about 1% per attempt.
- **No ceiling was measured before building.** Every candidate was
  implemented in full before anyone knew what the most it could win was.

## Starting point

These are pooled five-run kernel wall ratios, `perf-rust-jit` over
`perf-rust` (`rust-cpython/results/perf-bench/20261102T032140Z-perf-rust-vs-perf-rust-jit/`).
Kernel wall equals kernel CPU within 0.1% on these single-threaded kernels
(attribution J4 in `docs/CAMPAIGN-JIT.md`). P0 re-measured them as kernel CPU
against `climb-ref` and got `S0` = 0.7474; the table agrees within 0.013 per
kernel.

| Kernel | JIT / no-JIT | Kernel | JIT / no-JIT |
| --- | ---: | --- | ---: |
| richards | 0.590 | float | 0.825 |
| scimark_sor | 0.601 | deltablue | 0.820 |
| nbody | 0.612 | fannkuch | 0.838 |
| scimark_lu | 0.666 | chaos | 0.847 |
| spectral_norm | 0.690 | nqueens | 0.850 |
| scimark_fft | 0.734 | go | 0.864 |
| scimark_sparse_mat_mult | 0.755 | hexiom | 0.791 |
| scimark_monte_carlo | 0.769 | raytrace | 0.784 |

Geometric mean: 0.7465. Peak RSS is +1.4% to +5.2% over no-JIT.

## Definitions

- **Training set:** the 16 kernels above, unchanged (PD5 wheel, registry
  sizes).
- **Held-out set:** eight kernels from the same pinned pyperformance 1.14.0
  wheel that the climb never profiles or tunes against: `barnes_hut`,
  `pidigits`, `pyflate`, `coroutines`, `generators`, `comprehensions`,
  `richards_super`, `meteor_contest`. Run them with `perf.py bench
  --jit-heldout`; `perf.py profile` refuses them. `pidigits` replaced
  `crypto_pyaes`, which needs the unlocked `pyaes` package. `pidigits` and
  `pyflate` spend most of their time in C code, so they mainly catch
  regressions.
- **Guard set:** the non-kernel workloads the `--jit-set --gate` run already
  includes (`django_template_realistic`, `django_wsgi_request`,
  `import_django`, `python_startup`, `serialization_roundtrip`,
  `zlib_decode_1m`, `gzip_extract_1m`).
- **Kernel CPU:** user plus system CPU from `getrusage(RUSAGE_SELF)`,
  sampled at each kernel's existing `perf_counter()` boundaries. JIT
  compilation and warmup inside the timed loop count. Process CPU stays
  recorded beside it.
- **`climb-ref`:** a clean no-JIT build of `HEAD` at P0, frozen for the
  whole campaign. All absolute scores are against it.
- **Score `S`:** the geometric mean over the training set of the pooled
  median kernel-CPU ratio, candidate over `climb-ref`, from
  `perf.py bench --baseline climb-ref --candidate C --jit-set --runs 5`.
  The P0 score `S0` is expected near 0.75.
- **Incumbent:** the latest accepted JIT build, rebuilt clean after each
  acceptance. P0's incumbent is `perf-rust-jit` rebuilt at `HEAD`.

## Targets

Targets are fixed. Only the owner changes them. A missed milestone is
recorded with its measured `S` and the climb moves to the next tier.

| Milestone | `S` | Subset bars | Tier that should reach it |
| --- | ---: | --- | --- |
| M0 | measured | kernel CPU metric live, held-out set live, A/A spread recorded | P0 |
| M1 | ≤ 0.62 | no training kernel above 0.80 | T1 runtime overheads |
| M2 | ≤ 0.48 | numeric geomean (nbody, spectral_norm, float, five scimark) ≤ 0.35 | T2 unboxing |
| M3 | ≤ 0.38 | object geomean (richards, deltablue, go, chaos, raytrace, hexiom, nqueens, fannkuch) ≤ 0.50 | T3 inlining and allocation removal |
| M4 | ≤ 0.25 | every training kernel ≤ 0.45 | T4 backend |

M1 is roughly the most the measured runtime overheads can buy: removing all
of `_tlv_get_addr` alone would put `S` near 0.63. M2-M4 have no prior
evidence in CPython. They are set at the speedups that tracing JITs with
unboxing and inlining reach on these program types. M4 is the stretch goal
that defines "extremely good".

Milestone gates also require:

- **Held-out set:** geometric mean ratio over `climb-ref` at most `S^0.5`.
  This is half the training improvement in log space. A miss blocks the
  milestone and triggers **Overfit review**.
- **Guard set:** no workload regresses under the unchanged gate policy.
- **Memory:** peak RSS for each kernel stays within +8% of `climb-ref`.
  Total JIT code bytes for each kernel stay within 2x of the M0 incumbent.

## Per-step acceptance

A candidate becomes the incumbent only when all of these hold:

1. **Gate:** `perf.py bench --baseline <incumbent> --candidate C --jit-set
   --gate --runs 5` returns ACCEPT under the unchanged `perf_verdict.py`
   policy (1% practical floor, 95% bootstrap). `perf-gate-ready` exits 0
   before and `perf-gate-ready -1` exits 0 after. A busy host means wait
   and retry, never a pass.
2. **No kernel regression:** no training kernel's pooled kernel-CPU CI95
   lower bound exceeds 1.02 against the incumbent.
3. **Digests:** every training, held-out, and guard digest matches.
4. **Differential fuzz:** 2,000 generated programs from the P0 fuzzer give
   identical output, exceptions, and reference-tracer counts on C and on C
   with `PYTHON_JIT=0`.
5. **Targeted suites:** the JIT-relevant suites from D5 pass with the same
   failed-test set as the JIT-off sibling: `test_capi.test_opt`,
   `test_jit*`, `test_float`, `test_long`, `test_sys_settrace`,
   `test_monitoring`, `test_generators`, `test_frame`, `test_gc`,
   `test_weakref`.
6. **Independent review:** a reviewer that did not write the change reads
   the diff with no access to the author's reasoning. It approves any
   change to reference ownership, deopt, frame materialization, or
   executor invalidation. Any demonstrated defect blocks.
7. **Clean rebuild:** a clean build of C reproduces the gate direction in
   a two-run check. Incremental builds are for screening only.

**Stacking.** A candidate that is INCONCLUSIVE with point estimate ≤ 0.995,
no regressed kernel, and passes 3-6 is parked as stackable. When at least
two stackable candidates compose cleanly, gate the stack as one candidate.
The gate thresholds never change. Stacking is how sub-1% wins add up
without relaxing them.

Every gated candidate, accepted or not, adds one row to **Ledger**: date,
mechanism, commit or tag, `S` before and after, the per-kernel ratios that
moved more than 1%, the verdict, and the verdict directory.

## Sequence

### P0: instrument (one pass, no climbing)

1. Add per-workload `cpu_boundary: Literal["process", "internal"]` to
   `benchmarks/workloads/registry.py`, set to `internal` for the training
   and held-out kernels. The kernels report `cpu_user_seconds` and
   `cpu_system_seconds` over their timed region. `runner._cpu_dict` uses
   them for `internal` workloads and fails loudly when they are missing.
   The coverage label names the boundary, and `statistics._compare_cpu`
   accepts it. This is a benchmark schema contract change; record it in
   `benchmarks/README.md`.
2. Add the held-out kernels and `--jit-heldout`.
3. Write the differential fuzzer under `rust-cpython/tests/`. It generates
   programs over floats, ints, mixed arithmetic, aliasing, subclasses,
   finalizers, nested calls, generators, comprehensions, `f_locals` writes,
   `sys.settrace`, `sys.monitoring`, and threads. Seed
   it from the `parity_jit_float_reuse.py` cases at tag
   `parked/jit-boxed-float-reuse`. It must reproduce, as a mismatch, a
   deliberately broken refcount in a scratch stencil before it counts.
4. Add `perf.py climb-step --candidate <name>`. It runs steps 1-5 of
   **Per-step acceptance** and prints one JSON verdict. A screen mode runs
   one run over the training set in about 2.5 minutes and gates only when
   the point estimate is ≤ 0.99.
5. Build `climb-ref` and the incumbent clean. Run an A/A gate (incumbent
   against a second clean build of itself) and record the spread. Measure
   `S0`.

**Exit:** M0 row in the ledger. From here the harness, kernels, fuzzer
oracle, and `perf_verdict.py` are frozen. A climb step whose diff touches
`benchmarks/`, `perf_verdict.py`, or the fuzzer's comparison logic is
rejected automatically.

### P1: measure ceilings (before each tier)

For each mechanism in the tier, build a throwaway probe. A probe removes the
cost unsafely, keeps digests matching on the training set, and is never
landed. Gate it against the incumbent and record the ratio as the
mechanism's ceiling. Rank the tier's backlog by ceiling. Drop a mechanism
whose ceiling is under 1% unless it unblocks a later tier.

| Tier | Mechanism | Probe |
| --- | --- | --- |
| T1 | Thread-state lookup (`_tlv_get_addr`, 16.1% sampled) | `_PyThreadState_GET` reads a plain global (single-threaded only) |
| T1 | Frame push/pop (`_PyFrame_ClearExceptCode` 3.2%, `_PyEval_FrameClearAndPop`) | skip clearing for frames the trace proves own no cells or locals escapes |
| T1 | `_CHECK_VALIDITY` and `_SET_IP` | strip both from traces |
| T1 | Type and version guards | strip guards the optimizer already proved |
| T2 | Float and int boxing (`PyFloat_FromDouble`, `float_dealloc`, `_PyCompactLong_Add`) | no probe; ceiling is the summed sampled share of allocation, deallocation, refcount, and type guards on the numeric kernels |
| T3 | Python-to-Python call overhead | count frames per kernel; ceiling is the sampled share of frame and call uops |
| T3 | Short-lived object allocation | sampled share of allocation for non-escaping objects |
| T4 | Spills, reloads, stencil glue | recompute the sampled share of `_SPILL_OR_RELOAD` and inter-stencil moves after T3 |

Re-profile with `perf.py profile --tool sample --jit-map` on all 16 kernels
at the start of each tier. Costs that become dominant after earlier tiers
move up.

### T1: runtime overheads, toward M1

Implement the ranked T1 mechanisms. The thread-state fix is systemic, not
per call site. Pass `tstate` or the interpreter through the freelist,
allocation, and frame paths that the JIT and the hot interpreter paths
call. It goes through the overlay and stays correct with subinterpreters and
threads. The fuzzer's thread cases plus `test_threading` and
`test_interpreters` join the targeted suites for this mechanism.

### T2: unboxing, toward M2

Within a trace, keep exact floats and compact ints as raw machine values
between uops. Box them only at side exits, calls, stores to the heap, and
the trace end. Deopt materializes boxed values for every frame
introspection path that the fuzzer covers. Build it in this order:

1. Float unboxing across the binary op and compare uops.
2. Int unboxing with overflow guards.
3. Unboxed loop-carried locals.

Each step is its own candidate.

### T3: inlining and allocation removal, toward M3

1. Inline Python-to-Python calls in traces, with lazy frame
   materialization on deopt.
2. Remove allocations of non-escaping objects inside a trace.
3. Lengthen traces: loop peeling, and hoisting loop-invariant guards.

### T4: backend, toward M4

Revisit the whole-trace register allocator (J8) once T1-T3 have shrunk the
other costs. Then reduce stencil glue. Keep the locked LLVM (OD1).

## Loop

Repeat until **Stop and escalate** fires:

1. If the tier has no ceilings yet, run P1 for it.
2. Take the top unparked mechanism. Write a candidate in a worktree under
   `.work/wt/`, at most two design variants per mechanism.
3. `perf.py build --incremental`, then `climb-step` screen. Discard the
   candidate when the screen shows no win, and record the reason.
4. Gate, then run the full **Per-step acceptance**.
5. Accepted: commit to `main` (Conventional Commit, the ledger row in the
   same commit), rebuild the incumbent clean, and update `S`. Rejected: add
   the ledger row and discard the code. Stackable: tag it
   `stack/<mechanism>` and add the ledger row.
6. When the tier's backlog is empty, run that tier's milestone gate and
   record it, met or missed. Then move to the next tier.

## Stop and escalate

The loop stops and waits for the owner when any of these happens:

- A milestone's held-out check fails (**Overfit review**). The owner
  decides whether the accepted steps since the last milestone stay.
- Eight consecutive gated candidates fail to be accepted. Re-profile once.
  Stop if the next four also fail.
- A correctness failure reproduces on the incumbent, not only on a
  candidate.
- A step needs anything under **Boundary**: a new overlay file under
  `rust-cpython/overlay/Python/`, a lock change, or a production change.
- M4 is met.

## Owner decisions

- **OD7, 2026-11-02: OD5 is lifted for the lane.** Unboxing, escape
  analysis, inlining, and longer or speculative traces are in scope. The
  product build stays JIT-disabled.
- **OD8, 2026-11-02: the fitness metric is kernel CPU.** It replaces
  whole-process CPU for the training and held-out kernels. Wall and process
  CPU stay recorded.
- **OD9, 2026-11-02: accepted steps land on `main` without review by the
  owner.** Each one passes **Per-step acceptance**. Pushing still needs
  the owner.
- **OD4 (unchanged):** memory gates stay as written. The milestone memory
  bars above add to them.

## Boundary

macOS arm64 only. Linux x86-64 confirmation waits for the
`rust-cpython-linux:20260401` image on this host, and Linux arm64 stays out
of the lane. No production change, no lock change, no new dependency, no
push, and no change to `perf_verdict.py` thresholds. Each new overlay file
under `rust-cpython/overlay/Python/` is a contract change for owner review,
including the existing `PYTHON_JIT_MAP` output.

## Ledger

| Date | Step | Mechanism | Ref | `S` before → after | Moved >1% | Verdict | Directory |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-02 | M0 | instrument | `perf-rust-jit` | — → 0.7474 | A/A max 0.19% | MEASURED | `rust-cpython/results/perf-bench/20261102T174650Z-perf-climb-ref-vs-perf-rust-jit` |
