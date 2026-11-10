# rust-cpython JIT campaign

The single campaign for the `rust-cpython/` research lane's Rust execution
pipeline: track **J**, the tier-2 JIT, and track **C**, a Rust source-to-bytecode
compiler ported from Serc. It replaces `CAMPAIGN-JIT.md` (J0-J9, closed 2026-11-01),
`CAMPAIGN-JIT-CLIMB.md` (hill-climb, 2026-11-02), and
`CAMPAIGN-JIT-CINDERX-316.md` (R0-R3, 2026-11-09). Their full text is in git
at `76de0e1`. Every rule below is written so an agent can apply it without
asking; the owner steps in only at **Stop and escalate**.

## Goal and end state

On current CPython 3.16, make the lane's tier-2 JIT four times faster than the
lane's own interpreter on the training kernels with optimizer passes written
in Rust (track J), and compile Python source to byte-identical bytecode
in-process with a Rust compiler (track C), without changing Python semantics
or the 3.15.1 product.

The campaign is **done** when all of these hold at one commit on `main`:

1. The lane builds a pinned latest-3.16 `main` commit (see **Source epochs**)
   with the unchanged LLVM pins.
2. On macOS arm64, the incumbent meets **M4**: `S ≤ 0.25`, every training
   kernel ≤ 0.45, held-out geomean ≤ `S^0.5`, no guard regression, and the
   memory and code-size bars in **Targets**.
3. Every mechanism added by the campaign passes **Per-step acceptance**, and
   new optimizer passes live in Rust under the **Rust rule**.
4. On Linux x86-64, the final incumbent fails exactly its JIT-off sibling's
   failed-test set, matches every kernel digest, and passes the differential
   fuzzer. Linux timing is reported, not gated.
5. Track C meets **C3** on the same epoch: the `--serc` lane build compiles
   every source-mode `exec`, `eval`, and `single` request through Serc
   in-process, byte-identical to upstream CPython on the corpus at
   optimization levels 0-2, with full-suite parity and an ACCEPT cold-import
   gate.
6. One combined build (JIT plus `--serc`) reproduces the M4 score, the C3
   parity, and the targeted suites.
7. The ledger has M4, C3, and closing rows naming the final `S`, the source
   epoch, and the verdict directories.

The campaign also **ends**, incomplete, when T4's backlog is empty without M4
or when track C cannot reach C3; the closing row records how far each track
got and why it stopped. A stopped, blocked, or host-busy run is never a met goal.

Out of scope: any change to the production 3.15.1 build, a JIT-enabled
distribution, Linux arm64, free-threading, PGO, BOLT, and the tail-call
interpreter. Promoting any result to production needs a separate owner
decision and full production qualification.

## Boundary

- **Production is frozen.** No edits to root `build.py`, `buildsys/`,
  `backend/`, `patches/`, `sources.lock.json`, `release-budgets.json`, or CI.
- **LLVM is fixed (OD1).** Stencils use the locked LLVM: 23.1.4 on macOS arm64
  (`bootstrap.lock.json`) and 23.1.2 on Linux x86-64
  (`rust-cpython/linux-toolchain.lock.json`), through configure's
  `LLVM_VERSION` and `LLVM_TOOLS_INSTALL_DIR`. Upstream's "LLVM 21" warning is
  accepted. No other LLVM, system clang, runtime LLVM, asmjit, or GCC.
  Spikes may extract further members of the same locked archive into
  `.work/`. If 23.x breaks stencils, patch `Tools/jit` through the overlay;
  if that fails, escalate.
- **Locks.** Only a **Source epochs** refresh and C0's Serc source record edit
  `rust-cpython/sources.lock.json`. Vetted Rust crates for the **Rust rule**
  and track C update the overlay `Cargo.lock` with their licenses recorded. Every other
  lock, `rust-toolchain.toml`, and the vendored benchmark wheels stay as is.
- **Frozen harness.** A climb step whose diff touches `benchmarks/`,
  `perf_verdict.py`, or the fuzzer's comparison logic is rejected.
  `climb-step` enforces this. Thresholds never change.
- **Hosts.** Performance runs on macOS arm64. Linux x86-64 runs correctness
  in the local `rust-cpython-linux:20260401` image (see **Lane host**); no
  image build, pull, or prune. If the image is missing, Linux items park as
  "needs environment" and done-criterion 4 stays open.
- **TLS stays out.** `_ssl_rs`, `_hashlib_rs`, RTLS/Graviola, and trust-store
  code are untouched by both tracks. Serc's SipHash use for `.pyc` source
  hashes is independent of them.
- **Remote.** No pushes, PRs, issues, or comments unless the owner asks,
  including upstream Serc or CPython.

## Reference roles

| Role | What | Used for |
| --- | --- | --- |
| Semantic oracle | Unmodified CPython at the same pinned 3.16 commit, JIT off, with an upstream JIT-on control | Python semantics, C-API, refcount ownership, tracing and monitoring, deopt, introspection, GC, threads, subinterpreters |
| Parity reference | The candidate's own JIT-off sibling: same overlay commit, same flags but the JIT | Failed-test sets and workload digests (PD8) |
| Performance reference | Pinned [CinderX](https://github.com/facebookincubator/cinderx) on stock CPython 3.15.1, its supported range (`>=3.14.5,<3.16`) | Which mechanisms pay off and by how much; never semantics |
| Implementation target | `rust-cpython/overlay/` on the pinned 3.16 tier-2 pipeline | Where mechanisms land |
| Compiler oracle | Upstream CPython's C compiler and marshal at the epoch commit (`perf-upstream`) | Byte-identical code objects and `.pyc` bytes for track C |
| Compiler starting point | Pinned [Serc](https://github.com/astral-experiments/serc) (MIT OR Apache-2.0), newest target 3.15.0rc2 | The 3.16 port begins from its compiler and marshal writer |

CinderX numbers are within-version uplifts (3.15 JIT over 3.15 no-JIT),
reported beside, never against, the lane's 3.16 uplift.

## Rust rule

New optimizer passes (analysis, unboxing decisions, inline-cache
specialization, inlining, allocation removal, register allocation) are written
in Rust in an overlay crate and called from the tier-2 pipeline. C stays only
where the code must be C: stencil templates compiled by the locked clang,
the existing hooks in `overlay/Python/optimizer.c` and `jit.c`, and the
smallest glue between them and the Rust passes. No handwritten assembly
(ENGINEERING rule 9).

Adding the Rust optimizer crate and its tier-2 hook is a contract change. The
first candidate that needs it stops for one owner review of the crate
boundary; later passes inside that crate do not.

## Definitions

- **Training set:** 16 kernels from the pinned pyperformance 1.14.0 wheel:
  `richards`, `scimark_sor`, `nbody`, `scimark_lu`, `spectral_norm`,
  `scimark_fft`, `scimark_sparse_mat_mult`, `scimark_monte_carlo`, `float`,
  `deltablue`, `fannkuch`, `chaos`, `nqueens`, `go`, `hexiom`, `raytrace`.
- **Held-out set:** `barnes_hut`, `pidigits`, `pyflate`, `coroutines`,
  `generators`, `comprehensions`, `richards_super`, `meteor_contest`. Run with
  `perf.py bench --jit-heldout`; `perf.py profile` refuses them.
- **Guard set:** the non-kernel workloads in `--jit-set --gate`
  (`django_template_realistic`, `django_wsgi_request`, `import_django`,
  `python_startup`, `serialization_roundtrip`, `zlib_decode_1m`,
  `gzip_extract_1m`).
- **Kernel CPU (OD8):** user plus system `getrusage` over each kernel's timed
  region, including compilation and warmup inside it. Wall and process CPU
  stay recorded.
- **`climb-ref`:** a clean no-JIT build of the epoch's first commit, frozen
  for the epoch.
- **Score `S`:** geometric mean over the training set of the pooled median
  kernel-CPU ratio, candidate over `climb-ref`, from `perf.py bench --baseline
  climb-ref --candidate C --jit-set --runs 5`.
- **Incumbent:** the latest accepted JIT build, rebuilt clean after each
  acceptance.

## Targets

Fixed; only the owner changes them. A missed milestone is recorded with its
`S` and the climb moves to the next tier.

| Milestone | `S` | Subset bar | Tier |
| --- | ---: | --- | --- |
| M0 | measured | epoch pinned, A/A spread recorded | R0 |
| M1 | ≤ 0.62 | no training kernel above 0.80 | T1 runtime overheads |
| M2 | ≤ 0.48 | numeric geomean (nbody, spectral_norm, float, five scimark) ≤ 0.35 | T2 unboxing |
| M3 | ≤ 0.38 | object geomean (richards, deltablue, go, chaos, raytrace, hexiom, nqueens, fannkuch) ≤ 0.50 | T3 inlining and allocation removal |
| M4 | ≤ 0.25 | every training kernel ≤ 0.45 | T4 backend |

Every milestone also requires:

- **Held-out:** geomean over `climb-ref` ≤ `S^0.5`. A miss triggers
  **Overfit review**.
- **Guard:** no workload regresses under the unchanged gate policy.
- **Memory (OD4):** each kernel's peak RSS within +8% of `climb-ref`; JIT
  code bytes per kernel within 2x the epoch's M0 incumbent.

## Sequence

```text
J   R0 pin ── R0 port + M0 ── T1 ── M1 ──┬── T2 ── M2 ── T3 ── M3 ── T4 ── M4 ──┐
R1  CinderX study on stock 3.15.1 ───────┘ (catalog before T2 ceilings)        ├── close
C   C0 vet ──┬── marshal parity ───────────────────────────────────────────────┤
             └── (after R0 pin) C1 port ── C2 in-process exec ── C3 all modes ─┘
```

Three lanes run in parallel, each in its own `.work/wt/<item>` worktree:
track J; R1, which needs nothing from R0 and starts now; and track C, whose C0
and marshal parity start now and whose C1 waits only for R0 step 1's pin, not
R0's exit. Builds and suites may overlap; timing gates do not, because each
needs `perf-gate-ready` on a quiet host. JIT climb builds stay without
`--serc` until C3, and track C builds stay without the JIT, so neither track's
candidates move the other's measurements.

### R0: refresh to latest 3.16 and rebaseline

1. Resolve upstream `python/cpython` `main`, pin the commit with archive
   SHA-256 and size in `rust-cpython/sources.lock.json`. Never build a
   floating ref.
2. Port the overlay deliberately: bytecodes and uops, optimizer, stencil
   layout, runtime helpers, frames, C-API, Rust bindings.
3. Confirm the locked LLVMs regenerate stencils, link, and produce ThinLTO
   output on both hosts. A failure is reported with evidence, then
   escalated; never fixed by changing a lock.
4. Build clean upstream and overlay siblings, JIT off and on, per host. Run
   the targeted suites, the broad lane suites, pydebug, and the fuzzer.
   Recheck the 3.16.0a1 upstream findings against the new source:
   - a function that started function-entry tracing then runs under
     `sys.settrace` leaked one block per function with the JIT on;
   - `test_capi.test_opt` and `test_monitoring` failed from the second `-R`
     repetition because the GC invalidates cold executors;
   - `test_optimizer` leaked 6 references per run with or without the JIT.
   Keep the overlay's `effective_trace_length` pystats fix only if upstream
   still needs it.
5. Build `climb-ref`, the incumbent, and a second clean incumbent. Run the
   A/A gate, record its spread, and measure `S0` and the held-out and guard
   baselines.

**Exit:** M0 ledger row for the new epoch. If parity fails, keep the previous
epoch's lock and stop.

### R1: CinderX reference study

1. Source-build pinned CinderX on stock CPython 3.15.1 in `.work/`. Run its
   tests. Record source, ABI, dependency, and license closure. CinderX never
   enters the overlay, a lock, or the product.
2. Measure its JIT-off and JIT-on uplift on the training and held-out
   kernels, with code bytes, warmup, compile CPU, and peak RSS.
3. Read `Jit/hir/`, `Jit/lir/`, `Jit/codegen/`, `Jit/inline_cache.cpp`,
   guard invalidation, type feedback, inlining, and deopt. For each
   mechanism record its source, version assumptions, the semantics it must
   preserve, its measured uplift on our kernels, and the tier it belongs to.
4. Do not adopt its runtime, Static Python, lightweight frames, parallel GC,
   or build system. Copying code needs a license and attribution review;
   prefer an independent Rust implementation.

**Exit:** a mechanism catalog committed as a section of this document, ranked
by measured uplift, feeding the T2-T4 ceilings. This is an owner checkpoint:
the report sets CinderX's measured uplift beside M2-M4, and the owner
reaffirms or moves those targets before T2 starts.

### P1: ceilings (start of each tier)

For each mechanism, build a throwaway probe that removes the cost unsafely
while keeping training digests, and gate it against the incumbent. Its ratio
is the ceiling. Rank the backlog by ceiling; drop a mechanism under 1% unless
it unblocks a later tier. Re-profile all 16 kernels with `perf.py profile
--tool sample --jit-map` first.

| Tier | Mechanism | Probe |
| --- | --- | --- |
| T1 | Thread-state lookup (`_tlv_get_addr`, 16.1% sampled on a1) | `_PyThreadState_GET` reads a plain global |
| T1 | Frame push and pop (`_PyFrame_ClearExceptCode`, `_PyEval_FrameClearAndPop`) | skip clearing frames that own no cells or escaping locals |
| T1 | `_CHECK_VALIDITY` and `_SET_IP` | strip both |
| T1 | Proven type and version guards | strip them |
| T2 | Float and int boxing | summed sampled share of allocation, dealloc, refcount, and type guards on numeric kernels |
| T3 | Python-to-Python calls | sampled share of frame and call uops |
| T3 | Short-lived allocations, attribute and global inline caches | sampled share of allocation and lookup; R1 uplift |
| T4 | Spills, reloads, stencil glue | sampled share of `_SPILL_OR_RELOAD` and inter-stencil moves after T3 |

### T1: runtime overheads, toward M1

Attack only costs the new epoch's profile still shows. The thread-state fix is
systemic: pass `tstate` or the interpreter through the freelist, allocation,
and frame paths, correct under threads and subinterpreters; add
`test_threading` and `test_interpreters` to its targeted suites.

### T2: unboxing, toward M2

Keep exact floats and compact ints as machine values between uops; box only at
side exits, calls, heap stores, and trace end. Deopt rebuilds boxed values on
every introspection path the fuzzer covers. Each is its own candidate:
float unboxing across binary ops and compares, int unboxing with overflow
guards, then unboxed loop-carried locals.

### T3: inlining and allocation removal, toward M3

Inline Python-to-Python calls with lazy frame materialization on deopt. Add
CinderX-informed attribute and global inline caches with invalidation that
honours descriptors, mutation, inheritance, lazy imports, and
subinterpreters. Remove non-escaping allocations. Lengthen traces with loop
peeling and invariant-guard hoisting.

### T4: backend, toward M4

Revisit whole-trace register allocation once T1-T3 shrink other costs (the
a1 attempt cut spills 8% for no measurable gain), then reduce stencil glue.

## Track C: Rust source compiler (Serc on 3.16)

Serc compiles Python source to CPython bytecode; it does not make execution
faster. Its value here is cold imports, `compile()`, and `exec()` of uncached
source (the product ships without `.pyc` caches), plus a byte-exact marshal
writer. Only the port to the pinned 3.16 epoch is in scope; there is no 3.15
host-tool step.

### C0: vet and pin

1. Pin a Serc commit (head `bc563167`, release 0.1.11, on 2026-11-09) as a
   second source record in `rust-cpython/sources.lock.json` with archive
   SHA-256 and size. Lane changes live in an overlay over that archive, as for
   CPython; no copied tree without its record.
2. Pin the `serc` crate's non-dev dependencies exactly in the overlay
   `Cargo.lock` with licenses, including the Ruff parser crates at the git
   revision Serc pins and `stacker`, whose `psm` dependency compiles C and
   assembly through `cc`: that must use the locked clang, never GCC. Extend
   lane `fetch` for the git dependency so builds stay offline. Leave out the
   CLI, benchmark crates, `mimalloc`, and `rayon`.
3. Record the dependency closure and the `libpython` text-size delta of
   linking the bare compiler.

### Marshal parity (starts now)

`_marshal_rs` writes valid bytes that differ from CPython's reference
selection, so `compileall_source` is excluded from control comparisons
(`KNOWN_CONTROL_MISMATCHES` in `rust-cpython/perf.py`). Make `_marshal_rs`
byte-identical to upstream `marshal.dumps`, using Serc's writer as the guide,
and remove the exclusion. Gate: byte-identical output on the C1 corpus, the
marshal and importlib suites, and the existing marshal route verdicts.

### C1: port Serc to the epoch's 3.16

Add a 3.16 target for the epoch commit: opcodes, code-object layout,
exception and line tables, marshal rules, parser features, and compiler
passes. The oracle is upstream CPython at the same commit compiling the same
file. **Corpus:** the epoch's `Lib/` and `Lib/test/`, plus the lane's pinned
benchmark source trees (Django and the `compileall_source`, `pylint_source`,
and `pycparser_source` inputs), at `-O0`, `-O1`, and `-O2`.

**Exit (C1):** zero byte mismatches on the corpus, with Serc's own tests
green. A mismatch blocks until fixed; a file is never excluded to pass.

### C2: in-process `exec` mode

Add an opt-in `--serc` lane build flavor in which source-mode `exec`
compilation (imports, `compile(..., "exec")`, `exec(str)`) goes through
Serc. `eval`, `single`, and AST-object input go to the C compiler by mode,
by design. A Serc error raises; it is never retried on the C compiler. A
verify switch compiles with both and raises on any difference; the full suite
runs once under it.

**Exit (C2):** the `--serc` build fails exactly its sibling's failed-test set,
the verify run reports no difference, and a cold-import gate (`-B`, empty
caches; `python_startup`, `import_django`, `import_app_stack`,
`compileall_source`) against the sibling is ACCEPT under the unchanged
verdict and memory policy. Cold-import workloads may be added to the registry
outside the JIT training, held-out, and guard sets; existing workloads are not
changed.

### C3: all source modes

Extend the Serc path to `eval` and `single` with the same oracle and gates.
AST-object input (`compile(ast_obj, ...)`) stays on the C compiler: Serc
consumes Ruff's AST, and converting CPython's is out of scope.

**Exit (C3):** done-criterion 5.

## Close

Build the combined JIT plus `--serc` incumbent, run done-criteria 4 and 6,
write the closing ledger row, and replace **Status** with the final state.

## Source epochs

An epoch is one pinned 3.16 commit with its own `climb-ref`, incumbent, A/A
spread, and `S0`. Scores compare only within an epoch.

- Refresh only between milestones, never mid-tier. Refresh when the owner
  asks or when upstream tags a 3.16 pre-release and the current tier has
  closed.
- A refresh reruns R0 on the accepted incumbent's code. The ledger opens the
  new epoch with its M0 row; milestones met in an earlier epoch are re-gated
  in the new one before the climb continues.
- If the refresh fails parity, the campaign continues on the previous epoch
  and the refresh is retried at the next milestone.
- Track C re-ports Serc to each new epoch and reruns C1's corpus before its
  next gate. Track J never waits for it; track C's milestones count only on
  the epoch they were measured on.

## Per-step acceptance

A candidate becomes the incumbent only when all hold:

1. **Gate:** `perf.py bench --baseline <incumbent> --candidate C --jit-set
   --gate --runs 5` returns ACCEPT under the unchanged `perf_verdict.py`
   policy (1% floor, 95% bootstrap). `perf-gate-ready` exits 0 before and
   `perf-gate-ready -1` exits 0 after; a busy host means wait, never pass.
2. **No kernel regression:** no training kernel's pooled kernel-CPU CI95
   lower bound exceeds 1.02 against the incumbent.
3. **Digests:** every training, held-out, and guard digest matches.
4. **Differential fuzz:** 2,000 programs from `rust-cpython/tests/jit_fuzz.py`
   give identical output, exceptions, and reference-tracer counts with the JIT
   on and off.
5. **Targeted suites:** the same failed-test set as the JIT-off sibling for
   `test_capi.test_opt`, `test_jit*`, `test_float`, `test_long`,
   `test_sys_settrace`, `test_monitoring`, `test_generators`, `test_frame`,
   `test_gc`, `test_weakref`.
6. **Independent review:** a reviewer who did not write the change approves
   any change to reference ownership, deopt, frame materialization, or
   executor invalidation. A demonstrated defect blocks.
7. **Clean rebuild:** a clean build of C reproduces the gate direction in a
   two-run check.

`perf.py climb-step --candidate <name>` runs steps 1-5; `--screen` first runs
one training pass and gates only at a point estimate ≤ 0.99.

**Stacking.** An INCONCLUSIVE candidate with point estimate ≤ 0.995, no
regressed kernel, and steps 3-6 passed is tagged `stack/<mechanism>`. Two or
more stackable candidates that compose cleanly are gated as one.

## Loop

1. If the tier has no ceilings, run P1.
2. Take the top unparked mechanism; write it in `.work/wt/<mechanism>`, at
   most two design variants.
3. `perf.py build --incremental`, then `climb-step --screen`. No win: discard
   and record why.
4. Gate, then run **Per-step acceptance**.
5. Accepted: commit to `main` with its ledger row, rebuild the incumbent
   clean, update `S`. Rejected: ledger row, discard. Stackable: tag and
   ledger row.
6. When the tier's backlog is empty, gate its milestone and record it, met or
   missed; consider a **Source epochs** refresh; move to the next tier.

## Stop and escalate

The loop stops for the owner when:

- a milestone's held-out check fails (**Overfit review**: the owner decides
  whether steps since the last milestone stay);
- eight consecutive gated candidates fail; re-profile once and stop if the
  next four also fail;
- a correctness failure reproduces on the incumbent;
- the locked LLVM cannot build a refreshed source;
- a step needs the Rust crate boundary review, the C2 compile-path hook
  (it changes CPython's compile entry points, a contract change), a new
  overlay file under `overlay/Python/`, a lock change outside **Boundary**,
  or a production change;
- R1 lands its catalog (the target checkpoint);
- the campaign reaches its end state.

## Owner decisions

- **OD1 (2026-11-01):** keep the pinned LLVM; see **Boundary**.
- **OD4:** memory gates stay as written; JIT against no-JIT keeps its peak
  RSS REJECT as a recorded cost.
- **OD7 (2026-11-02):** unboxing, escape analysis, inlining, and longer or
  speculative traces are in scope. Production stays JIT-disabled.
- **OD8 (2026-11-02):** fitness is kernel CPU.
- **OD9 (2026-11-02):** accepted steps land on `main` without owner review.
- **OD10 (2026-11-09):** track latest 3.16 `main` through **Source epochs**;
  use CinderX as a performance reference only; new optimizer passes are Rust.
- **OD11 (2026-11-09):** track C ports Serc to the pinned 3.16 epoch only,
  runs in parallel with track J, and leaves TLS code untouched. The product
  compiler is not replaced.
- **Parked hypothesis:** a method-based frontend (withdrawn PEP 836
  prototype, observed head `04a79098`) is not on the path to the end state
  and opens only by owner decision.

## Lane host

Linux x86-64 lane commands run in the local image `rust-cpython-linux:20260401`
(Ubuntu 24.04, the exact `linux-toolchain.lock.json` packages, rustup
`nightly-2026-09-15`):

```sh
docker run --rm --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  -v "$PWD:$PWD" -w "$PWD" -e TMPDIR="$PWD/.work/tmp" \
  rust-cpython-linux:20260401 <command>
```

Builds and `test --all` outlast 10 minutes; run them in the background.

## Debugging methods

1. **Compare against the JIT-off sibling.** `PYTHON_JIT=0` skips every
   `requires_jit_enabled` test, so it proves nothing for tier-2 tests.
   `python -I` implies `-E` and silently drops `PYTHON_JIT`.
2. **Shrink to pure Python first.** `sys.getallocatedblocks()` deltas over
   fresh `exec`'d functions and `_opcode.get_executor(code, i)` reproduce
   leaks and missing executors without a test runner. The GC invalidates cold
   executors, and backoff then blocks re-tracing. Warmup takes about 4,000
   iterations; a loop that raises every iteration never traces.
3. **A release SIGSEGV becomes an assertion under pydebug.** Run the test on
   `rust-cpython/stage/bin/python3.16d` with `PYTHON_LLTRACE=2`; the last trace
   printed before the abort is the suspect, not the reported
   `executor_cases.c.h` line.
4. **Bisect code generation with a runtime mask** in a throwaway build: read
   an env var once in the changed function, one bit per transition, and run
   the failing tests once per bit. Delete the build and mask before
   committing.
5. **Model an allocator offline** from `_opcode.get_executor` dumps and
   `_PyUop_Caching`; keep `-1` default entries in the table parse.
6. **Uop share is not CPU.** Check stencil body sizes in
   `jit_stencils-<triple>.h` before ranking a cost.
7. **pystats:** build `--jit --pystats`; per kernel, `sys._stats_clear()`,
   run, `sys._stats_off()`, `sys._stats_dump()` into an existing stats
   directory; summarize with the build's `Tools/scripts/summarize_stats.py`.
8. **Look in the locked LLVM archive, not the prefix,** for `opt`,
   `llvm-config`, `llvm-symbolizer`, headers, and the ASan runtime.
9. **Monitoring:** the JIT reports LINE events only at side exits, upstream
   included, so line sets are compared only between JIT-off runs. Event
   counts at `set_events` boundaries vary between identical runs.
10. **Harness traps:** two containers in one directory collide on
    `test_python_<pid>`; a crashed test leaves mode-000 directories that break
    the next clean build (`chmod -R u+rwx`); delete a dropped `core`; never run
    bare `make` in a perf build directory; `rc=2` right after a crash is
    usually leftover scratch.

## Status

- **Epoch a1** (`v3.16.0a1`, `22d79d98`): P0 tooling done and frozen
  (kernel-region CPU, held-out set, differential fuzzer, `climb-step`). M0
  measured `S0 = 0.7474`, A/A max 0.19%. No climb step was taken. The a1
  epoch closes at R0; its numbers do not carry over.
- **Track C:** not started.
- **Next, in parallel:** R0 on the latest 3.16 `main`; R1 on stock 3.15.1;
  C0 and marshal parity. C1 starts once R0 pins the epoch.

Prior a1 evidence that shapes the backlog:

- Sampled cost ranking over 16 kernels: `_tlv_get_addr` 16.1%,
  `_PyFrame_ClearExceptCode` 3.2%, `PyFloat_FromDouble` 2.8%, `_POP_TOP`
  2.3%. Executed-uop shares (Linux pystats): type guards 10.5%, `_SET_IP`
  9.6%, `_CHECK_VALIDITY` 8.1%, `_SPILL_OR_RELOAD` 7.5%.
- Rejected one-site fixes: generic exact-float pop (REJECT, stencil 56 to 156
  bytes), outlined and inlined boxed-float reuse (INCONCLUSIVE and REJECT;
  parked at `lane/jit-boxed-float-reuse`), compact-int fast path
  (INCONCLUSIVE), whole-trace register cache (INCONCLUSIVE, 0.9993). Lesson:
  per-call-site patches buy about 1%; T1 must be systemic.
- Hybrid DynASM backend (python/cpython#146235): closed, not ported.

## Ledger

Track C rows use the same columns, with the gate's headline ratio in `S`.

| Date | Epoch | Step | Mechanism | Ref | `S` before → after | Moved >1% | Verdict | Directory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-02 | a1 | M0 | instrument | `perf-rust-jit` | — → 0.7474 | A/A max 0.19% | MEASURED | `rust-cpython/results/perf-bench/20261102T174650Z-perf-climb-ref-vs-perf-rust-jit` |
