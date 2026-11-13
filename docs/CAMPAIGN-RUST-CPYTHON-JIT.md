# rust-cpython JIT campaign

The single campaign for the `rust-cpython/` research lane's Rust execution
pipeline. Track **J** builds a Rust optimizing tier on CPython 3.16's tier-2
JIT. Track **C** ports Serc, a Rust source-to-bytecode compiler, to the same
3.16. Lane **R1** studies CinderX as a performance reference for track J.

It replaces `CAMPAIGN-JIT.md` (J0-J9, closed 2026-11-01),
`CAMPAIGN-JIT-CLIMB.md` (hill-climb, 2026-11-02), and
`CAMPAIGN-JIT-CINDERX-316.md` (R0-R3, 2026-11-09); their text is in git at
`76de0e1`. The campaign runs unattended. Every rule below is written so an
agent can apply it without asking; owner input is collected asynchronously
(see **Owner queue**) and blocks only the lane that needs it.

## Goal and end state

On current CPython 3.16, make the lane's tier-2 JIT four times faster than the
lane's own interpreter on the training kernels, with optimizer passes written
in Rust; and compile Python source in-process with a Rust compiler that is
byte-identical to CPython's. Python semantics and the 3.15.1 product do not
change.

The campaign is **done** when all of these hold at one commit on `main`:

1. The lane builds one pinned 3.16 `main` commit (see **Source epochs**) with
   the unchanged LLVM pins.
2. On macOS arm64 the JIT incumbent meets **M4**: `S ≤ 0.25`, every training
   kernel ≤ 0.45, held-out geomean ≤ `S^0.5`, no guard regression, and the
   memory and code-size bars in **Targets**.
3. Every accepted track J step passed **J acceptance**, and new optimizer passes
   live in Rust under the **Rust rule**.
4. Track C meets **C3** on the same epoch: `perf-rust-serc` compiles every
   source-mode `exec`, `eval`, and `single` request through Serc in-process,
   byte-identical to upstream CPython on the corpus at `-O0`/`-O1`/`-O2`, with
   full-suite parity and ACCEPT cold-import gates.
5. The combined build `perf-rust-jit-serc` reproduces the M4 score within the
   A/A spread, the C3 corpus parity, and the J targeted suites.
6. On Linux x86-64, the combined build fails exactly its no-JIT, no-Serc
   sibling's failed-test set, matches every kernel digest, and passes the
   differential fuzzer and the C1 corpus. Linux timing is reported, not gated.
7. The ledger has M4, C3, and closing rows.

The campaign **ends incomplete** when T4's backlog is empty without M4, or
when track C exhausts its attempts without C3; the closing row records how
far each track got and why. A stopped, blocked, or host-busy run is never a
met goal.

Out of scope: any change to the production 3.15.1 build, a JIT- or
Serc-enabled distribution, Linux arm64, free-threading, PGO, BOLT, the
tail-call interpreter, and a method-based JIT frontend (PEP 836, parked).

## Execution model

### Start here

An agent resuming the campaign reads, in order: **Status** (which lanes are
active, parked, or waiting), this section, the active item's section, and
**Boundary**. It then continues every lane that is not parked. It never
re-derives a decision recorded here.

### Lanes

| Lane | Items, in order | Depends on |
| --- | --- | --- |
| J | R0 → T1 → M1 → T2 → M2 → T3 → M3 → T4 → M4 | T2 ceilings need R1's catalog |
| R1 | CinderX study | nothing; starts now |
| C | C0 → marshal parity; C1 → C2 → C3 | C1 needs R0's chosen SHA, not R0's exit |
| Close | combined build, Linux confirmation | M4 and C3 on one epoch |

Each lane works in its own worktree `.work/wt/<item>` on branch
`campaign/<item>`, created with `perf.py setup-worktree` so caches are shared.
`TMPDIR` points at `.work/tmp`.

### Integration

- A lane integrates an accepted change by rebasing onto `main`, rerunning the
  narrowest check its change affects, and fast-forwarding `main`. Only one lane
  integrates at a time; the others rebase before their next gate.
- Commits are Conventional Commits scoped `jit`, `serc`, `marshal`, `bench`,
  or `docs`; the body names the item and the commands with their results.
  Accepted steps land on `main` without owner review (OD9). Nothing is pushed.
- After every accepted step or milestone, the lane rewrites its **Status** row
  and adds its **Ledger** row in the same commit. Status is rewritten in
  place, never appended.
- Default-build changes from track C (marshal parity) land only at a J
  milestone boundary or before R0 step 5, so they never move a J measurement
  mid-tier. They get a ledger row; track J rebuilds its incumbent and
  re-measures `S` after one lands.

### Host

- Builds, suites, and benchmarks go through `perf.py`, whose host lease lets
  builds and tests share the host and gives a measurement exclusive use across
  all worktrees. Work outside `perf.py` (CinderX and Serc test builds) never
  runs while `perf.py status` shows a measurement holding the lease.
- During feature completion, performance gates are deferred (OD16): no step
  waits for a quiet host or a `perf-gate-ready` window. Correctness, parity
  and independent review still gate feature completion. Performance acceptance
  stays open until the unchanged gates can run; a waiver is never a pass.
  The rules below apply when **Close** resumes performance measurements.
- Gate evidence counts only when `perf-gate-ready` exits 0 immediately before
  and `perf-gate-ready -1` exits 0 immediately after. If the gate refuses
  three times at least 20 minutes apart, record "perf gate deferred: host
  busy" in Status, do other non-timing work, and retry at the next
  checkpoint. A deferred gate is never a pass.
- Delete a rejected candidate's build (`perf.py clean --name <build>`) and
  worktree when its ledger row lands. Keep named builds in **Builds**.

### Attempts and parking

- A failing item gets its root cause before any retry. An item gets at most
  three root-caused attempts unless its section says otherwise.
- To park an item: push nothing, keep its branch as `lane/<item>`, and record
  in Status the cause, the evidence path, and the condition that reopens it.
  The lane moves to its next unblocked item; if none exists, the lane is
  parked.
- Fix an egregious local defect met on the way (crash, wrong output, a
  harness step twice as slow as recorded) in its own commit. Anything else
  goes to `docs/open-work.md`.
- The campaign session ends when every lane is parked, waiting on the owner,
  or done. The final message lists each lane's state and the owner queue.

### Owner queue

Owner reviews are asynchronous. A contract change lands once it passes its
track's acceptance, including the independent review, and is added to the
**Owner queue** in Status. If the owner rejects it later, it is reverted and
the affected steps are re-gated. Only these items block, and only their own
lane: **Overfit review**, a lock or production change outside **Boundary**,
and an LLVM incompatibility that patching `Tools/jit` cannot fix.

## Boundary

- **Production is frozen.** No edits to root `build.py`, `buildsys/`,
  `backend/`, `patches/`, `sources.lock.json`, `bootstrap.lock.json`,
  `release-budgets.json`, or CI. Reading them, or building a production input
  archive into `.work/`, is allowed.
- **LLVM is fixed (OD1).** Stencils use the locked LLVM: 23.1.4 on macOS arm64
  (`bootstrap.lock.json`) and 23.1.2 on Linux x86-64
  (`rust-cpython/linux-toolchain.lock.json`), through configure's
  `LLVM_VERSION` and `LLVM_TOOLS_INSTALL_DIR`. Upstream's "LLVM 21" warning is
  accepted. No other LLVM, system clang, runtime LLVM, asmjit, or GCC enters a
  lane build. Spikes may extract further members of the locked archive into
  `.work/`. R1's CinderX build is scratch and may use the toolchain CinderX
  documents; nothing it builds enters the lane.
- **Locks.** R0 and later epoch refreshes edit the CPython record in
  `rust-cpython/sources.lock.json`; C0 adds the Serc record. Vetted crates for
  the **Rust rule** and track C update the overlay `Cargo.lock` with their
  licenses recorded. Every other lock, `rust-toolchain.toml`, and the vendored
  benchmark wheels stay as they are.
- **Frozen harness.** From M0 on, a J step whose diff touches `benchmarks/`,
  `perf_verdict.py`, or the fuzzer's comparison logic is rejected
  (`climb-step` enforces this). R0 may repair the harness for 3.16 before M0.
  Track C may add new workloads outside the J training, held-out, and guard
  sets, never edit existing ones. Thresholds never change.
- **TLS stays out.** `_ssl_rs`, `_hashlib_rs`, RTLS/Graviola, and trust-store
  code are untouched. Serc's SipHash `.pyc` source hashing is unrelated.
- **Hosts.** Performance runs on macOS arm64. Linux x86-64 correctness runs in
  the local image `rust-cpython-linux:20260401` (see **Lane host**); no image
  build, pull, or prune. Where the image is absent, Linux items park as "needs
  environment" and the rest continues.
- **Network.** Allowed reads: lane `fetch` and `perf.py fetch` of locked
  inputs, `git ls-remote`/archive downloads of `python/cpython`, and clones of
  pinned CinderX and Serc commits into `.work/`. No pushes, PRs, issues, or
  comments anywhere, including upstream Serc, CinderX, or CPython.

## Reference roles

| Role | What | Used for |
| --- | --- | --- |
| Semantic oracle | `perf-upstream`, `perf-upstream-jit`: unmodified CPython at the epoch commit | Python semantics, upstream-red attribution |
| Parity reference | The candidate's sibling: same overlay commit, same flags minus the feature under test | Failed-test sets and workload digests |
| Performance reference | Pinned [CinderX](https://github.com/facebookincubator/cinderx) on stock CPython 3.15.1, inside its supported range `>=3.14.5,<3.16` | Which mechanisms pay off and by how much; never semantics |
| Compiler oracle | `perf-upstream` compiling and marshalling the same file | Byte-identical code objects for track C |
| Compiler starting point | Pinned [Serc](https://github.com/astral-experiments/serc), MIT OR Apache-2.0, newest target 3.15.0rc2 | Track C's compiler and marshal writer |

CinderX numbers are within-version uplifts (3.15 JIT over 3.15 no-JIT),
reported beside the lane's 3.16 uplift, never against it.

## Rust rule

New optimizer passes (analysis, unboxing decisions, inline-cache
specialization, inlining, allocation removal, register allocation) are written
in Rust in one overlay crate, `rust-cpython/overlay/Modules/_jit_opt_rs/`, and
called from the tier-2 pipeline. C stays only where it must: stencil
templates compiled by the locked clang, the existing hooks in
`overlay/Python/optimizer.c` and `jit.c`, and the smallest glue between them
and the crate. No handwritten assembly (ENGINEERING rule 9). T1's runtime
fixes are changes to existing C paths and are exempt.

The crate and its hook are a contract change: the first candidate that adds
them goes to the **Owner queue**.

## Builds

| Build | `perf.py build` flags | Role |
| --- | --- | --- |
| `perf-upstream` | `--empty-overlay` | semantic and compiler oracle |
| `perf-upstream-jit` | `--empty-overlay --jit` | upstream JIT control |
| `perf-rust` | — | no-JIT sibling, track C baseline |
| `perf-rust-jit` | `--jit` | J incumbent (`@incumbent-jit`) |
| `perf-rust-jit-aa` | `--jit` | A/A twin, built at M0 only |
| `perf-climb-ref` | — | frozen no-JIT reference for the epoch |
| `perf-jit-<mechanism>` | `--jit` | J candidate or probe |
| `perf-rust-serc` | `--serc` | C incumbent |
| `perf-serc-<item>` | `--serc` | C candidate |
| `perf-rust-jit-serc` | `--jit --serc` | combined build at Close |

C2 added `--serc` to `perf.py build` and `rust-cpython/build.py build`.
Serc is linked only into `--serc` builds.
"Rebuild clean" means `perf.py clean --name <build>` then `perf.py build`
with the table's flags at `main`'s head.

Status names the current correctness incumbent. After C3 these accepted stages
use both `--jit` and `--serc`; paired J candidates must match both report flags.
An OD15 footprint comparison uses the same overlay and JIT setting with Serc
on and off. A combined build against `perf-climb-ref` changes both features
and records their aggregate cost; it does not isolate the Serc footprint gate.

## Definitions

- **Training set:** 16 kernels from the pinned pyperformance 1.14.0 wheel
  (`benchmarks/workloads/jit_kernels.py`): `richards`, `scimark_sor`,
  `nbody`, `scimark_lu`, `spectral_norm`, `scimark_fft`,
  `scimark_sparse_mat_mult`, `scimark_monte_carlo`, `float`, `deltablue`,
  `fannkuch`, `chaos`, `nqueens`, `go`, `hexiom`, `raytrace`.
- **Held-out set:** `barnes_hut`, `pidigits`, `pyflate`, `coroutines`,
  `generators`, `comprehensions`, `richards_super`, `meteor_contest`. Run with
  `--jit-heldout`; `perf.py profile` refuses them.
- **Guard set:** the non-kernel workloads in `--jit-set --gate`
  (`django_template_realistic`, `django_wsgi_request`, `import_django`,
  `python_startup`, `serialization_roundtrip`, `zlib_decode_1m`,
  `gzip_extract_1m`).
- **Kernel CPU (OD8):** user plus system `getrusage` over each kernel's timed
  region, including compilation and warmup inside it. Wall and process CPU
  stay recorded.
- **Score `S`:** geometric mean over the training set of the pooled median
  kernel-CPU ratio, from `perf.py bench --baseline perf-climb-ref --candidate
  perf-rust-jit --jit-set --runs 5`.
- **Held-out score:** the same over the held-out set, with `--jit-heldout`.
- **JIT code bytes:** per kernel, the summed executor code size in the
  `PYTHON_JIT_MAP` output of one registry-size run. R0 adds this to
  `perf.py` before M0 if no command reports it.

## Targets

Fixed; only the owner changes them. A missed milestone is recorded with its
`S`, and the lane moves to the next tier.

| Milestone | `S` | Subset bar | Tier |
| --- | ---: | --- | --- |
| M0 | measured | epoch pinned, A/A spread recorded | R0 |
| M1 | ≤ 0.62 | no training kernel above 0.80 | T1 runtime overheads |
| M2 | ≤ 0.48 | numeric geomean (nbody, spectral_norm, float, five scimark) ≤ 0.35 | T2 unboxing |
| M3 | ≤ 0.38 | object geomean (richards, deltablue, go, chaos, raytrace, hexiom, nqueens, fannkuch) ≤ 0.50 | T3 inlining and allocation removal |
| M4 | ≤ 0.25 | every training kernel ≤ 0.45 | T4 backend |

**Milestone gate.** With `perf-rust-jit` rebuilt clean, run the `S` command,
the held-out command, and `perf.py bench --baseline perf-climb-ref
--candidate perf-rust-jit --jit-set --gate --runs 5`, each between
`perf-gate-ready` checks, and measure JIT code bytes. The milestone is met
when all of these hold:

- `S` and the subset bar meet the row.
- **Held-out:** held-out score ≤ `S^0.5`. A miss is **Overfit review**: the
  J lane parks and the owner decides which steps since the last milestone
  stay.
- **Guard:** no guard workload regresses under the unchanged gate policy.
- **Memory (OD4):** each kernel's peak RSS within +8% of `perf-climb-ref`;
  JIT code bytes per kernel within 2x the epoch's M0 incumbent.

## Track J

### R0: refresh to latest 3.16 and rebaseline

1. Resolve `python/cpython` `main` with `git ls-remote`, download its archive,
   and record the SHA, SHA-256, and size in Status. Work in
   `.work/wt/r0`; the lock change lands on `main` with R0's exit.
2. Port the overlay: bytecodes and uops, optimizer, stencil layout, runtime
   helpers, frames, C-API, Rust bindings, Rust modules.
3. Confirm the locked LLVM regenerates stencils, links, and produces ThinLTO
   output. If it fails and an overlay patch to `Tools/jit` cannot fix it in
   three attempts, park the J lane (owner-blocking).
4. Build `perf-upstream`, `perf-upstream-jit`, `perf-rust`, and
   `perf-rust-jit`. Run `perf.py test --all` on each, the targeted suites,
   pydebug, and the fuzzer at 2,000 programs for seeds 0-2. **Parity:** each
   overlay build fails no test that its upstream counterpart passes; tests red
   on upstream are recorded as upstream-red with the upstream build's
   evidence. Recheck these 3.16.0a1 upstream findings and record each as
   fixed or still present:
   - a function that started function-entry tracing then runs under
     `sys.settrace` leaked one block per function with the JIT on;
   - `test_capi.test_opt` and `test_monitoring` failed from the second `-R`
     repetition because the GC invalidates cold executors;
   - `test_optimizer` leaked 6 references per run with or without the JIT.
   Keep the overlay's `effective_trace_length` pystats fix only if upstream
   still needs it. A parity failure gets three root-caused attempts per
   failing test file, then the J lane parks.
5. Repair `benchmarks/`, the fuzzer, or `climb-step` only where 3.16 broke
   them, and add the JIT code bytes measurement. The harness is frozen after
   this step.
6. Build `perf-climb-ref` and `perf-rust-jit-aa`. Run the A/A gate
   (`perf-rust-jit` against `perf-rust-jit-aa`, `--jit-set --gate --runs 5`),
   then the **Milestone gate** commands to measure `S0` and the held-out and
   guard baselines.

**Exit:** the lock change and M0 ledger row land on `main`. Linux x86-64 parity
for step 4 runs where the image exists; otherwise it parks under Close and
does not block M0.

### P1: ceilings (start of each tier)

Re-profile all 16 kernels with `perf.py profile --tool sample --jit-map`. For
each mechanism in the tier, build a throwaway probe `perf-jit-probe-<name>`
that removes the cost unsafely while keeping training digests, and gate it
against the incumbent with `climb-step --candidate`. Its ratio is the
mechanism's ceiling; record it in the ledger with verdict PROBE. Rank the
backlog by ceiling, and drop a mechanism under 1% unless it unblocks a later
tier. Costs that the profile shows as newly dominant join the backlog.

| Tier | Mechanism | Probe or ceiling source |
| --- | --- | --- |
| T1 | Thread-state lookup (`_tlv_get_addr`, 16.1% sampled on a1) | `_PyThreadState_GET` reads a plain global |
| T1 | Frame push and pop (`_PyFrame_ClearExceptCode`, `_PyEval_FrameClearAndPop`) | skip clearing frames that own no cells or escaping locals |
| T1 | `_CHECK_VALIDITY` and `_SET_IP` | strip both |
| T1 | Proven type and version guards | strip them |
| T2 | Float and int boxing | summed sampled share of allocation, dealloc, refcount, and type guards on numeric kernels, with R1's measured uplift |
| T3 | Python-to-Python calls | sampled share of frame and call uops |
| T3 | Short-lived allocations; attribute and global inline caches | sampled share of allocation and lookup, with R1's measured uplift |
| T4 | Spills, reloads, stencil glue | sampled share of `_SPILL_OR_RELOAD` and inter-stencil moves after T3 |

### T1: runtime overheads, toward M1

Attack only costs the new epoch's profile still shows. Per-call-site patches
bought about 1% each on a1, so the thread-state fix is systemic: pass `tstate`
or the interpreter through the freelist, allocation, and frame paths, correct
under threads and subinterpreters. Add `test_threading` and
`test_interpreters` to its targeted suites.

**Frame push and pop (measured 2026-11-10: 1.3% removable, re-ranked).** The
backlog's 13.0% mean is almost all work that must happen. Profiles of the 16
kernels on `perf-jit-int-unbox` (`perf.py profile --tool sample --jit-map`)
split it as: `_PyFrame_ClearExceptCode` self 3.65% mean (the localsplus scan
and inline decrefs, `Python/frame.c:100-135`), `_PyEval_FrameClearAndPop`
1.44% (`Python/ceval.c:2038-2049`), `_PyThreadState_PopFrame` 1.16%
(`Python/pystate.c:3332-3356`), push-side runtime 1.08%
(`_PyFrame_Initialize`/`_PyFrame_PushUnchecked`, `pycore_interpframe.h:188-219`,
`:404-415`), and the frame uops themselves 5.27%. Slot occupancy at return,
measured with `_testinternalcapi.get_frame_local` over the same kernels, is
96.4-99.8% live on the return-heavy kernels (sor 0.2% null, lu 0.1%, hexiom
0.2%, raytrace 0.7%, deltablue 0.4%, nqueens 0.3%; go 10.9%, richards 3.6%),
so clearing slots known NULL is at most 0.04% mean. Immortal and borrowed
references already cost no decref: `PyStackRef` sets `Py_TAG_REFCNT` on them
(`Include/internal/pycore_stackref.h:483-496`, `:744-751`) and the clear loop
tests that bit. No frame field is redundantly initialised:
`_PyFrame_Initialize` already zeroes only the tail beyond the passed arguments.
What remains is out-of-line call overhead: the incumbent's disassembly has 10
of 26 fast-path instructions in the `_PyEval_FrameClearAndPop` prologue and
epilogue and 7 of 12 in `_PyThreadState_PopFrame`, about 1.23% mean, or 1.27%
with the NULL-slot scan. Inlining the clear loop as well would add about 0.9%
but duplicates the materialized-frame and generator paths into every return
stencil, and the loop body is necessary decref work either way. The removable
part is above P1's 1% floor and landed as a static-inline fast path:
`Include/internal/pycore_interpframe.h:400-430` adds
`_PyEval_FrameClearAndPopThreadFast`, for a thread-owned frame with
`frame_obj == NULL`, which clears the frame and then pops it inline unless it
starts a datastack chunk (upstream's order: the chunk is read after the clear,
whose finalizers may push and pop chunks), and `_PyThreadState_PopFrameFast`; `_RETURN_VALUE` and `RETURN_GENERATOR`
(`overlay/Python/bytecodes.c:1691`, `:5950`) use them, and every other case
calls the out-of-line functions unchanged. Sampled self time of the two
symbols, same command and iteration counts (`perf.py profile --tool sample
--jit-map --iterations 500/70/4121/2074`), `perf-jit-tlv` →
`perf-jit-popinline`: richards 7.85% → 0.00%, raytrace 5.24% → 0.00%,
deltablue 5.48% → 0.00%, scimark_sor 4.89% → 0.00% (mean over the four
5.87% → 0.00%). `_RETURN_VALUE`'s own sampled share rises 1.03% → 6.42% on
richards and 0.53% → 3.99% on deltablue as the work moves into the stencil,
whose `_RETURN_VALUE_r11` grows 60 → 192 bytes. These profiles ran on an
earlier draft whose stencil checked the chunk before clearing; the landed
stencil differs only in that order. On a clean build of the landed source,
climb-step `--correctness` is CORRECT: digests equal, 2,000 fuzz programs with
0 mismatches, targeted suites match. JIT code bytes are at most 1.30x M0
(raytrace 457,068); the incumbent-relative growth is largest on richards
(+6.7%); peak RSS is +0.1% to +1.1%
over the incumbent and at most +6.0% (raytrace) over the no-JIT sibling
`perf-rust`. Evidence:
`rust-cpython/results/perf-profile/20261110T1658*-perf-jit-{tlv,popinline}-*-sample`.

**Type and version guards (measured 2026-11-10: 0.01% removable, dropped).**
The backlog's 8.5% mean is an upper bound. Upstream's abstract interpreter
already nops an exact-type guard when `sym_matches_type` holds
(`Python/optimizer_cases.c.h:911-929` for float and `:569-592` for int), a
`_GUARD_TYPE_VERSION` when `sym_matches_type_version` holds (`:2731-2780`), and
a repeated `_GUARD_GLOBALS_VERSION` in one abstract frame (`:2340-2362`); it
types the producers (`_ITER_NEXT_RANGE` `:3996`, `_BINARY_OP_*_FLOAT` `:931`)
and `_LOAD_FAST` copies the frame local's symbol (`:42-62`), so a fact won once
survives a reload. The saved 16 `perf.py profile --tool sample --jit-map` runs
give 8.51% mean (richards 18.9%, go 17.7%): 0.72% `_GUARD_TYPE_VERSION` and
`_GUARD_TYPE`, 3.31% exact-type and `_GUARD_BINARY_OP_EXTEND` guards, 1.45%
`_CHECK_MANAGED_OBJECT_HAS_VALUES`/`_CHECK_ATTR_*`/`_GUARD_DORV_*`, 1.39%
function, globals and builtins versions, 0.23% code versions, 0.17% callable
and object checks, and 1.23% pure value guards (`_GUARD_IS_*`,
`_GUARD_BIT_IS_*`), which are outside this mechanism. Matching a `gc` dump of
each `uop_executor`, with opargs and operand0 intact, to the sampled uops, and
keeping a fact only until an escaping uop or an object store, with immutable
builtin type facts outliving escapes: 4.6% mean of samples are the first
occurrence of that check on that value, 3.8% sit on values with no trace-local
type or version fact (loop-entry stack slots, call results, attribute loads,
unpack elements), and no guard repeats a check that an escape invalidated. The
executor dump reads each `uop_executor` item as `(name, oparg, target,
operand0)`, so type versions are compared per trace. The only
provable repeats are 3 samples on go (`Board.useful`, `_CHECK_MANAGED_OBJECT`
twice in one trace) and 5 on go and nqueens globals-version checks in one frame
with no intervening escape. Repeated `_GUARD_CODE_VERSION__PUSH_FRAME` guards
(sor 0.53%, lu 0.24%, hexiom 0.03% of samples) are not provable: an equal
`co_version` names a code object, but a repeat at another `PUSH_FRAME` does not
prove that call site's callee is that object
(`overlay/Python/bytecodes.c:6439-6445`). No trace pairs a version fact with an
exact-type or `_GUARD_BINARY_OP_EXTEND` guard on the same value, and every
surviving `_GUARD_BINARY_OP_EXTEND` uses a descriptor whose guard the pass
cannot evaluate (`Python/optimizer_cases.c.h:1371`). Unmatched side traces hold
0.6% of deltablue's samples and 0.1% of hexiom's and raytrace's, 0.05% mean
overall, so counting all of them as removable still leaves 0.11% mean. The sound
share is 0.01% mean, under P1's
1% floor: dropped. Evidence:
`rust-cpython/results/perf-profile/20261110T14*-perf-jit-int-unbox-*-sample`,
dumps and `guard_redundancy.py` under `.work/tmp`. It reopens if upstream stops
proving the guards it removes today, or if T3 gives attribute loads a
trace-local dict fact.

**`_CHECK_VALIDITY` and `_SET_IP` (measured 2026-11-10: 0.0% removable, dropped).**
The backlog's 3.5% mean is what upstream's `remove_unneeded_uops` already
leaves: it nops every `_SET_IP` and restores only the last one before the next
uop with `HAS_ESCAPES_FLAG` or `_PUSH_FRAME`/`_YIELD_VALUE`/`_DYNAMIC_EXIT`/
`_EXIT_TRACE`, and it nops every `_CHECK_VALIDITY` unless an escaping uop ran
since the previous check (`Python/optimizer_analysis.c:724-799`, `:708-713`).
The saved 16 `perf.py profile --tool sample --jit-map` runs on
`perf-jit-int-unbox` give 3.47% mean, 3.18% over the numeric kernels (richards
4.71%, nbody 4.86%): 2.90% `_CHECK_VALIDITY` and 0.57% `_SET_IP` over 227
sampled `_SET_IP` and 835 sampled `_CHECK_VALIDITY` occurrences. Matching each
trace to its executor dump, every sampled `_SET_IP` is followed before the next
`_SET_IP` or trace exit by an escaping uop or a needs-IP uop, and every sampled
`_CHECK_VALIDITY` has an escaping uop since the previous check, so 0 sampled
occurrences are removable. The predicates make both necessary: an escaping uop
can run arbitrary code, which reads `frame->instr_ptr` through `sys._getframe`,
tracebacks, and monitoring, and can invalidate the running executor through the
globals and type watchers (`Python/optimizer_analysis.c:147`, `:156`),
instrumentation (`Python/instrumentation.c:1828`, `:2071`, `:2159`, `:2504`),
function modification (`Objects/funcobject.c:73`), frame-locals proxies
(`Objects/frameobject.c:283`), or `_testinternalcapi.invalidate_executors`
(`Modules/_testinternalcapi.c:1962`); `HAS_ESCAPES_FLAG` comes from a dealloc
outside `NON_ESCAPING_DEALLOCS` (`Tools/cases_generator/generators_common.py:98-104`, `:417`)
or another escaping call (`Tools/cases_generator/analyzer.py:942`). The
pointer is read directly by `_GUARD_IP__PUSH_FRAME`, `_GUARD_IP_YIELD_VALUE`,
and `_GUARD_IP_RETURN_VALUE/GENERATOR` (`executor_cases.c.h:25901`, `:25987`,
`:26077`, `:26167`), `_YIELD_VALUE` (`:10710`), `_DYNAMIC_EXIT` (`:24509`), and
`_COLD_DYNAMIC_EXIT` (`:25521`); `_ERROR_POP_N` writes it (`:25130`), and
`_RETURN_VALUE` uses the caller's value as the return-address base
(`Python/bytecodes.c:1693`, `:6228-6235`; `Python/ceval_macros.h:442`). The
one sampled cluster whose escape can continue, `_GUARD_IS_NOT_NONE_POP`
(richards 0.20%), closes a possibly owned value before continuing
(`Python/bytecodes.c:6209-6213`), so its check is needed too. T2 unboxing runs
after the removal pass and rewrites an escaping `_BINARY_OP_TRUEDIV_FLOAT` to
the non-escaping `_BINARY_OP_TRUEDIV_FLOAT_UNBOXED`
(`overlay/Python/bytecodes.c:991-999`); the saved dumps then hold one stale
`_SET_IP` and one stale `_CHECK_VALIDITY`, both unsampled, so a second removal
pass after unboxing has 0.00% sampled share. The sound share is 0.00% mean,
under P1's 1% floor: dropped. Evidence:
`rust-cpython/results/perf-profile/20261110T14*-perf-jit-int-unbox-*-sample`,
dumps and `validity_split.py`/`validity_verdict.py` under `.work/tmp`. It
reopens if a later trace rewrite replaces escaping uops in a sampled trace
without re-running the removal pass, or if an escape is narrowed to a dealloc
that cannot run code.

### T2: unboxing, toward M2

Keep exact floats and compact ints as machine values between uops; box only at
side exits, calls, heap stores, and trace end. Deopt rebuilds boxed values on
every introspection path the fuzzer covers. Candidates, in order: float
unboxing across binary ops and compares; int unboxing with overflow guards;
unboxed loop-carried locals.

**Float unboxing (landed 2026-11-10, CORRECT).** Branch
`campaign/t2-unbox` at `aadaf25`. A Rust pass in `overlay/Modules/_jit_opt_rs`
runs in `uop_optimize` after the optimizer and before register allocation. It
keeps a float temporary as raw double bits in its stack slot when its only
consumer is another float binary op or compare, nothing else reads it, it stays
within 32 slots of the top, and no escaping, frame-switching, periodic,
variable-shape, or unmodelled failing uop runs while it is live. A chain is
rewritten only when it removes more allocations and frees than the boxing it
adds. A verifier replays the rewritten trace and records the unboxed slots at
every exit and error: exit stubs box them (`_BOX_FLOAT_STACK`; if boxing fails
the slots are cleared and MemoryError is raised), error stubs clear them
(`_CLEAR_FLOAT_STACK`), and a zero divisor deopts so tier 1 raises.

Float allocation, deallocation, guards and pops are 8.9-38.6% of sampled time
on the eight numeric kernels (nbody 38.6%, sparse 24.9%, fft 23.5%). In the
hottest traces the rewrite removes 2 of 15 allocation sites and 2 of 10 frees on
nbody, 2 of 8 and 2 of 6 on fft, and 2 of 6 and 2 of 11 on monte_carlo. The
share of samples in float-op uops and the allocations they call drops from
27.5% to 26.3% (nbody), 11.4% to 9.5% (fft), 6.2% to 5.1% (spectral_norm) and
6.4% to 5.8% (monte_carlo). Most float allocations on these kernels feed stores
to locals or lists, which stay boxed until the loop-carried-locals candidate.
Temporaries also cannot live across a list subscript, because the cases
analyzer marks `_BINARY_OP_SUBSCR_LIST_INT` escaping for its free-threaded
branch.

`climb-step --correctness` against a same-worktree `perf-rust-jit` at
`6040917`: training and held-out digests match, 2,000 fuzz programs give 0
mismatches at 97.5% JIT coverage, and 8 of the 9 targeted suites match.
Four `test_capi.test_opt` tests fail only with the JIT, and under the owner's
ruling (below) they are the candidate's named superseded uop assertions in
`perf.CLIMB_SUPERSEDED_UOP_ASSERTIONS`, which makes the verdict CORRECT: `test_float_add_chain_both_unique`,
`test_float_inplace_chain_propagation`, `test_float_truediv_inplace_unique_lhs`
and `test_float_truediv_unique_result_enables_inplace`. Each asserts that a
`_BINARY_OP_*_FLOAT_INPLACE*` uop appears in a trace that the rewrite fully
unboxes; their numeric results pass. `rust-cpython/tests/jit_unbox_integration.py`
compares JIT-on and JIT-off results, finalizers, refcounts, trace and
monitoring lines over chains that side-exit, deopt into a raised
ZeroDivisionError, trace, monitor, collect, yield and run in a thread, and
checks that each kernel ran unboxed. It fails on the incumbent, whose tier-2
float division raises `float division by zero` where tier 1 raises `division
by zero`; the unboxed division deopts and matches tier 1. JIT code
bytes are at most 1.17x M0 (raytrace, +16.5% over the incumbent from boxing
exit stubs). Peak RSS is +0.3% to +2.0% over the incumbent and at most +6.1%
(go) over the no-JIT sibling `perf-rust` at `aadaf25`, against +4.9% (hexiom)
for the incumbent. `libpython` `__text` grows by 27,328 bytes.

The independent review found nothing above low severity. Its low findings are
fixed in `b5599d2` and `c34ce10`: the FFI wrapper writes the planned steps
through `MaybeUninit` instead of a `&mut` slice over the uninitialized
`PyMem_RawMalloc` buffer; a rejected plan leaves the trace boxed instead of
calling `Py_FatalError` (crate tests show no step is written); the unboxed
stackref helpers compile under `Py_STACKREF_DEBUG`; the `Py_DEBUG` uop
interpreter's `PYTHON_LLTRACE>=4` dump replays the executor's stack shapes and
prints unboxed slots by kind (overlay copies of `Python/ceval.c` and
`Python/ceval.h`); and the integration test feeds owned list items and
attributes into unboxed chains, so a throwaway build whose `_UNBOX_FLOAT_TOS`
skips its close fails it (the old test passed). The dump and a
`-DPy_STACKREF_DEBUG=1` compile of `Python/ceval.c` were checked on a throwaway
`--enable-experimental-jit=interpreter --with-pydebug` build, which the lane
does not otherwise build.

**Int unboxing (CORRECT, 2026-11-10, branch `campaign/t2-int` at
`407096c`).** Ranked first by sampled share on the float incumbent: the
specialized int uops and the runtime they call are 29.6% of samples on
spectral_norm, 17.9% on sor, 9.9% on fft and 7.2% on monte_carlo (8.2% mean
over the eight numeric kernels), while float results stored to locals plus the
frees of the values they replace are 9.8% on nbody and 2.7% mean, and most of
nbody's stores sit across escaping uops where an unboxed local would be boxed
anyway. The planner now has two value domains with the float rules unchanged:
an int node (`_BINARY_OP_{ADD,SUBTRACT,MULTIPLY}_INT*`, `_COMPARE_OP_INT`)
links only to int nodes. An unboxed int is a raw `Py_ssize_t` that is always
compact: `_UNBOX_INT_TOS/NOS` read operands the trace already guarantees
compact, and each unboxed add, subtract or multiply side-exits like upstream
when its result leaves the one-digit range, with both raw operands boxed by the
exit stub. The verifier records float and int slots separately; the stubs are
now `_BOX_UNBOXED_SLOTS` (boxes each slot by kind; on MemoryError clears every
remaining unboxed slot) and `_CLEAR_UNBOXED_SLOTS`. `_BOX_INT` allocates
through the new `_PyLong_FromCompactTstate` on the executor's tstate.

Sampled shares, float incumbent → candidate, same iteration counts and
digests: spectral_norm's int uops and their runtime calls drop from 29.6% to
27.0% of samples (calls into the int runtime 17.0% → 12.0%), with
`(i + j) * (i + j + 1)` in `eval_A` fully unboxed and boxed once. sor, fft and
monte_carlo stay at 17.9% → 18.0%, 9.9% → 10.6% and 7.2% → 6.8%: their chains
are a non-inplace op feeding an inplace one, whose boxed form already allocates
once, so the cost model leaves them boxed (a tie); nbody, float, lu and sparse
have no int chains. Float-op chain shares are unchanged within noise.

`climb-step --correctness` against the float incumbent `perf-jit-unbox`
(`aadaf25`-equivalent overlay) with a clean `perf-jit-int-unbox` at `407096c`:
training and held-out digests match, 2,000 fuzz programs give 0 mismatches at
97.5% JIT coverage, and the targeted suites differ only by the four float tests
above and `test_int_inplace_chain_propagation`, which asserts an inplace int
add in a chain the rewrite unboxes after its result check passes; it is listed
in `perf.CLIMB_SUPERSEDED_UOP_ASSERTIONS`, and the verdict is CORRECT
(advisory point estimate 0.9939). `jit_unbox_integration.py` adds int chains,
an overflow exit, int type exits, owned int list items and attributes, an exit
that boxes a float and an int slot together, and int chains in the trace,
monitoring, gc, generator and thread kernels; its 10 kernels match on the
candidate and on a debug uop-interpreter build of it. JIT code bytes are at
most 1.24x M0 (raytrace, +5.8% over the float incumbent; spectral_norm +19.1%).
Peak RSS is within 0.995-1.005x the float incumbent and +1.1% to +5.7% (go)
over the no-JIT sibling `perf-rust`. `libpython` `__text` grows by 9,664
bytes. The independent review found nothing above low severity, and it landed
as `60e3a7b`. Its follow-up, `06a636c`, pins the offset of every field of
`_PyJitOptUop` and `_PyJitOptStep` in `optimizer.c` and in the crate's layout
test, so reordering two same-sized C fields no longer compiles.

**Array subscript specialization (CORRECT, landed 2026-11-10).** Exact `array.array` loads with an exact non-negative
compact int index specialize in `_Py_Specialize_BinaryOp` for typecodes `d`
and `i`. The type is identified by `PyType_GetModule` plus the module's `array`
dict entry, so a Python class named `array.array` cannot match; its type
version is written into the `BINARY_OP` cache and `_GUARD_NOS_OBJECT_TYPE_VERSION`
rechecks it. `_BINARY_OP_SUBSCR_ARRAY_D`/`_I` bounds-check and read `ob_item`
through `_PyArray_ReadDoubleItem`/`_PyArray_ReadIntItem`, which live in
`Python/specialize.c`; `arrayobject`/`arraydescr` move to
`Include/internal/pycore_arrayobject.h` so core does not link against the
builtin module. Out-of-range and negative-out-of-range indices, non-compact or
non-exact indices, other typecodes, array subclasses and arrays resized mid-loop
all deopt to tier 1, which raises the original `IndexError`. `d` loads box with
`_PyFloat_FromDoubleTstate` and `i` loads with `_PyLong_FromCompactTstate`;
an `i` item outside the compact range (|v| >= 2**30) deopts, since boxing it
as a one-digit int would break equality and hashing. Both results are claimed
unique, which selects the in-place float and int ops downstream; the boxing
error path is still an unbox-pass barrier, so no `_UNBOX_FLOAT_*` forms around
the load. The win is tier 1: `_GUARD_NOS_OBJECT_TYPE_VERSION` has no optimizer
case (as upstream's `_GUARD_NOS_TYPE_VERSION` has none), so traces recheck the
version each iteration. Eliding it needs a recorded NOS type and stays open.
On `scimark_sparse_mat_mult`, `scimark_fft` and `scimark_lu` the sampled share
of generic `_BINARY_OP` with its callees plus the `array_subscr` subtree falls
62.4% → 0.0%, 27.8% → 4.8% and 20.5% → 0.0%; the new load uops take 29.2%,
9.2% and 9.1%. `climb-step --correctness` against `perf-jit-popinline` is
CORRECT (advisory 0.9277): training and held-out digests match, 2,000 fuzz
programs give 0 mismatches at 97.45% JIT coverage, and the suites differ only by
the incumbent's five superseded `test_capi.test_opt` uop assertions.
`rust-cpython/tests/jit_array_subscr_integration.py` compares JIT-on and JIT-off
for out-of-range and negative indices, wrong index types, array subclasses,
mid-loop appends and pops, an `__index__` that resizes, and `f`/`l`/`h`
typecodes, and checks that both load uops run. `jit_unbox_integration.py` is OK;
`test_array`, `test_buffer` and `test_memoryview` pass under `PYTHON_JIT=1` and
`test_capi.test_opt` fails only those five. JIT code bytes are at most 1.30x M0
(raytrace, 457,068 bytes) and 1.23x (spectral_norm); peak RSS is 0.997-1.008x
the incumbent. `perf-climb-ref` is absent in this worktree, so the OD4
`perf-climb-ref` comparison is unverified; against `perf-rust` the four kernels
the early-reject pass reached are +2.0% to +3.6%. Stores (`array_ass_subscr`)
stay generic at 1.3-2.2% of fft/lu samples and were not specialized. Timing is
deferred under OD14.

**Generic `_BINARY_OP` specialization (CORRECT, 2026-11-10).** On
`perf-jit-array` (`b356e9f`), generic `_BINARY_OP` with its callees is 9.6% of
samples on `scimark_monte_carlo` (374/3912; the 19.9% backlog number predates
the array-load slice) and 11.6% on `nbody` (482/4162). The monte_carlo site is
`Random.nextDouble`'s `k += self.m1`: exact float plus the exact int `2**31-1`,
which is not compact. The nbody site is `advance`'s `(...) ** (-1.5)` and
`report_energy`'s `** 0.5`: exact float ** exact float; the exponents are
float constants, not ints. `_Py_Specialize_BinaryOp` now selects
`BINARY_OP_ADD_FLOAT_INT` for `NB_ADD`/`NB_INPLACE_ADD` when it first sees
that non-compact shape (a site first seen with a compact int takes the existing
`BINARY_OP_EXTEND` float-long descriptors; a site already specialized here
also accepts compact ints) and `BINARY_OP_POWER_FLOAT` for
`NB_POWER`/`NB_INPLACE_POWER` on two exact floats, unless the operands seen
are a negative base and a fractional exponent, which stay generic. The uops use `_GUARD_NOS_FLOAT` with `_GUARD_TOS_EXACT_INT`,
and `_GUARD_TOS_FLOAT` with `_GUARD_NOS_FLOAT`; the add converts through
`_Py_convert_int_to_double` and boxes with `_PyFloat_FromDoubleTstate`; the pow
calls `_PyFloat_PowExactTstate`, which mirrors `float_pow` case for case, boxes
with `_PyFloat_FromDoubleTstate`, and signals a deopt for the negative-base
non-integral-exponent complex result, so every success result is a unique
float. Neither uop is `pure`, since both can raise. Both helpers are in `NON_ESCAPING_FUNCTIONS`;
`_PyFloat_PowExactTstate` and `_Py_convert_int_to_double` are exported because
`_testinternalcapi` compiles the uop executor. Generic `_BINARY_OP` with
callees falls 9.6% → 0.0% on monte_carlo, replaced by
`_BINARY_OP_ADD_FLOAT_INT` at 8.8% (344/3900), and 11.6% → 0.0% on nbody,
replaced by `_BINARY_OP_POWER_FLOAT` at 6.4% (269/4211). `climb-step
--correctness` against `perf-jit-array` is CORRECT (advisory 0.9992): training
and held-out digests match, 2,000 fuzz programs give 0 mismatches at 97.45% JIT
coverage, and the suites differ only by the five incumbent superseded
`test_capi.test_opt` uop assertions. `jit_genops_integration.py` compares
`operator.add`/`float.__pow__` references and JIT-on/off, and checks bit-exact
expected values through sites it first confirms are specialized (literal
operands are constant-folded and never reach `BINARY_OP`). An independent
review found no correctness defect over 608 add and 17,424 pow operand pairs
against the unspecialized build; its low findings (the folded literal checks,
an off-by-one masked by a converging reference, and churn on always-complex
sites) are fixed. JIT
code bytes are at most 1.303x M0 (raytrace, 457,068); monte_carlo is 66,156
and nbody 39,416. Peak RSS is 0.989-0.995x the incumbent on all 16 training
kernels. `perf-climb-ref` is absent, so the OD4 reference comparison is
unverified. Timing is deferred under OD14.

**Unboxed loop-carried locals (dropped 2026-11-10: 0.0% achievable).** Any uop
that can run arbitrary code can observe a local through `frame.f_locals`,
`locals()`, tracing, monitoring, tracebacks, or the GC's frame traversal. A raw
float or int in a local must therefore be boxed at every barrier between its
store and the next store of that local, across the backedge when the value is
loop-carried. The store's allocation and the free of the value it replaces are
saved only when that interval has no barrier. Profiles of all 16 kernels on
`perf-jit-int-unbox` (`perf.py profile --tool sample --jit-map`, about 8 s
each) were matched to executor dumps that carry opargs. Float results stored
to locals, plus the frees of the values they replace, are 15.0% of samples on
nbody, 3.3% on spectral_norm, 3.1% on fft, 2.4% on sparse, and 0.7% on
monte_carlo: 3.1% mean over the eight numeric kernels. Int stores are 3.5% on
fft and 2.1% on monte_carlo, 0.7% mean. Under the planner's barrier rules, no
such interval on any kernel is barrier-free, so 0.0% is achievable. Three
waivers still leave 0.0%: treating `_CHECK_PERIODIC` as a boxing exit, and
treating both the generic `_POP_TOP` of replaced values and the list subscript
load and store as non-escaping. Reaching 2.2% (numeric mean; nbody 13.1%) also
needs generic `_BINARY_OP` (nbody's `**`, `array` subscripts in fft and
sparse), `_UNPACK_SEQUENCE_*`, `_ITER_NEXT_LIST_TIER_TWO`, and generic
`_STORE_SUBSCR` to stop escaping. Even `for i in range(n): s = s * 0.5 + x`
keeps `s` boxed, because the loop head does not know the type of the replaced
`i`, so its generic `_POP_TOP` escapes. The mechanism falls under P1's 1% floor
and is dropped. It reopens once those barriers are gone.

**Backlog re-rank (2026-11-10).** These shares come from the same 16 profiles.
Each counts the samples whose innermost JIT frame is in the item's uops,
callees included. Means are over all 16 kernels, the eight numeric kernels,
and the eight object kernels:

| Rank | Item | Tier | Mean | Numeric | Object | Peak |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1 | Frame push and pop: `_RETURN_VALUE` with its frame clear and pop (6.0% of samples alone), `_INIT_CALL_PY_EXACT_ARGS_*`, `_PUSH_FRAME`, `_TIER2_RESUME_CHECK`, `_CHECK_STACK_SPACE*`, `_CREATE_INIT_FRAME` (measured 2026-11-10: 1.3% removable as pop fast-path inlining, see **T1**) | T1 | 13.0% | 7.9% | 18.1% | richards 35.3%, raytrace 31.6%, deltablue 25.7%, sor 21.3% |
| 2 | Type and version guards, all of them; upstream's optimizer has already removed those it proves (measured 2026-11-10: 0.01% removable under trace-local facts, dropped under P1's 1% floor, see **T1**) | T1 | 8.5% | 6.4% | 10.7% | richards 18.9%, go 17.7% |
| 3 | `_CHECK_VALIDITY` and `_SET_IP` (measured 2026-11-10: 0.0% removable; upstream's removal pass already keeps only observers and post-escape checks, see **T1**; dropped under P1's 1% floor) | T1 | 3.5% | 3.2% | 3.8% | nbody 4.9% |
| 4 | Residual `_tlv_get_addr`, from `float_dealloc`, `PyFloat_FromDouble`, `PyLong_FromLong`, and `long_dealloc` on generic paths | T1 | 3.0% | 3.2% | 2.9% | nqueens 6.8%, fannkuch 5.8%, fft 5.7% |
| 5 | Unboxed loop-carried locals (above) | T2 | 0.0% | 0.0% | 0.0% | ceiling nbody 15.0% |

Two newly dominant costs join the backlog under P1. Generic `_BINARY_OP` is
11.4% mean and 18.2% numeric: `array` subscripts are 53.3% on sparse, 26.2% on
fft, and 17.0% on lu, monte_carlo's generic ops are 19.9%, and nbody's `**` is
8.9%. Specializing these is T2. Generic decref pops are 8.5% mean (nbody 24.3%,
mostly replaced locals of unknown type). Typing those values across the
backedge needs T3's loop peeling.

**Borrowed double-array producers (2026-11-13).** Rust unboxing now connects
eligible `array.array('d')` reads to existing float arithmetic. Array and
index inputs stay boxed. The planner selects a connected component only
when it reduces boxing cost. A single escaping read stays unchanged.

The original type and compact-index guards remain. Array cleanup must be
borrowed, while index cleanup may be borrowed or exact-integer cleanup.
Only NOPs may separate the original read and those cleanups. The new
`_BINARY_OP_SUBSCR_ARRAY_D_UNBOXED` refuses a mortal owned array before
consuming inputs. It reuses the original typecode, negative-index and bounds
helper, closes the exact index and returns a raw double without allocation.
Owned arrays retain the boxed path and its finalizer timing.

The private unboxing descriptor adds array-read kind 7 and closed pop details
0, 1 and 2 for other, borrowed and integer cleanup. Synthetic action 16 is
appended. Existing action numbers remain fixed, while the supplied synthetic
table grows from 15 to 16 entries. Descriptor and step widths remain 8 and
16 bytes. C and Rust must come from the same overlay. Invalid descriptors,
short output or allocation refusal preserve the original trace.

Original source indices and targets carry the exit masks. The native
four-read fixture requires raw-array instructions and masks 4 and 12 before
later guard failures. Bounds at every read, arbitrary index callbacks,
storage mutation, aliasing and owned-array weakrefs retain exact errors,
progress and ownership. Float bits cover zeros, subnormals, NaNs and
infinities. Trace, profile and monitoring callbacks, allocation observers
and deterministic result or exit-boxing failures also pass on both variants.
The separate allocation-site prerequisite remains required.

All 95 Rust tests and the targeted Rust FFI AddressSanitizer test pass.
Independent source and final generated/native reviews approve both variants.
The rebase onto the allocation-site fix preserves the optimizer patch ID and
Rust source. Earlier builds are retained separately as historical evidence.
Timing acceptance stays deferred under OD16.

All 34 native integrations pass. The 18 focused suites retain the same
16 reviewed shape exclusions. All 2,000 fuzz programs match, with 1,949
JIT-active cases, and all 31 workload digests agree. Five interleaved
resource trials pass: matched RSS at most 1.0063x incumbent, isolated Serc
footprint at most 1.0355x no-Serc and code at most 1.8792x M0. Raytrace stays
at 659,248 bytes. Every trial emits four raw reads in sparse matrix multiply,
two each in SOR and LU, and twelve in Monte Carlo. FFT emits none. Borrowed
cleanup alone does not establish an eligible connected component or a
boxing-cost reduction. These are emitted instruction counts, not speedups.

### T3: inlining and allocation removal, toward M3

Inline Python-to-Python calls with lazy frame materialization on deopt. Add
attribute and global inline caches whose invalidation honours descriptors,
mutation, inheritance, lazy imports, and subinterpreters. Remove non-escaping
allocations. Lengthen traces with loop peeling and invariant-guard hoisting.

**Frame-free leaf calls (2026-11-11).** The Rust planner
`Modules/_jit_opt_rs/src/leaf.rs` fuses a complete, guarded exact-argument
call whose optimized body returns an argument, constant or preguarded slot. The new
`_CALL_PY_LEAF` consumes the caller's arguments and produces the result
without constructing a callee frame. Existing function-version, recursion,
stack-space and PEP 523 checks remain before the call. The fused instruction
also checks argument shape, executor validity and instrumentation before
consuming anything; every refusal resumes the original call in tier 1.
`_SAVE_RETURN_OFFSET` remains so finalizers observe the original caller IP.
The result is made heap-safe before releasing arguments in reverse order,
then self, function and executable. No guard, callback, allocation or side
exit remains inside the removed body. Other callees keep ordinary frames.

The private `_PyJitOpt_PlanLeafCalls` ABI takes opcode identities from C's
generated table and returns transactional rewrite steps. Its two 32-bit
instruction caches hold a constant address or a tagged argument index with an
optional slot byte offset; Rust never dereferences Python objects. Unsupported bodies and
allocation failure preserve the original trace. The pass is disabled for
free-threaded and DTrace builds. Future bucket-(a) profiles must count
`_CALL_PY_LEAF` as call work, alongside retained guards.

`jit_leaf_integration.py` checks seven fused traces, including bound self,
six arguments and a `None` result, against both JIT-off results and absolute
expectations. It checks code rebinding and executor invalidation, argument,
function and code finalizers, caller frame and exception state, tracing,
profiling, monitoring, generator callers and threads. Three additional
upstream instruction-shape assertions are named in
`CLIMB_SUPERSEDED_UOP_ASSERTIONS`: `test_func_guards_removed_or_reduced` and
`test_func_version_guarded_on_change` require `_PUSH_FRAME`, and
`test_make_heap_safe_optimized_immortal` requires `_RETURN_VALUE`. Their
replacement behavior and guards are covered by the integration test under
the existing owner ruling.

Slot getters are eligible only when the optimized body loads a borrowed
argument, reads its slot and discards the borrowed owner before returning.
Any surviving type guard rejects fusion: the existing optimizer must already
have proved the owner layout before the call. The fused instruction reads the
slot before consuming arguments and deopts at the original call if it is
missing. Tier 1 then constructs the getter frame and raises the original
exception. The slot result acquires its own reference before the receiver can
be finalized. `jit_slot_leaf_integration.py` checks five getter traces,
including an argument getter, inherited and unrelated owner layouts, descriptor invalidation,
tracing/monitoring, and a finalizer that deletes the slot while the caller is
running in the JIT. The latter must retain the getter's traceback frame.

**Inline instance getters (2026-11-12).** The same leaf planner recognizes
borrowed argument loads followed by the managed-inline-values validity check,
instance-value load and complete borrowed-owner cleanup. Existing type guards
must already be discharged before the call. The private leaf opcode map grows
from 80 to 84 bytes; result tag 3 records an inline instance value using the
existing argument/byte-offset fields.

The fused call retains the mutable storage check at runtime before reading
that offset. Replaced dictionaries, invalid inline storage and missing values
take the original call path before consuming inputs; the ordinary callee
therefore produces the original error frame. The result acquires ownership
before reverse argument/function/code cleanup. Descriptor/type invalidation,
instrumentation and function guards retain their existing boundaries.

The slot-getter native regression now shares behavioral checks across slot
and managed layouts, including unrelated layouts, refcounts, finalizers,
missing-value errors and observers. Separate managed mutation cases each
start in a fresh child process. An earlier caller-side attribute read proves
the argument layout; an active-JIT finalizer then replaces or empties only
that argument's dictionary before the getter. Both cases prove tag 3 fusion,
active execution at the call and original results or callee traceback.
JIT-off and optimizer-disabled modes match all absolute behavioral checks.

One additional upstream shape assertion is named under the existing ruling:
`test_guard_type_version_locked_propagates` now sees one concrete frame and
one fused managed getter rather than two concrete frames. Independent review
ran the original method with only that count changed in memory; its numeric,
one-type-guard and function-version assertions all passed.

Global-return getters retain a pre-call `_CHECK_LEAF_GLOBALS_VERSION` that
reads the called function's globals dictionary. The Rust planner moves one
remaining globals guard from the pure callee body to the original call site;
it keeps the existing dictionary watcher dependencies and constant result.
The guard and the leaf call both retry at the caller's call instruction.
Dictionary value replacement invalidates the executor before the old object
is decref'd, so the call's validity check protects cached mortal results.
Unresolved lazy imports and dictionary-subclass lookups remain ordinary
calls because their loads are not constant-folded by the existing optimizer.
`jit_global_leaf_integration.py` checks separate caller/callee dictionaries,
shared-code functions, replacement, deletion, clear/update, builtin fallback,
cached object collection, dictionary callbacks, lazy lookup errors and
observer frames.

**Immediate tuple results (2026-11-11).** The leaf planner recognizes a
complete two-argument call that returns both borrowed arguments, once each,
in either order, followed immediately by a two-tuple unpack. It replaces the
callee frame, temporary tuple and unpack with `_CALL_PY_LEAF_PAIR`. Escaping
results, extra operations, bound calls, cells and free variables retain the
ordinary path. Its private opcode map grows from 54 to 62 bytes; pair steps
encode forward or reversed argument order.

Both output references become heap-safe before any input is released. A
callable whose last owned reference would be closed takes the original call
path: function or code finalizers can observe the concrete return tuple with
`gc.get_referrers`. All refusals use a side exit before consumption, including
this lifetime guard. No callback or safepoint remains between the original
call and consumed unpack; interstitial validity and instruction-pointer
updates disappear with the tuple. Trace, profile and monitoring observers
continue to see real callee frames and distinct return tuples.

**Immediate list results (2026-11-12).** The same pair-call instruction
also removes an exact two-element list followed immediately by a list unpack.
The Rust state machine retains container kind until the unpack and rejects
cross-container or wrong-arity matches. The opcode map grows from 62 to 66
bytes. It also consumes the effect-free validity check emitted after list
construction; leaving that check would retain callee-stack deopt metadata
for an allocation that no longer exists. Construction and unpack on the
GIL-only exact-list path have no Python callback window. Runtime ownership
and lifetime guards remain the same as for tuples.

The parameterized native regression checks six actual tuple/list traces with
explicit container type and fresh escaping identities, forward/reverse and
aliased results, reference counts, sole-callable finalizers, observers, code
rebinding and cell fallback. Independent source and native review approves.

**Float-call error materialization (2026-11-12).** The Rust leaf planner
recognizes two borrowed argument loads, optional exact-float guards, one
addition/subtraction/multiplication/division, complete borrowed-operand
cleanup and return. It records operation identity, argument order and the
callee arithmetic offset; the private opcode map grows from 66 to 80 bytes.
`_CALL_PY_FLOAT_LEAF` checks exact types, call shape, executor and
instrumentation before consumption. Successful arithmetic uses the existing
float allocation helper and frame-free return cleanup. Existing function,
recursion, stack-space and PEP 523 guards and return offset remain.

On error the instruction leaves all four CALL inputs and the pending
exception untouched. `prepare_for_execution` selects a dedicated
`_MATERIALIZE_FLOAT_LEAF_ERROR` stub carrying the callee offset and operand
order. The stub transfers callable/argument references into the already
space-checked datastack frame, restores both borrowed arithmetic operands,
publishes the consumed caller stack, links the child and enters tier 1's
normal error handler. No allocation, retry, exception clearing or caller
unboxed cleanup occurs in the stub. Its zero-output cache variant preserves
the reconstructed child stack during the terminal transfer. Success keeps
callee frames virtual; additional operations, locals requiring reconstruction,
cells, free variables and resumable callees retain their ordinary calls.

The native regression forces division and result-allocation failures while
the caller is executing JIT code, checks exact traceback positions, callee
argument identities, outer exception context and sole-callable lifetime,
and retains return outputs to drain the float freelist before a one-shot
allocator failure. It also checks all four operations in both argument
orders, special float values, success finalizers, subclass fallback,
invalidation and observer frames.

**Virtual float-call operands (2026-11-12).** Longer straight-line float
callees now retain their original arithmetic instructions while avoiding
ordinary frame initialization, argument-to-local copying and frame
registration on success. The Rust leaf planner recognizes borrowed argument
loads, at least two exact-float operations, complete typed/borrowed cleanup
and heap-safe return. The private opcode map grows from 84 to 108 bytes.
Unused locals, cells, free variables, resumable/bound calls, non-float
arguments, callbacks, constants and unsupported operations retain ordinary
calls. This is a closed float-body slice, not general frame virtualization.

`_BEGIN_VIRTUAL_FLOAT_CALL` retains existing function-version, recursion,
stack-space and PEP 523 checks and checks exact argument types and
instrumentation before consumption. It publishes the consumed caller stack
while holding the CALL references in their original reserved slots. The
already checked callee reservation supplies a separate operand stack with a
minimal unlinked frame header; the running thread-state frame remains the
caller at its original CALL IP. A callee stack may exceed the caller's
capacity without extending the caller's stack. Borrowed argument loads read
the held slots. The scratch reservation remains exclusive until return or
materialization; no body operation executes Python or releases the GIL.

Original in-place forms are normalized to ordinary boxed arithmetic together
with their former NULL cleanup. The proof distinguishes fresh unique
intermediates from shared heap-safe arguments. This preserves the
interpreter's allocation/error boundaries for the initial implementation.
Traces containing these virtual calls stay boxed throughout; restoring
unboxing and in-place reuse requires a further proof. These restrictions can
affect performance, which has not been accepted under OD16.

`_RETURN_VIRTUAL_FLOAT_CALL` makes the result heap-safe, holds the code and
closes the scratch executable borrow before restoring the real caller.
Arguments, function and code are then released in the original order; a
finalizer may immediately reuse scratch without a later read of that area.
Begin/return use explicit register-cache boundaries. New error stubs close
the scratch borrow, initialize the same reserved address with the held
callable/arguments, restore the untouched operand-stack top, link the child
and decrement recursion once. Original arithmetic targets supply the callee
PC. The terminal zero-output stub preserves the pending exception and does
not allocate, retry, clear operands or overwrite the restored SP. Debug
stack-reference borrows and SP validity follow the same lifetime boundaries.
An explicit transactional bound covers cache transitions and error/exit
stubs before any rewrite is committed.

The native regression compares JIT-on, JIT-off and disabled optimization,
including a callee stack larger than its caller's, distinct first/second
error PCs, one-shot allocation failure at a later operation, sole-callable
traceback lifetime, multiple virtual calls, scratch-reusing function/code
finalizers, subclass fallback, code rebinding and profile/trace/monitoring
observers. All seven fault/finalizer activity checks prove JIT execution.
Independent source, generated-code and native review approves.

**Virtual float locals (2026-11-12).** The same reserved-stack model now
supports nonargument locals holding heap-safe exact floats or NULL. Arguments
remain in held caller storage on success. The private opcode map grows from
108 to 116 bytes, and begin carries a 32-bit minimum locals-plus extent.
The runtime checks that extent against the actual callee and initializes
every nonargument slot to NULL, including slots first assigned after a fault.

The Rust proof tracks assigned slots, shared aliases, fresh unique results,
possible borrowed argument copies and borrows anchored to individual locals.
Stores remove uniqueness and require a heap-safe float. Argument copies keep
`PyStackRef_DUP` semantics, including its possible borrowed tag, and need
`MAKE_HEAP_SAFE` before storage. Argument writes without a numeric proof or entry guard, unassigned reads, unsupported
operations and overwriting a local while its borrow is live retain ordinary
calls. Proved NULL/float old-local cleanup becomes no-op/typed cleanup.

Success restores the real caller before closing extra locals in reverse
order, followed by held arguments, function and code. Extra slots contain only
NULL or exact floats, so their cleanup cannot execute Python; every scratch
read finishes before function/code callbacks can reuse that reservation.
Error initialization preserves the existing extra-local array, then fills
argument slots from held references and restores the operand-stack top.
Tracebacks therefore expose assigned values and aliases while omitting NULL
slots, with no error-path allocation or retry. Boxing and arithmetic
normalization restrictions remain unchanged.

Native coverage compares JIT-on/off and disabled optimization for assigned
and unassigned error locals, overwritten aliases, reference counts, later
allocation failure, argument-write fallback, unbound-local errors, observers
and finalizers reusing scratch. A generator caller additionally checks held
references above a heap-owned caller stack. All six fault/finalizer activity
checks prove JIT execution. The full correctness pass was followed by the
independent generator-case run; compiled sources did not change between them.
Independent source, generated-code and native review approves.

**Virtual-call side exits (2026-11-12).** Exact-float power can now remain
inside a virtual call. The existing helper returns a fresh float on success,
a non-consuming side exit for a complex result and a pending exception on
error. The Rust success proof preserves original operand order and cleanup;
complex fallback permanently leaves that proof. Single-operation, repeated-
argument and single-argument scalar bodies also use the general virtual path
when the earlier compact leaf-call shape does not apply. The private opcode
map grows from 116 to 118 bytes.

The terminal stub is renamed `_MATERIALIZE_VIRTUAL_FLOAT_FRAME`. Its private
selector is 0 for pending-error propagation and 1 for interpreter resumption;
both branches have zero outputs and cache depth zero. A side exit spills
cached operands before reconstructing the same locals/stack as error
materialization, then resumes the original callee instruction. Earlier
arithmetic and caller argument evaluation are not repeated. Error transfer
keeps the pending exception and never retries. Begin's own guards remain
ordinary caller exits; virtual context activates only after them. Jump/error
sharing resets across region boundaries, and the transactional capacity bound
counts the additional side-exit spill and materializer.

The native regression compares JIT-on/off and disabled optimization across
ordinary/special float powers, repeated complex fallback, later complex/real
division errors, overflow, zero-to-negative-power errors, one-shot allocation
failure and a generator caller. It requires 29 active-JIT samples and exact
source-consumption counts, along with single/repeated-argument fusion.
Independent source/generated/native review approves. The actual tail checker
also passes its assertion-enabled regression with the renamed materializer;
this does not claim a full debug interpreter build. Boxing and in-place
normalization restrictions remain in force, with performance deferred.

**Bound virtual float methods (2026-11-12).** The existing float-call
virtualizer now supports an object receiver and guarded slot or inline
instance reads. The receiver remains a borrowed value anchored by the held
CALL self reference. Attribute values remain owned and unknown until an
original exact-float guard succeeds; unsupported uses preserve concrete
frames. Original type/layout proofs and managed-storage validity checks
remain in place. Missing attributes and non-float values materialize at the
original callee instruction, preserving earlier work and its operand stack.

The private opcode map grows from 118 to 122 bytes. Begin's second cache now
selects standalone (0) or bound (1) shape, checked before consuming inputs.
Explicit arguments still require exact floats; receiver presence shifts
argument indices and the parameter boundary. Reconstruction moves
self into local 0 and explicit arguments into the following slots. Success
closes all extra float locals before arguments, self, function and code, so
receiver callbacks cannot reuse scratch before its final read.

`jit_virtual_methods_integration.py` compares JIT-on/off and disabled
optimization for slot and managed layouts, zero/three explicit arguments,
non-float and float-subclass fallback, missing fields, dictionary replacement
and clear after bound-method lookup, descriptors, class changes, error locals
and source consumption. Twenty activity checks include a last-reference receiver whose destructor
rebinds the callee code, invokes nested JIT code and collects garbage. Traces
prove virtual entry, return and materialization. Whole-trace boxing and
in-place normalization restrictions remain; general scalar bodies and
callback-capable bodies still use concrete frames. The full correctness pass
was followed by the strengthened held-method regression; compiled sources
did not change. Independent source/generated/native review approves.

**Constants and argument use-site guards (2026-11-12).** Virtual float
bodies now retain inline constants and accept unused arguments of arbitrary
type. Begin no longer requires every explicit argument to be an exact float.
The Rust proof keeps required float guards and inserts missing guards
immediately before arithmetic, using that operation's original callee PC.
Failure reconstructs its full pre-operation stack without replaying earlier
arithmetic. Successful argument guards also prove later loads of that same
parameter until an accepted store replaces its type proof.

Type proof does not change ownership. Borrowed constants and arguments stay
borrowed; copied arguments may still be borrowed until heap safety is explicit;
owned constants and attributes become shared floats. Constants remain opaque
addresses to Rust. Unknown values cannot enter extra locals, generic cleanup
or the return result. Held unused arguments are released in reverse order
after all scratch reads; they can invoke Python or change the callee code.

Guard insertion exercises the private leaf planner's existing variable-length
output contract. C checks output count, source indices and the complete
spill/exit/error bound before writing. Every output instruction comes from
an immutable original snapshot; inserted guards retain the arithmetic target
and clear unused caches. Allocation failure or insufficient capacity leaves
the original recipe unchanged. The returned length reaches every following
optimizer pass; private ABI layouts are unchanged.

The regression checks constants, unused objects, non-float fallback after an
assigned float local, later division errors and second-operation allocation
failure. Two last-reference unused arguments finalize in reverse order; the
first callback changes the callee from three parameters to one, invokes nested
JIT code and collects garbage before the remaining argument is closed. Six
activity samples and exact source counts pass in JIT-on/off and disabled
optimization modes. Rust tests cover owned/borrowed constants and expanded-
output refusal without partial writes. Independent source/generated/native
review approves; performance acceptance remains deferred under OD16.

**Integer scalar virtual calls (2026-11-12).** The same virtual-frame
proof now supports compact integer addition, subtraction and multiplication,
including their existing left/right in-place helpers. Scalar kind and
ownership are separate. Successful helpers produce compact integers, which
may be cached immortal values; fresh origin does not imply physical uniqueness.
Arguments, constants and shared local loads cannot become reuse targets.
The in-place helper retains its operand references and duplicates its result,
so the proof requires both operand pops before any further value use or store.
Float in-place normalization remains unchanged.

Original integer type, compactness and overflow guards remain. Unknown values
receive a full exact-and-compact guard before an overflow-only check can inspect
their layout. Local overwrites clean the previous value with its previous kind,
while aliases preserve their own kind and borrowed-local anchors prevent early
overwrites. Slot and managed receiver attributes, integer constants, mixed
integer/float locals and generator callers use the same reconstruction path.
Mixed arithmetic itself remains separate work.

Compact helpers return NULL without a pending exception for overflow or
allocation refusal. Both cases materialize at the original arithmetic PC and
resume in tier one, with earlier assignments and all operands intact. The
regression requires one-shot allocation recovery and sustained allocation
failure; available callee traceback state must be exact. Memory exhaustion
can itself prevent traceback allocation. Intermediate injected failure counts
are not required to match across modes: the ordinary cold-exit path can spend
allocations on tracing or retries that terminal materialization bypasses.
No strict allocation-attempt parity is claimed.

The private runtime names are now `_BEGIN_VIRTUAL_CALL`,
`_LOAD_VIRTUAL_ARGUMENT`, `_COPY_VIRTUAL_ARGUMENT`, `_RETURN_VIRTUAL_CALL`
and `_MATERIALIZE_VIRTUAL_FRAME`; their old float spellings are removed.
The private opcode map grows from 122 to 150 bytes. The specialized
`_CALL_PY_FLOAT_LEAF` remains unchanged. Virtual regions stay boxed while
independent outer arithmetic can still be unboxed.

Native tests compare ordinary results across JIT-on/off and disabled
optimization, with 68 activity samples across slot and managed layouts.
They cover cached and mortal integers, both reuse directions, bool/subclass/
noncompact fallback, helper overflow followed by a callback that inspects
reconstructed locals, mixed-kind overwrites, reference counts, generator
callers and allocation refusal. Four additional original tests require physical
frame uops that are now virtual: both `test_call_py_exact_args` methods,
`test_make_heap_safe_not_optimized_for_owned` and
`test_record_bound_method_exact_args`. They are listed individually in the
existing shape-exclusion set. Independent review ran every original method
with only that obsolete name translated; every other assertion passed.
CPython tests and suite thresholds are unchanged. Independent source/generated/
native review approves; the final rebuild includes the cleanup restriction and
repeats all correctness checks. Performance acceptance remains deferred.

**Mixed float/int virtual calls (2026-11-12).**
The Rust proof accepts existing `_BINARY_OP_ADD_FLOAT_INT` with an exact
float on the left and an exact integer on the right. `CompactInt` implies
`ExactInt`; narrowing preserves the stronger proof and reference ownership.
The private C/Rust opcode map grows from 150 to 154 bytes. Instruction and
rewrite-step layouts are unchanged. The existing conversion and allocation
operation retains its original callee target and operand references.

`jit_virtual_mixed_integration.py` checks JIT-on/off and disabled-optimizer
parity for convertible large integers, conversion overflow after an earlier
local assignment, refcounts, single source consumption, and one-shot and
sustained allocation errors. Faults preserve the original arithmetic PC and
available locals. Caller globals remain private and stable during warming,
so test bookkeeping cannot invalidate their watched executors. All six
activity samples pass. The incumbent retains physical frames for this path.
The candidate passes correctness and corpus parity. Independent Codex worker
source/generated/native review approves under OD17. A final strengthened
one-shot OOM assertion requires the original arithmetic traceback PC; sustained
exhaustion may prevent traceback allocation. Performance acceptance remains deferred.

**Unary scalar virtual calls (2026-11-12).** The Rust virtual-call proof
accepts existing `_UNARY_NEGATIVE` only for proven exact numeric operands.
Compact negation retains compact signed magnitude; other integer results stay
exact integers. Generic negation retains the input reference and its original
callee error target. Existing `_UNARY_NEGATIVE_FLOAT_INPLACE` requires a fresh
float and retains its infallible transfer plus NULL cleanup. Unknown or
callback-capable operands keep concrete frames. No runtime opcode is added;
the private opcode map grows from 154 to 158 bytes.

`jit_virtual_unary_integration.py` checks JIT-on/off and disabled-optimizer
parity, negative zero, NaN, infinity, compact extrema, large exact integers,
shared locals, reference counts, callbacks and single source consumption.
Separate caller code objects and stable globals prove both unary variants
across eleven traces and 35 active samples. One-shot integer and float OOM
must retain the original unary PC and assigned locals; sustained exhaustion
may prevent traceback allocation. Independent source/generated/native review
approves. Performance acceptance remains deferred under OD16.

**Integer inversion in virtual calls (2026-11-12).** The Rust proof accepts
existing `_UNARY_INVERT` only for proven exact integers. Its result is always
classified `ExactInt`: the complement of compact `2**30 - 1` is noncompact
`-2**30`. Subsequent compact arithmetic retains its original guard. Input
ownership, cleanup and the original unary error target remain intact. The
private opcode map grows from 158 to 160 bytes. No runtime opcode is added.

`jit_virtual_invert_integration.py` checks compact extrema, large integers,
aliases, reference counts, source consumption, callback fallback and original
unary OOM PCs. A later multiplication callback sees the assigned locals and
original multiplication PC after compactness refusal. Unknown operands keep
ordinary frames. JIT-on, JIT-off and disabled-optimizer modes agree. Optimized
JIT checks require 16 active samples and five virtual traces, including
allocation faults.

An allocation-stack probe first exposed an incumbent defect in both allocating
fused entries. The separately accepted observer fix preserves the callee
allocation frame. Inversion was rebased onto that fix and
rebuilt before repeating all correctness checks. The original probe now passes
with the JIT on and off. Independent inversion review remains valid because
its three reviewed files are unchanged after rebase. The shared guard has its
own independent source, generated-code and native approval. Performance stays
deferred under OD16.

**Allocation-observer frame visibility (2026-11-12).** Both allocating fused
entries, `_CALL_PY_FLOAT_LEAF` and `_BEGIN_VIRTUAL_CALL`, exit at the original
call before consuming inputs when a reference tracer is installed or memory
tracing is active. The incumbent omitted the callee from allocation stacks.
Ordinary frames preserve the allocation filename and line while leaving the
existing fast path available without observers.

Reference tracers are published with all interpreter threads stopped before
tracemalloc sets its atomic tracing flag. The guard covers that transition
and other reference observers without acquiring a lock or reading trace tables.
The case generator now classifies the integer atomic load beside its existing
non-escaping pointer loads. The first build exposed that missing classification;
full target generation fails before the change and passes afterward. A retry
of the configured tree verifies all 58 Rust extensions. No Rust planner or
private opcode-map layout changes.

`jit_tracemalloc_integration.py` retains input and result floats and exhausts
the freelist, so recorded allocations cannot inherit unrelated object history.
It checks both fused entries with tracing enabled before warming and after
warmed executors exist, plus source consumption, post-stop progress and
trace/profile/monitoring callbacks. Interpreter control passes, the incumbent
JIT fails on a missing callee, and the fixed JIT and disabled-optimizer modes
pass. Independent source/generated/native review approves. Performance stays
deferred under OD16.

**Allocation-site tracing (2026-11-13).** Ordinary physical JIT frames can
still report a function's definition line for an array-result allocation.
The optimizer removes instruction-pointer updates around non-escaping
operations. Keeping a physical callee alone does not preserve allocation
lines.

While `tracemalloc` is active, executor entry uses the existing tier-one
fallback and the optimizer declines new traces. A retained validity check
after an escaping operation deopts when a callback starts tracing. CPython's
escape metadata preserves those checks after physical returns, virtual
returns and generic reference cleanup. Rust unboxing treats escapes as
barriers, so raw values cannot cross those callback boundaries. Cached
executors remain attached and become usable again after tracing stops.

The regression retains fresh float and noncached integer results, clears
freelists and checks exact filenames and lines. It covers tracing before
warming, after warming and inside a finalizer. Physical argument cleanup,
virtual held-argument cleanup and an owned-array weakref also enable tracing
from hot execution. All nine incumbent cases report wrong lines while their
interpreter and disabled-optimizer controls pass. The fixed variants pass
the same checks. Cold, warm and finalizer cases also require JIT resumption
after tracing stops. Independent source and final generated/native reviews
approve. No private ABI or Rust planner changes.

Both clean JIT variants verify all 58 Rust extensions. All 33 native
integrations pass. The 18 focused suites retain the same 16 reviewed shape
exclusions. All 2,000 fuzz programs match, including 1,949 JIT-active cases.
Five interleaved resource trials pass: matched RSS at most 1.0014x incumbent,
isolated Serc footprint at most 1.0372x no-Serc and code at most 1.8792x M0.
Raytrace maps 659,248 bytes in all trials. Timing acceptance stays deferred
under OD16.

**Numeric parameter reassignment (2026-11-12).** Held argument slots remain
the canonical parameter storage. `_SWAP_VIRTUAL_ARGUMENT` replaces a parameter
and returns its old reference for typed numeric cleanup. Both values must be
proven exact numbers, and the replacement must be heap-safe. Error and side-exit
materialization already moves the current held references into callee locals.
Success still avoids argument copying and concrete frame initialization.

The Rust proof retains each argument borrow's slot identity after type
refinement. A live borrow or argument copy prevents overwriting its anchor.
Stored parameter values become shared, and later loads use the replacement's
type proof. Bound receiver stores keep ordinary frames. The entry guard below
can establish numeric proofs for up to two previously unproved old arguments.
The private leaf opcode map grows from 160 to 162 bytes. Other layouts remain
unchanged. A separate parameter shadow array was rejected because it would add
load, cleanup and reconstruction state without a consumer that needs it.

The replaced reference requires immediate numeric cleanup, even when the
original optimizer emitted a borrowed no-op pop. A planner regression fails
before this correction and passes afterward. Native checks cover repeated
stores, aliases, compact overflow, exact-int-to-float changes, bound calls,
updated error locals and PCs, callbacks, reference counts and later unary OOM.
Trace, profile and monitoring observations agree with interpreter control.
A C consumer keeps the generator caller's executor on its own code object.
The fixture requires active-JIT samples and virtual trace checks. Independent
source, generated-code and native review approves the final candidate.
Performance acceptance remains deferred under OD16.

**Numeric parameter entry guards (2026-11-12).** A virtual body may replace
one or two parameters whose old types lack static proofs. Begin checks each held
argument for exact float or exact integer type before consuming CALL inputs.
Other values retain the original call, including bools and numeric subclasses.
No body work has run when that entry guard refuses. Existing side-exit and
error materializers remain unchanged.

The second begin cache is 32 bits. Its low bit selects bound-call shape.
With bit 31 clear, the remaining bits retain the single argument index plus one,
or zero for none. With bit 31 set, bits 1..16 and 16..31 hold two distinct
15-bit indices. A third guard or a pair containing an index above 32767 keeps
the original call. Single guards retain their full existing index range.
Bounds and receiver checks precede argument access; every guard runs before
input consumption or body work. The layout adds no cache word or allocation.
A bitmap would restrict the single-index range, while a separate list would
add allocation and reconstruction state. Guarding at entry uses the existing
CALL resume boundary. Store-time refusal would need a separate restartability
proof for combined store bytecodes.

Rust marks the replaced old reference as numeric-only cleanup and still rejects
live argument anchors. The store planner fuses its generic pop into
`_STORE_VIRTUAL_ARGUMENT_NUMERIC`, whose old reference is exact numeric under
the entry proof. Other statically typed stores retain their specialized forms.
Explicit bound-method setup selects the bound proof. A regression caught a
receiver-write body being planned as standalone before that correction. The
existing no-fusion assertion remains intact. Leaf and store opcode maps are
164 and 22 bytes. Instruction and rewrite layouts remain unchanged.

The new native fixture checks float and large-int acceptance, bool and None
refusal, bound indices, repeated last-reference refusal, and generic, int-subclass
and float-subclass finalizers. Finalizers run at STORE_FAST in the ordinary
callee and may mutate its locals. Later errors and one-shot OOM expose the
original PC and parameter values before or after replacement. A generator
caller and profiling also match interpreter control. Independent final-source,
generated-code and native review approves, including the subclass addendum.
The two-guard fixture checks both argument positions in ordinary and bound
calls, large integers, repeated stores, generic and numeric-subclass finalizers,
and errors before, between and after replacements. All captured bodies must
contain both guarded stores; all 32 call samples and three one-shot OOM samples
must be JIT-active. OOM checks retain the original unary PCs and assigned locals.
The source and generated/native reviews approve these paths. A clean combined
JIT/Serc stage matches the incumbent's flags; the initial JIT-only artifact is
historical evidence. Performance acceptance remains deferred under OD16.

**Borrowed argument attributes (2026-11-12).** Numeric virtual bodies may
read guarded slot and inline instance values from borrowed ordinary arguments.
The Rust proof retains argument identity through managed-storage checks.
Copied generic owners, effectful guards and loads without storage proof retain
ordinary frames. A live borrowed or managed owner prevents replacing its
argument anchor. Receiver writes and nested calls remain unsupported.

Direct method lookup can leave no explicit bound-method setup instruction.
Borrowed owners may then satisfy both standalone and bound proofs, even when
unused trailing parameters make their argument counts differ. Rust permits
runtime shape inference only when both proofs produce identical rewrites,
inserted guards, numeric entry indices and return positions. Different proofs
retain their fixed shape. Explicit bound-method setup still selects the bound
proof directly.

Bit 31 of the first begin cache certifies equivalent call shapes. Its lower
31 bits hold the shared minimum local extent. Entry uses the actual receiver
shape only with that certificate. Actual argument-count and local-extent checks
still precede CALL input consumption. The second cache retains both numeric
entry guards independently. No opcode-map, FFI-struct or cache-width change is
needed. The existing materializers reconstruct arguments, assigned locals,
stack values and original callee PCs.

The native fixture checks both call shapes and storage layouts, shared owners,
unused trailing parameters and both guarded numeric replacements. Wrong owner
layouts and missing attributes retain the original attribute error position
and payload. Descriptor changes, replaced dictionaries, numeric callbacks,
aliases, reference counts, finalizers, generators, one-shot OOM and all three
observer modes agree with interpreter control. Bound receiver guards may
already run at method lookup. Borrowed argument guards remain in the virtual
body. Independent source, generated-code and native review approves the final
runtime change. Performance acceptance remains deferred under OD16.

**Nested identity methods (2026-11-12).** A numeric virtual body may call a
closed zero-argument identity method on a borrowed argument root and immediately
discard its owned result. This covers `Vector.dot` calling
`other.mustBeVector()` before arithmetic. General nested calls, unanchored
receivers and other result uses retain ordinary frames.

The Rust leaf planner works inside-out over projected descriptors. It folds
the inner leaf before proving the outer numeric body. One projection helper
retains each rewrite's original source, cache payload and remaining effects.
Inserted guard groups stay in original source order. Unsupported outer bodies
still retain their accepted inner leaf rewrite. Planning allocation failure
leaves the original trace unchanged.

For argument-kind leaf results, bit 63 requests guarded execution inside a
virtual frame. Constant addresses retain every bit. The runtime checks the
bound zero-argument identity shape, reserved virtual frame, surviving callable
reference and additional logical recursion budget before consuming inputs.
Existing callee-shape, executor and instrumentation guards remain. The held
outer argument anchors the receiver and returned copy. The copy keeps its
real reference close. Callable cleanup leaves an owner of its code.

Every refusal uses the original recursion-check target to materialize the
outer frame at its nested CALL. Held inputs, assigned locals and live stack
remain intact. The materializer accounts for the outer frame's recursion
level before tier 1 resumes. The private leaf opcode map appends `SWAP_2`,
growing from 164 to 166 bytes with matching C and Rust layout assertions.
Instruction caches and exported descriptor widths remain unchanged.

Native checks cover both outer call shapes and owner layouts, exact error PCs
and locals, wrong owners, method/code/type/descriptor changes and callbacks.
Aliases, reference counts, finalizers, generators, one-shot OOM and observers
match interpreter control. The recursion test lowers the limit inside an
active JIT loop using C builtins, then raises at the original nested CALL.
Independent source, generated-code and final native review approves. The same
fixture passes on the JIT-enabled no-Serc control. Performance acceptance
remains deferred under OD16.

**Readonly Boolean attribute predicates (2026-11-13).** The Rust virtual-body
proof accepts short-circuit predicates over owned slot or managed attributes
of held receiver and argument roots. COPY retains the attribute producer's
identity. A successful exact-Boolean check narrows every matching stack alias;
unproven owned attributes cannot use borrowed cleanup. Original COPY, Boolean,
NOT and popped bit-guard instructions retain their operands and targets.
Popped bit-guard exits materialize the original callee at its return
instruction without replaying prior truth tests. The final operand may be an arbitrary owned
attribute and is returned without coercion. The existing virtual return makes
it heap-safe before releasing held references in the caller.

Numeric and predicate proofs remain separate. Predicate stores, mixed numeric
operations, constants, generic Boolean arguments and nested calls refuse
fusion. No runtime kernel is added. The private leaf opcode map grows from
166 to 180 bytes; C and Rust assert every appended offset. Descriptor and
step widths remain 16 bytes.

The native regression requires all 64 truth-table, layout and call-shape
traces to use virtual calls. Active faults cover branch flips and original
return PCs, exact callbacks and rebinding without replay, missing or replaced
storage, descriptors, receiver/result aliases and sole-owner finalizers.
Trace, profile and monitoring observers run before and after warming. Both
JIT/Serc and JIT-only variants pass, as do the predecessor and interpreter
semantic controls. All 89 Rust tests, targeted FFI AddressSanitizer, 31 native
integrations, unchanged focused-suite exclusions, 2,000 differential programs
and 31 workload digests pass. Five interleaved trials meet memory and code
bars. Independent source/generated/native review approves. Performance
acceptance remains deferred under OD16.

**Guarded Boolean state updates (2026-11-13).** A separate Rust proof accepts
one held receiver, exact Boolean constant writes to managed inline fields,
and return of that receiver. Each store retains its lock, type/layout and
no-dictionary guards. A new store stencil checks both the old and incoming
values before consuming inputs. Exact Boolean old values are immortal and
cannot finalize. Other old values unlock and materialize the callee at the
original store PC; preceding writes are retained without replay. Missing
values also fall back so ordinary storage maintains insertion order. Slot
setters, other constants, local writes, mixed bodies and nested calls retain
physical frames.

The private leaf opcode map grows from 180 to 188 bytes. C initializes a
Boolean-constant fact in descriptor byte 5; Rust validates it without reading
Python objects. Descriptor and step sizes remain 16 bytes. The emitter changes
only the selected store opcode, preserving its original target, argument and
cache offsets. Generated metadata gives the new stencil only an exit flag.
The existing virtual return makes the receiver heap-safe before held cleanup.

All 91 Rust tests and targeted FFI AddressSanitizer pass. The sanitizer covers
the Rust interface, not the C stencil. Native checks execute the stencil on
both clean variants, including 64 managed traces and 64 physical slot
controls. Finalizers at each of three stores verify exact PCs, partial
progress, mutation, local rebinding and unraisable errors. Missing fields,
dictionary replacement, descriptors, receiver ownership and observers pass.
Eight bound/unbound calls to the frozen Richards methods each use three
guarded stores. The predecessor JIT, interpreter and optimizer-disabled
controls pass. All 32 native integrations, unchanged focused-suite exclusions,
2,000 differential programs and 31 workload digests pass. Five interleaved
resource trials meet the unchanged bars. Independent source/generated/native
review approves both variants. Performance acceptance remains deferred under OD16.

**Loop globals prologue (2026-11-11).** The Rust planner in
`Modules/_jit_opt_rs/src/loop_globals.rs` moves one root-frame globals guard
before `_MAKE_WARM`. It requires a closed loop, an existing globals watcher,
one frame and a validity check after any preceding callback/effect. The
dictionary remains an executor dependency; mutation invalidates the trace
before a cached value can be reused. Resumable roots and traces that change
frames keep their original guards. The backedge targets `_MAKE_WARM` after
stack allocation and compaction, preserving its zero-depth cache boundary.

The new `_GUARD_LOOP_GLOBALS_VERSION` uses a side exit. A direct deopt to
`ENTER_EXECUTOR` would immediately reenter the same valid executor; the cold
side-exit handler instead detaches a self-target executor before resuming.
The native regression retains this progress failure: same-code functions
with different globals and dictionary-subclass globals must consume each
source item once and preserve the loop index on an exception. It also checks
active-JIT finalizer mutation, object collection, profiler frames and the
numeric builtins kernel. The upstream
`test_builtins_guard_emitted_once_per_frame` has one superseded assertion:
the old globals-guard count is now zero. Its other original assertions pass;
the canonical builtins guard remains exactly once and the kernel returns
`102*n`. This ninth named shape exclusion follows the existing owner ruling.

**Numeric loop-local invariants (2026-11-12).** The Rust planner in
`Modules/_jit_opt_rs/src/loop_types.rs` proves a closed, callback-free warm
cycle under exact root-local type guards. It replaces generic cleanup of
proven integers/floats with typed pops and removes redundant float guards.
It retains integer compactness and overflow checks: an exact integer may
already be large or grow beyond the specialized arithmetic range.

`_PyJitOpt_PlanLoopTypes` receives generated opcode identities, scalar local
kinds and stack depth, and returns transactional rewrite steps. Its private
opcode map, instruction descriptor and output step occupy 116, 6 and 8 bytes.
C owns live-object classification; cached constant addresses are classified
only by identity with objects owned by the active code's constant tuple.
All input stack kinds begin unknown. Every guarded local must retain its
kind at the backedge; unproved callbacks, frame changes, resumable roots,
closure state and unsupported operations retain the original trace.

New `_GUARD_LOCAL_INT` and `_GUARD_LOCAL_FLOAT` entry side exits precede
`_MAKE_WARM`; the backedge skips them. Pending work uses the existing
terminal resume-check path, so callbacks and thread handoffs leave the cycle
and reentry checks local kinds again. Rewriting is committed only after
allocations, descriptors and an explicit bound for cache transitions and
exit/error stubs succeed; unboxed expansion retains a bounded boxed fallback.
The native regression covers numeric induction, noncompact indices, integer
growth, NULL/bool/subclass refusal, signal/GC/thread mutation of locals,
exception context, observers, wide accepted loops and oversized-trace
fallback. Async cases inspect warmed guarded executors and exact checkpoint
state; they do not directly sample JIT activity before the callback.
Independent source and native review approves.

Virtual-call locals and broader call shapes, remaining lookup caches, other
non-escaping allocations and broader loop peeling remain T3 work.

**Inline frames (dropped 2026-11-10, CORRECT; code parked on branch
`campaign/t3-inline`, whose paths the citations below refer to).** A trace call site is
elidable when `_INIT_CALL_PY_EXACT_ARGS*`, `_SAVE_RETURN_OFFSET` and
`_PUSH_FRAME` are followed, before any terminator or nested `_PUSH_FRAME`, by
a matching `_RETURN_VALUE` whose segment has no uop with
`HAS_ESCAPES_FLAG`, `HAS_ERROR_FLAG`, `HAS_PERIODIC_FLAG` or
`HAS_EVAL_BREAK_FLAG` (`overlay/Modules/_jit_opt_rs/src/inline.rs:35`,
`:66`). `uop_optimize` runs the Rust planner on the optimized buffer before
unboxing and rewrites the five call uops to `_INLINE_*` forms
(`overlay/Python/optimizer.c:2133`): the callee frame is built at
`tstate->datastack_top` with `_PyFrame_Initialize`, but datastack_top is not
moved, `tstate->current_frame` is not changed and `py_recursion_remaining` is
not decremented (`overlay/Python/bytecodes.c:4842`, `:4865`). The frame keeps
the normal `localsplus`/`stackpointer`/`previous` layout, so the in-trace
callee uops run unchanged, and `_SAVE_RETURN_OFFSET` still records the
caller's resume offset. The stack-space check and the callee-entry resume
check are folded into the inline init: a pending eval breaker or insufficient
datastack space deopts at the call instruction before any frame exists
(`:4846-4851`). Side exits, deopts and errors inside the segment materialize:
`prepare_for_execution` inserts `_INLINE_MATERIALIZE_FRAME` before every
exit/error stub whose uop is inside the segment, after unboxed slots are
boxed (`overlay/Python/optimizer.c:1375`, `:1447-1518`). Materialization moves
datastack_top past the frame, links `tstate->current_frame = frame` and
decrements recursion; the exit stub then writes `stackpointer` and resumes
tier 1 at `_PyFrame_GetBytecode(frame) + target`, which reconstructs IP,
`f_lasti`, stacktop and `frame->previous`. A normal return is not linked: the
frame is committed only for the clear, `_PyThreadState_UpdateLastProfiledFrame`
and pop, so a finalizer sees the caller as current, exactly as after an
upstream unlink. Anything that can observe a frame or run code is excluded
from the segment, and tracing or monitoring enabled mid-loop changes the eval
breaker, so the callee first deopts and runs in tier 1. Executor invalidation
cannot occur inside a segment (no escaping or watcher-visible uop); a
`_CHECK_VALIDITY` exit materializes before deopting, and an invalid executor
is rejected at `_START_EXECUTOR`. The public `uop_executor` sequence aliases
the inline names back to the upstream names and marks them in `operand0`, so
`test_capi.test_opt` uop-shape consumers still see the upstream scaffolding
(`overlay/Python/optimizer.c:394`). Generators, coroutines, calls inside the
callee, raising callees and callees with a periodic check are not elided.
On the measured pair, the incumbent's named bucket-(a) uops fall from 24.0%
to 17.2% of samples on richards and 11.6% to 9.6% on raytrace
(`rust-cpython/results/perf-profile/20261110T2337*-perf-jit-{genops,inline}-{richards,raytrace}-sample`),
but counting the new `_INLINE_*` call and frame uops as the same bucket gives
23.9% → 24.6% and 11.5% → 11.7% over two profile pairs: the argument setup,
the return clear and its decrefs stay necessary in a concrete frame, and the
folded checks plus the larger stencils offset the removed link/pop. The
mechanism is correct but does not clear P1's 1% floor and is dropped; a
virtual-frame design that maps callee locals to trace stack slots is needed
to remove the remaining work. `rust-cpython/tests/jit_inline_integration.py`
checks 18 absolute observations with the JIT on and off, including
tracebacks, getframe/f_locals above and inside observers, settrace/monitoring
enabled mid-loop, side exits, generator callers, recursion, exceptions,
`gc.get_referrers`, and the marked executor segments.

**Measured shares (2026-11-10).** All 16 `--jit-set` kernels were profiled on a
clean `perf.py build --name perf-jit-t3m --jit` of `b356e9f`
(`rust-cpython/results/perf-jit-t3m.json`: `commit=b356e9f5c7`, `dirty=False`,
`verified=True`) with `perf.py profile --tool sample --jit-map`, 174-37,677,648 iterations per
kernel (9 s sample window; workloads ran 6.2-11.0 s; attribution coverage
95.9-100%). Each sample is
attributed to the innermost JIT uop on its stack, callees included, exactly as
the **Backlog re-rank** did; the denominator is every sampled frame, so the
six rows sum to the 92.7% JIT-attributed mean. Means are over all 16 kernels,
the eight numeric kernels, and the eight object kernels. The classifier is
`.work/tmp/t3/classify_t3.py`; every number comes from the `report.txt` in
these reports:

```text
rust-cpython/results/perf-profile/20261110T203455Z-perf-jit-t3m-nbody-sample
rust-cpython/results/perf-profile/20261110T203502Z-perf-jit-t3m-spectral_norm-sample
rust-cpython/results/perf-profile/20261110T203509Z-perf-jit-t3m-float-sample
rust-cpython/results/perf-profile/20261110T203519Z-perf-jit-t3m-richards-sample
rust-cpython/results/perf-profile/20261110T203531Z-perf-jit-t3m-raytrace-sample
rust-cpython/results/perf-profile/20261110T203541Z-perf-jit-t3m-fannkuch-sample
rust-cpython/results/perf-profile/20261110T203550Z-perf-jit-t3m-deltablue-sample
rust-cpython/results/perf-profile/20261110T203558Z-perf-jit-t3m-chaos-sample
rust-cpython/results/perf-profile/20261110T203604Z-perf-jit-t3m-go-sample
rust-cpython/results/perf-profile/20261110T203612Z-perf-jit-t3m-nqueens-sample
rust-cpython/results/perf-profile/20261110T203620Z-perf-jit-t3m-hexiom-sample
rust-cpython/results/perf-profile/20261110T203630Z-perf-jit-t3m-scimark_sor-sample
rust-cpython/results/perf-profile/20261110T203640Z-perf-jit-t3m-scimark_sparse_mat_mult-sample
rust-cpython/results/perf-profile/20261110T203650Z-perf-jit-t3m-scimark_fft-sample
rust-cpython/results/perf-profile/20261110T203700Z-perf-jit-t3m-scimark_lu-sample
rust-cpython/results/perf-profile/20261110T203710Z-perf-jit-t3m-scimark_monte_carlo-sample
```

| Rank | Item | Tier | Mean | Numeric | Object | Peak |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1 | (f) Everything else: generic `_BINARY_OP`, `_STORE_SUBSCR`/`_STORE_SLICE`, `_CHECK_VALIDITY`, `_HANDLE_PENDING_AND_DEOPT`, typed pops, non-short-lived allocation | T3 | 54.6% | 59.2% | 50.0% | fannkuch 75.5%, fft 73.3%, hexiom 70.9%, monte_carlo 67.6% |
| 2 | (d) Allocation and free of short-lived float, int, tuple, list, bound-method and frame objects | T3 | 12.9% | 16.7% | 9.1% | sparse 27.4%, spectral 25.1%, nqueens 24.4%, nbody 21.7% |
| 3 | (a) Python-to-Python call and frame uops: `_INIT_CALL_PY_EXACT_ARGS*`, `_PUSH_FRAME`, `_RETURN_VALUE` with the frame clear and pop, `_CHECK_STACK_SPACE*`, `_TIER2_RESUME_CHECK`, `_CREATE_INIT_FRAME`, `_CHECK_FUNCTION*` | T3 | 12.5% | 8.2% | 16.7% | richards 35.7%, raytrace 31.1%, deltablue 24.2%, sor 23.0% |
| 4 | (b) Attribute loads and stores, method loads, and their type and version guards | T3 | 6.1% | 2.6% | 9.7% | richards 23.0%, deltablue 21.6%, go 14.4%, raytrace 9.6% |
| 5 | (e) Generic decref pops of unknown type (`_POP_TOP`) | T3 | 5.7% | 6.5% | 4.8% | nbody 18.9%, sparse 11.5%, nqueens 10.1%, fannkuch 7.9% |
| 6 | (c) Global and builtin loads with their dict-version guards | T3 | 0.9% | 0.6% | 1.2% | lu 2.8%, richards 2.6%, go 2.3%, deltablue 2.3% |

**Removable bounds.** Each is an estimate with the stated direction. (a) upper
bound 8.9% mean (5.2% numeric, 12.7% object) if every Python-to-Python callee
under 40 bytecodes (the `dis` instruction count) with no generator or closure
flag is inlined, weighted by each kernel's eligible-call fraction from a
`PYTHON_JIT=0` `sys.setprofile` census (richards 86.3% eligible, spectral
100%, nqueens 0% because its hot callees are generator expressions); this
assumes equal per-call cost and every site inlinable. (b) 0.01% mean measured
over the traces that matched an executor dump (80.6-96.8% of samples per
kernel); the trace-local guard simulator proves only seven samples of repeated
attribute guards on the same object (go 0.13%), far under P1's floor, and
unmatched traces could only add repeats, so this is a lower bound on the
matched subset, not a global upper bound. (c) 0.01% mean on the same matched
sample set (nqueens 0.11%, chaos 0.02%): repeated globals/builtins version
guards, also far under the floor. (d) at most all of 12.9% if every
short-lived allocation is non-escaping and removed; the named dealloc side is
2.4% mean (a lower bound on the removable part when those frees belong to
allocations in the same trace), 2.7% is in named allocator callees, and 7.9%
is the owning uop's own stencil (`_POP_TOP_FLOAT`/`_POP_TOP_INT`,
`_ALLOCATE_OBJECT`, `_SET_ADD`, `_BUILD_*`, binary-op boxing). (e) at most all
of 5.7%; only 0.04% mean has a typed producer immediately before the generic
pop, so typing the operand removes little without loop peeling (T2 measured
0.0% achievable for loop-carried locals). (f) is residual and has no single
mechanism estimate. Allocations performed inside (a)-(c) and (e) uops stay in
those rows — frame clear and pop under `_RETURN_VALUE` in (a), bound-method
allocation under `_LOAD_ATTR_METHOD_*` in (b) — so (d) understates total
alloc/free cost, while 12.9% remains an upper bound on what allocation
removal can take from the (d)-attributed paths. Int arithmetic
(`_PyCompactLong_Add/Subtract/Multiply`) is counted in (f), not (d), unless a
named allocator or deallocator is sampled. Inputs outside the profiles: (a)
`.work/tmp/t3/census/*.json`, (b)/(c) `.work/tmp/t3/guard-repeats.txt` with
dumps in `.work/tmp/t3/dumps/`, (d)/(e) the same 16 reports above.

Bucket (f) is diffuse. Its largest items, as 16-kernel means, are generic
`_STORE_SUBSCR` 5.1% (array stores among them), generic `_BINARY_OP` 3.9%,
`_CHECK_VALIDITY` 3.3%, `_HANDLE_PENDING_AND_DEOPT` 2.3%, `_STORE_SLICE`
2.1%, `_BINARY_OP_SUBSCR_ARRAY_D` 1.8% and `_BINARY_OP_SUBSCR_LIST_SLICE` 1.5%;
nothing else reaches 1.4%. Under P1 this ranks inlining (a) first among T3
mechanisms, with allocation removal (d) next; the store and generic-op items
are T2 specializations, and `_HANDLE_PENDING_AND_DEOPT` needs its exits
explained before it is ranked.

### T4: backend, toward M4

Revisit whole-trace register allocation (the a1 attempt cut spills 8% for no
measurable gain), then reduce stencil glue.

**Whole-trace cache allocation (2026-11-11).**
`Modules/_jit_opt_rs/src/cache.rs` chooses among generated input-cache
variants with a four-state dynamic program. It minimizes explicit
spill/reload stencils, then transferred stack references, while preserving
the original final cache depth. An alternative may spill earlier, but never
reloads more than the generated greedy choice requires at that depth; it
cannot prefetch below the live operand stack. `_START_EXECUTOR` and
`_JUMP_TO_TOP` keep their generated zero-depth boundaries. Omitted `_NOP`s
do not carry transitions.

The private `_PyJitOpt_PlanCache` ABI takes generated output and preferred
depths and returns one chosen input depth per original instruction. C owns
opcode selection and emission. Side exits use the selected variant's exit
depth, and unboxed masks stay attached to the original instruction.
Allocation failure, equal-cost plans and disabled optimization retain the
existing allocator. Exhaustive short-trace tests compare the result with
every permitted plan, including final depth and the no-prefetch constraint.
Native `jit_cache_integration.py` covers arithmetic, calls, generators and
exception exits, with an optional comparison of emitted spill/reload counts
against a matched reference. These counts establish the rewrite mechanism;
runtime and resource acceptance remain deferred under OD16. Stencil glue
outside these explicit transitions remains separate T4 work.

**Virtual resume stub reuse (2026-11-12).** The Rust planner in
`Modules/_jit_opt_rs/src/exit_stubs.rs` selects a prior materializer stub
within the same virtual call. The bytecode target and exit-cache variant
must match. A different recipe replaces the remembered recipe. Entry
refusals, separate virtual regions and error materializers stay separate.
NOP compaction and cache allocation precede planning, so source indices and
spill depths describe the final instruction stream.

The private `_PyJitOpt_PlanExitStubs` ABI consumes eight-byte descriptors
and returns one canonical u32 source index per instruction. C emits the
selected jump or the original spill and materializer. The Rust region enum
owns its remembered recipe. Invalid descriptors, insufficient capacity or
allocation failure preserve the output and retain the original emission.
C releases its descriptor and output arrays on every return path.

All 84 Rust tests pass. AddressSanitizer passes the new FFI test with
uninitialized output and unchanged sentinels on refusal. Offline Miri setup
lacks `addr2line`, so sanitizer evidence satisfies the engineering rule.
The native regression observes 424 virtual resume guards and requires shared
candidate targets, distinct entry refusals and separate same-callee regions.
It retains exact errors, PCs, locals and managed-dictionary behavior.
All 28 native integrations, the expanded suites, 2,000 differential fuzz
programs and 31 workload digests pass with the same 16 reviewed exclusions.
Independent source, generated-code and native review approves both Serc
variants. Five interleaved trials pass every resource bar: matched RSS at
most 1.0021x incumbent, isolated Serc footprint at most 1.0260x no-Serc and
code at most 1.6075x M0. No runtime speedup is claimed. Performance acceptance
remains deferred under OD16.

**Unboxing around virtual calls (2026-11-12).** A virtual call no longer
forces its entire trace to stay boxed. C describes every instruction from
virtual begin through return as an opaque, variable-shape barrier to the
existing Rust unboxing planner. This includes both boundaries. Independent
numeric chains before and after use the original descriptors; the virtual
region keeps zero unboxed exit/error masks and is copied unchanged.

The existing dataflow proof blocks raw values crossing an opaque boundary,
and verification rejects any live unboxed slot there. A live caller prefix
is boxed before entering the virtual frame. The original boxed recipe and
its capacity bound remain the rollback path. No private ABI or generated
opcode contract changes. The Rust regression covers independent float and
integer chains around the boundary; the native regression requires outer
unboxed arithmetic, prefix boxing, an entirely boxed virtual body and eleven
active samples across success, errors with assigned locals and complex power
resumption. All prior integrations pass. Independent source/native review
approves; performance acceptance remains deferred under OD16.

**Unboxing inside virtual bodies (2026-11-13).** Numeric virtual-body
instructions now use the existing Rust unboxing descriptors. Begin and
return stay opaque barriers, so caller prefixes and returned values stay
boxed at both boundaries. Rust chooses profitable chains and computes their
exit and error masks. Simple integer chains whose in-place allocation cost
ties the unboxed cost remain boxed.

C boxes raw slots before resume materialization. If boxing fails after some
slots convert, the existing boxing kernel clears the remaining raw slots
and enters error materialization. Ordinary error paths clear their remaining
raw masks before materializing. Synthetic float and integer boxes consume
their raw input before that error continuation. The helper's frame-kind enum
distinguishes physical exits from virtual materialization. Nonzero masks are
excluded from resume-stub reuse because its current recipe has no masks.
The original trace remains the capacity rollback. No private ABI changes.

The generator classifies `_PyFrame_GetStackPointer` as non-escaping in its
existing function list. The getter only returns the stored pointer. The
first native attempt exposed the missing classification: virtual argument
loads otherwise split every arithmetic chain. Generated metadata now agrees
with the getter, and all 99 upstream case-generator tests pass without skips.

All 86 Rust tests and 29 native integrations pass. The new regression requires
profitable inner float and integer arithmetic, overflow resumption and a raw
prefix at a type-guard exit. Callbacks observe the real callee and run once.
Full GC clears float freelists before focused malloc-allocator injection.
The test requires the actual boxed-prefix and raw-division sequence and a
two-slot mask of `0b11`. Injection counts zero, one and two cover the boxed
prefix, first exit box and failure after one slot converts. Exact error PCs,
locals, alias ownership, input progress and post-arming JIT activity pass.
The existing default-allocator and observer programs remain unchanged.

The expanded suites retain the same 16 reviewed exclusions. All 2,000 fuzz
programs and 31 workload digests match. Independent source, generated-code
and native review approves both Serc variants and the strengthened fault
proof. Five interleaved trials pass every resource bar: matched RSS at most
1.0051x incumbent, isolated Serc footprint at most 1.0444x no-Serc and code at
most 1.9823x M0. Raytrace maps 695,432 code bytes in every trial. No runtime
speedup is claimed. Performance acceptance remains deferred under OD16.

**Masked resume stub reuse (2026-11-13).** The Rust resume recipe now
includes float and integer masks with the bytecode target and exit-cache
variant. C describes every resume exit after cache allocation and compaction.
Within one virtual region, a matching recipe reuses the earlier jump target
before C chooses fresh plain or boxed emission. The shared boxed recipe
includes its allocation-error materializer. Ordinary instruction-error
continuations and entry refusals remain separate.

The private C and Rust descriptor grows from 8 to 16 bytes. Float and integer
masks occupy offsets 8 and 12. Both sides assert every field offset and size.
Resume masks must be disjoint. Unknown kinds or overlapping masks reject the
plan without touching output. Capacity or allocation refusal retains the
original emitter. A region still owns only its previous recipe, with no
cross-call state or hash table. Different targets, variants, masks and regions
cannot share a stub. The C and Rust halves must come from the same overlay.

All 87 Rust tests and the targeted FFI AddressSanitizer test pass. The native
regression observes two masked regions in one executor. Eight guards use five
targets in the first region, and six guards use five in the second. Their
targets stay disjoint despite common visible PC/mask pairs. The predecessor
uses eight and six distinct targets. Slots and managed layouts, dictionary
replacement and clearing on either side, wrong value types, wrong owners and
returning or raising properties preserve exact errors, PCs, locals, payloads,
progress and callback counts. Existing integer overflow, allocation-error and
partial-boxing controls remain required. Python executor tuples omit integer
masks and cache variants, so Rust tests establish those exact distinctions.

All 30 native integrations, the expanded suites, 2,000 fuzz programs and
31 workload digests pass with the same 16 reviewed exclusions. Independent
source, sanitizer, generated-code and native review approves both variants.
Five interleaved trials pass every resource bar: matched RSS at most 1.0014x
incumbent, isolated Serc footprint at most 1.0289x no-Serc and code at most
1.8602x M0. Raytrace maps 652,592 bytes in every trial. No runtime speedup is
claimed. Performance acceptance remains deferred under OD16.

**Local-store stencil fusion (2026-11-12).** The Rust planner in
`Modules/_jit_opt_rs/src/store.rs` replaces a local swap followed only by
NOPs and a generic pop with `_STORE_FAST_FUSED`. Instruction-pointer changes,
guards and callbacks remain boundaries. The new
private `_PyJitOpt_PlanStores` ABI returns transactional steps using generated
opcode identities and preserves the original pop's target metadata.

Generated local/escape flags make the generic fused instruction an unboxing barrier.
It replaces the local before publishing the consumed operand stack and
closing the old value, including NULL on first assignment. The following
validity check remains. A native finalizer regression checks the new local,
original store IP and outer exception; it mutates that local, rebinds a
watched global and collects garbage before the caller resumes. Alias
reference counts, numeric boxing, observers and disabled optimization are
also checked. This removes a stencil boundary; no speedup is claimed.

Two more upstream count assertions are named under the existing owner ruling:
`test_binary_subscr_list_slice` expects two generic pops (one pop and one fused
store now), and `test_call_method_descriptor_noargs` expects at least five
(three stores and two pops now). Independent review ran both original test
methods with only the count helper including fused stores. Every numeric,
list-slice and descriptor assertion, and the four unchanged no-op pops in the
slice test, passed. CPython's tests remain unchanged.

**Typed local-store fusion (2026-11-12).** The same Rust planner also fuses
exact-float and exact-int cleanup into `_STORE_FAST_FUSED_FLOAT` and
`_STORE_FAST_FUSED_INT`. The old local's cleanup type selects the stencil,
including an integer local replaced by a float while an alias retains the
integer. Only NOPs may separate the original swap and cleanup. The fused
instruction copies the cleanup's metadata and keeps the original local index.

Each stencil transfers the incoming reference before closing the old local
with its matching specialized deallocator. Generated metadata records local
access, no escape and one consumed input. The Rust unboxing proof keeps that
input boxed. Unboxed stack prefixes retain the original pair's behavior.
The private store opcode map appends two identities, growing from 22 to
26 bytes. C and Rust assert the size and new field offsets. Descriptors and
rewrite steps remain 4 and 8 bytes. No public contract or dependency changes.

The store fixture retains the original generic finalizer, invalidation,
simple float-loop guard and large-integer checks. Separate float and integer
alias loops check both typed stencils, reference counts, tracing and profiling.
Trace capture runs before module-global assignments can invalidate the
executor. The mixed-local fixture requires fused integer cleanup alongside
float multiplication and preserves its integer-alias result. Independent
source, generated-code and native review approves, including that assertion
update. The rewrite removes a stencil boundary. No speedup is claimed, and
performance acceptance remains deferred under OD16.

**Guarded old-float cleanup (2026-11-13).** The Rust store planner can use
an exact-float observation of a root-frame local to select a runtime guard.
The observation is a hint, not a type proof. C checks the active frame and
code before reading boxed locals under the GIL. Rust stops using hints at
the first physical or virtual frame boundary. Existing ordinary fusions
continue after that boundary.

For a local swap followed only by NOPs and generic cleanup, Rust emits the
existing `_GUARD_LOCAL_FLOAT` before `_STORE_FAST_FUSED_FLOAT`. Both copy
the original cleanup's STORE target and retain the local index. The guard
checks the actual old value before replacing the local or consuming the
incoming reference. NULL, tagged integers and non-floats resume the original
store. The typed store installs the new value before closing the old float.
The following validity check remains. No runtime stencil is added.

The private store map grows from 26 to 36 bytes. Input descriptors grow from
4 to 6 bytes with a closed observed-kind field. Output steps remain 8 bytes.
Sources never precede their output position, so C emits in place while
reading original source metadata. Malformed hints and short output leave
the output untouched. C and Rust must come from the same overlay.

All 98 Rust tests and the targeted FFI AddressSanitizer test pass. The native
fixture requires two adjacent guard/store pairs for its overwritten local.
Finalizing objects and float subclasses preserve the exact original store
PC, see the new value once and can mutate that local. Aliases, source counts
and observers match interpreter and disabled-optimizer controls. A separate
fresh-frame case enters a warmed executor with an unset local and preserves
the sole input through guard refusal. Its common loop tail provides one
backedge, ensuring the guard actually runs before the first assignment.

The unchanged nbody consumer emits fourteen guarded float-store pairs in
its inner trace and four in its second trace. The refresh on the accepted
array predecessor captures 60,701 samples with at least 95.39% named JIT
attribution per kernel. Generic fused-store self work averages 2.17%.
These are profile shares and emitted counts, not speedups.

All 35 native integrations pass. The 18 focused suites retain the same
16 reviewed exclusions. All 2,000 fuzz programs match, including 1,949
JIT-active cases. Independent source, generated-code and leased final native
reviews approve both variants. Five interleaved resource trials pass:
matched RSS at most 1.0045x incumbent, isolated Serc footprint at most
1.0352x no-Serc and code at most 1.8792x M0. Nbody maps 44,396 bytes and
raytrace stays at 659,248 bytes in every trial. Timing acceptance stays
deferred under OD16.

**Virtual parameter store fusion (2026-11-12).** The Rust store planner
replaces `_SWAP_VIRTUAL_ARGUMENT` and its typed numeric cleanup with one
`_STORE_VIRTUAL_ARGUMENT_FLOAT` or `_STORE_VIRTUAL_ARGUMENT_INT` stencil.
Selection follows the old value's cleanup type, including integer-to-float
parameter changes. The fused store retains the argument index and copies the
original cleanup's metadata. Only NOPs may intervene. Guards, instruction-pointer
updates, callbacks and typed ordinary local cleanup remain boundaries. Generic
virtual cleanup fuses only under the numeric entry proof described above.

Both stencils transfer the incoming reference into the bound-aware held slot
before closing the old number with its original specialized deallocator.
Generated code consumes one operand and leaves zero cached values, matching
the original pair. The existing ownership proof and frame materializer stay
unchanged. The original pair remains executable when planning allocation fails
or a pair is ineligible. The private store opcode map grows from 10 to 20
bytes. Store descriptors remain 4 bytes and rewrite steps remain 8 bytes.
The leaf opcode map stays 162 bytes.

The native parameter fixture requires exact fused-store kinds and counts,
with no remaining virtual swaps. All 20 activity
checks, aliases, bound calls, callbacks, updated error PCs and locals, one-shot
and sustained OOM, reference counts and observer checks still pass. Independent
source, generated-code and native review approves. This removes a stencil
boundary. No speedup is claimed, and performance gates remain deferred under OD16.

### J acceptance

Under OD14 a candidate lands on steps 3-6 alone, plus the memory and
code-size bars from **Targets** measured on any host. Steps 1, 2, and 7 and
the **Milestone gate** run at **Close** for the stacked result.
`perf.py climb-step --candidate C --correctness` runs steps 3-5 without
waiting for a quiet host and prints verdict CORRECT; its one-run training
point estimate goes in the ledger as advisory and never blocks. Mechanisms rank by
sampled profile share from `perf.py profile`, not by P1 probes.

After OD14 lifts, a candidate replaces the incumbent only when all hold:

1. **Gate:** `perf.py bench --baseline @incumbent-jit --candidate C --jit-set
   --gate --runs 5` returns ACCEPT under the unchanged `perf_verdict.py`
   policy (1% floor, 95% bootstrap), between `perf-gate-ready` checks.
2. **No kernel regression:** no training kernel's pooled kernel-CPU CI95
   lower bound exceeds 1.02 against the incumbent.
3. **Digests:** every training, held-out, and guard digest matches.
4. **Differential fuzz:** 2,000 programs from `rust-cpython/tests/jit_fuzz.py`
   give identical output, exceptions, and reference-tracer counts with the JIT
   on and off.
5. **Targeted suites:** the same failed-test set as `perf-rust` for
   `test_capi.test_opt`, any `test_jit*` files present at the pin, `test_float`, `test_long`,
   `test_sys_settrace`, `test_monitoring`, `test_generators`, `test_frame`,
   `test_gc`, `test_weakref`, plus any suites the item adds.
6. **Independent review:** a reviewer agent that did not write the change and
   has no access to the author's reasoning reads the diff. Its approval is
   required for any change to reference ownership, deopt, frame
   materialization, or executor invalidation; a demonstrated defect blocks.
7. **Clean rebuild:** a clean build of C reproduces the gate direction in a
   two-run check.

`perf.py climb-step --candidate C` runs steps 1-5 and prints one JSON verdict;
`--screen` first runs one training pass and gates only at a point estimate
≤ 0.99.

**Stacking.** An INCONCLUSIVE candidate with point estimate ≤ 0.995, no
regressed kernel, and steps 3-6 passed is tagged `stack/<mechanism>`. Two or
more stackable candidates that compose cleanly are gated as one candidate.

### J loop

1. If the tier has no ceilings, run P1. T2 waits for R1's catalog.
2. Take the top unparked mechanism. Write it in `.work/wt/<mechanism>`, at
   most two design variants per mechanism.
3. `perf.py build --name perf-jit-<mechanism> --jit --incremental`, then
   `climb-step --screen`. No win: discard and record why.
4. Run **J acceptance**.
5. Accepted: integrate, rebuild `perf-rust-jit` and `perf-rust` clean, update
   `S`. Rejected: ledger row, discard. Stackable: tag and ledger row.
6. When the backlog is empty, run the **Milestone gate** and record it, met or
   missed. Then consider a **Source epochs** refresh and move to the next
   tier.

**J stop rules.**

- After eight consecutive gated candidates fail, re-profile and re-rank once.
  If the next four also fail, the tier closes with its milestone recorded as
  missed, and the lane moves to the next tier. Closing T4 this way ends
  track J incomplete.
- A correctness failure that reproduces on the incumbent becomes the lane's
  top item: root-cause it, fix it under **J acceptance** steps 3-7, and add a
  fuzzer seed or test that catches it. Three failed attempts park the lane.

## R1: CinderX reference study

1. Build stock CPython 3.15.1 from the production source lock's archive into
   `.work/r1/`, and source-build CinderX at its latest `main` commit, recorded
   in Status, against it. Run CinderX's tests. Record source, ABI, dependency,
   and license closure.
2. Run the training and held-out kernels from
   `benchmarks/workloads/jit_kernels.py` with CinderX's JIT off and on, five
   interleaved runs each, while holding the host lease. Record kernel CPU,
   digests, code bytes, warmup, compile CPU, and peak RSS.
3. Read `Jit/hir/`, `Jit/lir/`, `Jit/codegen/`, `Jit/inline_cache.cpp`, guard
   invalidation, type feedback, inlining, and deopt. For each mechanism
   record its source, version assumptions, the semantics it must preserve,
   its contribution on our kernels (by disabling it through CinderX's own
   switches where they exist), and the tier it belongs to.
4. Do not adopt CinderX's runtime, Static Python, lightweight frames,
   parallel GC, or build system. Copying code requires a license and
   attribution review in the **Owner queue**; prefer an independent Rust
   implementation.

**Exit:** a **CinderX catalog** section added below **Status**, ranked by
measured contribution, and a ledger row with CinderX's within-version `S`.
The catalog's report sets that uplift beside M2-M4 in the **Owner queue**.
Targets stand unless the owner moves them; T2 does not wait for an answer.

**Setup (done 2026-11-09).** Rebuild it into `.work/r1/` when absent:

- CPython 3.15.1 from the root `sources.lock.json` archive (sha256
  `673ae723…f99b`): `./configure --prefix=$PWD/.work/r1/install
  --with-ensurepip=install`, `make`, then the install step.
- CinderX `e48f370dfcc045db32ac19142419043342b10cfb`: `python3 setup.py
  bdist_wheel` with that interpreter, then install the wheel into it. Closure:
  CinderX MIT, asmjit Zlib, and fmt, parallel-hashmap, usdt, capstone, and
  googletest fetched by CMake; lightweight frames, parallel GC, and Static
  Python are off on 3.15.
- Tests: `pytest cinderx/PythonLib/test_cinderx/` (3035 passed, 566 skipped)
  and `RuntimeTests` with the CI macOS arm64 filter (1205 passed, 7 skipped).
- JIT off: `-X cinderx-jit-disable`. JIT on: `-X cinderx-jit-all -X
  cinderx-jit-background-compile=0`, which compiles every function on its
  first call. The timed runs also measure CinderX's default `cinderx.jit.auto()`
  policy, because compile-everything inflates compile CPU and code bytes.
- `rust-cpython/tests/cinderx_kernels.py --python <interp>` runs all 24
  kernels in both modes; every digest matches across modes and the harness.

If CinderX does not build or pass its tests on 3.15.1 within three
root-caused attempts, R1 ends with a source-only catalog (step 3 without
measured contributions), and T2-T4 rank by the lane's own profiles.

## Track C: Rust source compiler (Serc on 3.16)

Serc compiles Python source to CPython bytecode; it does not make execution
faster. It targets cold imports, `compile()`, and `exec()` of uncached source
(the product ships no `.pyc`), and provides a byte-exact marshal writer. Only
the port to the pinned 3.16 epoch is in scope.

### C0: vet and pin

1. Pin Serc `bc563167` (release 0.1.11) as a second source record in
   `rust-cpython/sources.lock.json` with archive SHA-256 and size. Lane changes
   live in `rust-cpython/serc-overlay/`, mirroring the archive's paths.
2. Pin the `serc` crate's non-dev dependencies exactly in the overlay
   `Cargo.lock` with licenses, including the Ruff parser crates at the git
   revision Serc pins, and `stacker`, whose `psm` dependency compiles C and
   assembly through `cc` with the locked clang, never GCC. Extend lane
   `fetch` to cache the git dependency so builds stay offline. Exclude the
   CLI, benchmark crates, `mimalloc`, and `rayon` from the lane workspace.
3. Record the dependency closure and the `libpython` text-size delta of
   linking the bare compiler into a scratch `--serc` prototype.

**Exit:** the source record and lock entries land; Serc's own tests pass on
the pinned toolchain.

**Vetting result (2026-11-10).** Archive sha256
`4db43e58bd6f3b7e46728f686c7482d4404b61fe5143371642e1940041d3e84b`, 3,445,293
bytes. `cargo test --locked` in the serc crate passes 8,624 tests on `nightly-2026-09-15`
with the locked clang 23.1.4, against CPython 3.12-3.15 oracles. The non-dev
closure is 97 crates, all permissive (MIT, Apache-2.0, BSD, Unicode, Zlib,
Unlicense), plus `ruff_python_parser` at git `9d0f58b1`. Three constraints
follow:

- The repo's `.cargo/config.toml` replaces crates-io with the `stuff` mirror,
  which lacks 47 of the crates (every `ruff_*` 0.0.15, `psm`, `stacker`,
  `cc`, `dashu-int`). Serc's dependencies come from crates.io (OD13): lane
  `fetch` resolves crates-io from the official index for its own cargo
  invocations, with `--locked` checksums as the integrity check. The repo-wide
  `.cargo/config.toml` is unchanged.
- cc-rs prefers `HOST_CC` over `CC`, and the host cargo config sets
  `HOST_CC="kache clang"`, so `psm` silently compiles with Apple clang. Every
  lane cargo invocation now exports `HOST_CC`, `HOST_CXX`, and `HOST_AR` as the
  locked LLVM tools.
- A binary that calls the compiler gains about 9.2 MB of text (staticlib
  probe, ThinLTO, prebuilt std), against about 3.3 MB of `libpython` text. C2's
  memory gate is the likely failure; C2 measures it first.

**Landed (2026-11-10).** The Serc archive record and `serc_source` metadata
are in `sources.lock.json`; `overlay/Cargo.lock` grows from 311 to 364
packages with `serc-lane`, `serc`, `psm` 0.1.32, `stacker` 0.1.25, and the
`ruff_*` 0.0.15 crates. `serc` is a path dependency of the one-dependency
`serc-lane` anchor and the Serc tree is excluded from the lane workspace, so
the lock holds only the library's 97 normal and 19 build-only crates; the CLI,
benchmark crates, `mimalloc`, and `rayon` stay in Serc's own test workspace.
`build.py fetch` verifies the archive, applies `serc-overlay/`, and caches the
lane lock and the Serc test workspace from `https://index.crates.io` in the
private Cargo home. Offline `cargo test --locked --no-fail-fast` in the serc crate runs
8,624 tests on `nightly-2026-09-15` with the locked LLVM: with
`SERC_TEST_PYTHON` naming CPython 3.14.5, 6,777 pass and the other 1,847
fail only their oracle precondition: the 3.13 and 3.15 oracles must be
exactly CPython 3.13.15 and 3.15.0rc2, named by `SERC_TEST_PYTHON_313` and
`SERC_TEST_PYTHON_315`. With every oracle variable set, the suite is green
(see C1). `psm`'s `aarch_aapcs64.o` embeds `clang
version 23.1.4`. A clean `perf-rust` build on this lock fails exactly the
e891 parity set.

### Marshal parity

`_marshal_rs` writes valid bytes that differ from CPython's reference
selection, so `compileall_source` is excluded from control comparisons
(`KNOWN_CONTROL_MISMATCHES` in `rust-cpython/perf.py`). Make `_marshal_rs`
byte-identical to upstream `marshal.dumps`, using Serc's writer as the guide,
and remove the exclusion.

**Exit:** byte-identical `marshal.dumps` against `perf-upstream` over every
code object from the C1 corpus, `test_marshal` and `test_importlib` failing
the same tests as `perf-upstream`, and the marshal route verdicts unchanged.
Lands under the default-build rule in **Integration**.

**Landed (2026-11-10).** `_marshal_rs` chooses the ASCII string tag from the
string's ascii flag as CPython does (`660068e`), and the exclusion is gone.
`python3 rust-cpython/tests/marshal_parity.py --corpus <C1 corpus>` reports 0
mismatches over 9,102 code items and 119 plain values at versions 0-6, with
the same 33 oracle-refused cases skipped on both sides. `test_marshal` and
`test_importlib` pass on both builds (1,337 run, 19 skipped), and the marshal
route suites pass on `perf-rust` (1,660 run, 31 skipped).

### C1: port Serc to the epoch's 3.16

Add a 3.16 target for the epoch commit: opcodes, code-object layout,
exception and line tables, marshal rules, parser features, and compiler
passes. Add `rust-cpython/tests/serc_parity.py`: for each corpus file and
optimization level it compiles with `perf-upstream` (`compile()` then
`marshal.dumps`) and with Serc's CLI built in `.work/` from the pinned archive
plus `serc-overlay/`, and reports every byte mismatch as JSON. The CLI never
enters `libpython`.

**Corpus:** the epoch's `Lib/` and `Lib/test/`, plus the lane's pinned
benchmark source trees (Django and the `compileall_source`, `pylint_source`,
and `pycparser_source` inputs), at `-O0`, `-O1`, and `-O2`. Files that
upstream itself refuses to compile are listed and skipped on both sides. In
practice the benchmark trees are the 3.16 Django closure (Django, asgiref,
sqlparse) and the checked-in `python_corpus` fixture that `compileall_source`
and `pylint_source` both read; `pycparser_source` reads only C inputs.

**Exit:** zero mismatches and Serc's own tests green. Each distinct mismatch
class gets three root-caused attempts; an unresolved class parks track C with
the class, a minimal repro, and the upstream change that introduced it.

**Landed (2026-11-10).** `perf-upstream` and Serc agree byte for byte on
every case: 3,045 files, 9,135 cases, 9,102 exact, 0 mismatches, 0 Serc
refusals. The other 33 cases are 11 files the oracle itself rejects with
`SyntaxError`, at each level. `python3 rust-cpython/tests/serc_parity.py`
rebuilds the CLI offline from the cached archive plus `serc-overlay/` and
reruns the census; it exits nonzero on any mismatch. `cargo test --locked --offline` in the serc and
serc-cli crates passes with every `SERC_TEST_PYTHON*` oracle
set, `SERC_TEST_PYTHON_316` naming `perf-upstream`. Each 3.16 codegen
change carries a test pinning Serc's marshal bytes to oracle output in
`serc-overlay/crates/serc/tests/fixtures/python316_parity/`.

### C2: in-process `exec` mode

Add `--serc`. In `--serc` builds, source-mode `exec` compilation (imports,
`compile(..., "exec")`, `exec(str)`) calls Serc through a C entry point in the
overlay compile path. `eval`, `single`, and AST-object input go to the C
compiler by mode. A Serc error raises; it is never retried on the C compiler.
`PYTHONSERCVERIFY=1` compiles with both and raises on any difference.

Add three registry workloads, `cold_python_startup`, `cold_import_django`, and
`cold_import_app_stack`, that run with `-B` and a fresh empty
`PYTHONPYCACHEPREFIX`, so every module, stdlib included, compiles from source.

**Exit:** `perf-rust-serc` fails exactly `perf-rust`'s `perf.py test --all`
set; one full-suite run under `PYTHONSERCVERIFY=1` reports no difference; and
`perf.py bench --baseline perf-rust --candidate perf-rust-serc --workload
cold_python_startup --workload cold_import_django --workload
cold_import_app_stack --workload compileall_source --gate --runs 5` is ACCEPT
under the unchanged verdict and memory policy. A REJECT gets profiling and
three optimization attempts; if all fail, track C ends incomplete with the
measured loss.

The hook, the `--serc` flags, and the new workloads go to the **Owner queue**.

**Implemented; exit not met (2026-11-10).** `perf.py build --serc` and
`build.py build --serc` define `Py_SERC` and make the static carrier's
optional `serc` feature a default, linking `overlay/serc-lane` (the C entry
points) and Serc into `libpython`. A default build is unaffected: at the C2
head every `libpython` section, rebase/bind/export table, and non-debug
symbol equals a `perf-rust` built before C2 (only the debug-map UUID differs),
and `libcpython_rust_staticlib.a` is byte-identical.
`overlay/Python/pythonrun.c` routes source-mode `exec` requests to Serc by the
rule in `rust-cpython/README.md` (In-process Serc): string or bytes input,
flags limited to the source-form bits, an exact `str` filename, bytes that
CPython decodes as UTF-8 without the codec registry, and no grammar only
CPython's parser defines (the `barry_as_FLUFL` future, or nesting deeper than
200). `eval`, `single`, AST, `FILE *`, flagged and inherited-future requests
stay on the C compiler. The C compiler is the diagnostic authority: on a Serc
refusal it runs only to raise CPython's exception, and the hook raises
`SystemError` instead of returning C's code when C accepts; when Serc's scan
finds a construct CPython may warn about, C runs for its warnings and its
code is discarded. `PYTHONSERCVERIFY=1` (and `perf.py test --serc-verify`)
compiles every Serc request with both and raises on any difference in the
re-marshalled code or on a source C rejects. Serc's bytes load without the
`marshal.loads` audit event and with CPython's object identity for 1-byte and
1-character objects. C2 also committed the C1 3.16 target tables that the
root `target/` ignore rule had kept out of C1's commits.

After Serc's parse, source forms Ruff accepts but CPython rejects are left to
CPython's parser: a parenthesized assignment-expression target, a `__debug__`
match capture, a format spec nested deeper than CPython's three replacements,
tab/space indentation its tokenizer rejects (and any module mixing tabs with
backslash continuations, whose continuation-line check Serc does not model),
and a module whose estimated parser stack reaches 5,000 of CPython's 6,000
slots. The estimate sums the module's deepest bracket nesting at 27 slots, its
deepest block nesting at 7, and its deepest expression nesting and longest
`if`/`elif` chain at 1 each, weights measured on the C path. A long `elif`
chain, deeply bracketed arms and deep `def` nesting all overflow there, and
each overflow measured raises CPython's `MemoryError` on Serc too. A `\N{...}` escape missing from
Ruff's `unicode_names2` 1.3.0 table (23,955 of Unicode 18's 172,808 names) is
likewise left to CPython's name table, so no name raises `SystemError` and an
unknown name raises CPython's `SyntaxError`.

Evidence at `dbacf66`: an in-process census over the C1 corpus
(`marshal.dumps(compile())` per file and level) equals `perf-upstream` in all
9,135 cases, with and without `PYTHONSERCVERIFY=1`; the 26 frozen modules that
`_freeze_module` compiles through Serc equal the default build's;
`tests/test_serc_compile.py` passes on `perf-rust-serc`. `cold_python_startup`,
`cold_import_django`, and `cold_import_app_stack` exist (run only when named,
`-B -X frozen_modules=off` and a fresh empty `PYTHONPYCACHEPREFIX`, each child
checking that only the import bootstrap is frozen and nothing came from a
cache); `cold_import_app_stack` is unavailable like `import_app_stack`, so the
exit's gate command must drop it until that closure is locked.

The exit was met on `campaign/c2` rebased onto `main` `bd964f6` (2026-11-11,
macOS arm64, empty bytecode cache), before the review fixes below changed
Serc: `perf.py test --name perf-rust-serc-v3 --all` and `--all --serc-verify`
each failed exactly `perf-rust`'s set, the e891 five (52,102 tests, 2,877
skipped), plus one host-load flake in
`test_concurrent_futures.test_process_pool` (2 of 5 isolated runs fail on
`perf-rust` itself). `serc_parity.py --oracle perf-rust` reported 10,005
cases, 0 mismatches. The memory bar is met under OD15. The close-out below
repeats both full-suite runs and the census after the fixes.

**Independent re-review (2026-11-11) and fixes.** The adversarial review of
`d02ebce`..`b1ffca1` found nine defects; `852831a`, `b327c5c` and `b4c2a8a`
address all of them:

- *Finally exits (two segfaults, skipped finally bodies and `__exit__`
  calls, a `range_iterator` left in `sys.exception()`).* Serc reconstructs
  codegen.c's fblock unwind order from separate depth stacks and gets nested
  exceptional finalizers wrong. On 3.16 a `return` inside a `finally` body,
  or a `break`/`continue` that leaves one, now routes to the C compiler
  (`cpython_only::compiler_rules`, `CompileError::RequiresCpythonCompiler`).
  CPython warns on all of these (PEP 765), so the hook already ran the C
  compiler for them. The Serc code paths for these shapes remain for
  3.12-3.15 and are dead on 3.16.
- *Folded Boolean discard lines (b1ffca1) and chained-comparison break
  jumps (d02ebce)* match CPython in verify mode again; see `b327c5c`.
- *Routing:* parameter defaults weigh into the parser-stack estimate, 150
  nested f-strings route, and a thread with under 2 MiB of C stack compiles
  with the C compiler.

Checked on `b4c2a8a`: `cargo test --lib` in the serc crate passes 1,257;
`tests/test_serc_compile.py` passes 7; `test_compile test_grammar
test_syntax test_patma test_exceptions` pass under `--serc-verify`; every
review reproducer behaves as on the C compiler. With the Serc CLI against the
C compiler (scratch harness, not committed), the review's corpora show no
behaviour difference in 10,533 programs (5,331 now routed) and no layout
difference absent from the pre-review build. Layout differences that
predate this campaign remain: 19 targeted, 184/719/42 in the three random
control-flow corpora, 164 in the comparison grid (down from 288), plus
`a and False and b` statements, a decisive folded discard ending a try body
at a function's end, and a chained-comparison break in a `while` inside
`finally`. These are verify-mode-only (line tables, exception tables, jump
shapes).

**Close-out verification at `8930b8a` (2026-11-11, macOS arm64).** An
incremental `perf.py build --name perf-rust-serc-v3 --serc --incremental`
records the clean committed overlay and verifies 58 release Rust extensions.
`perf.py test --name perf-rust-serc-v3 --all` and the same command with
`--serc-verify` each report 457 test files OK and exactly the five failing
files in the existing `perf-rust` baseline: `test_asyncio.test_sendfile`,
`test_asyncio.test_sslproto`, `test_multiprocessing_forkserver.test_misc`,
`test_ssl` and `test_support`. Each runs 52,102 tests with 2,878 skipped.
The process-pool flake does not recur. The verify run uses a fresh bytecode
cache and reports no compiler difference. Logs are
`rust-cpython/logs/perf-rust-serc-v3-all.log` and
`rust-cpython/logs/perf-rust-serc-v3-all-serc-verify.log` in the C2 worktree.

`serc_parity.py --oracle rust-cpython/stage-perf-rust/bin/python3.16`
reports 10,005 cases: 9,159 exact matches, 813 Serc refusals under the
finally-exit routing rule, and 33 oracle syntax refusals across 11 files.
There are zero mismatches, panics or other Serc errors. The census exits 0;
its result is `.work/tmp/c2-close-parity.json` in the C2 worktree.
Timing gates remain deferred to **Close** under OD14.

The independent close-out review of `b1ffca1..8930b8a` finds no new defect
in the fixes or the compile-hook assumptions they use. The 2 MiB stack
cutoff relies on the empirical headroom analysis. The regression test covers
a 64 KiB thread, not the cutoff boundary or continued Serc use above it.
C2 closes on `campaign/c2`, and C3 feature acceptance closes on `campaign/c3`
(see C3's exit evidence). Continue J's T3/T4 feature backlog under OD16.
The routing rules, compiler API and private hook changes remain in the
asynchronous owner queue. Nothing is pushed.

**Parity.** The eight Serc 3.16 defect classes the first full-suite runs found
are fixed in `serc-overlay`, each pinned by a Serc test against
`perf-upstream` (marshal fixtures in `tests/fixtures/python316_parity/`,
refusal tests in `compiler/tests.rs`):

- slice constants intern their identifier-like strings (gh-129724), sharing
  the interned identity of equal names and constants;
- annotation text writes a tuple comprehension target without parentheses
  (`for i, _ in`) and keeps a generator's parentheses inside a subscript;
- f- and t-string value patterns are refused;
- a new pass, `compiler/static_blocks.rs`, refuses a code unit with more than
  `CO_MAXBLOCKS` (21) active frame blocks; it mirrors each
  `_PyCompile_PushFBlock` site in `codegen.c`, including an inlined
  comprehension that saves locals (gh-156091). `__classdict__` is refused as
  a type-parameter name;
- annotation scopes are never async, so an async comprehension in an
  annotation is refused even inside an async function; a name local to an
  inlined comprehension in an annotation scope loads as a global elsewhere
  in that scope (the `UnboundLocalError`);
- a comprehension in a class-visible annotation scope passes its iterable to
  the comprehension function, which calls `GET_ITER` itself from 3.15 (the
  `expression changed stack depth` error); a module-level generic class
  header keeps its type parameters fast and allocates comprehension slots
  before cells;
- a statement-level Boolean expression folds its constant operands, with
  CPython's locations for a deciding constant and for a fold that leaves one
  operand (`9and x`);
- `try: pass` on the `try` line before `except*` keeps the `pass` location on
  its terminal exit;
- a bare name annotation such as `(y): int`, which emits no code, leaves a
  class epilogue its class location (`test_ignore_non_simple_annotations`
  under verify).

A second round fixed the remaining verify-only classes but two, the open
fuzzing defects, and one more that fuzzing found, each pinned by a marshal
fixture from `perf-rust` (the C compiler, same source):

- wrong code: an inlined async comprehension excluded everything from its
  `CLEANUP_THROW` to its `END_ASYNC_FOR` from the coroutine's
  `StopIteration` region, and in emission order that span holds the loop
  body. With a target that binds no name (`[1 async for o.a in r()]`) no
  restore handler covered the body, so a `StopIteration` from the store
  escaped the coroutine instead of becoming `RuntimeError`. Only
  `CLEANUP_THROW` is excluded now;
- wrong code: a comprehension filter's rejection jump was excluded from an
  enclosing `with` on the same assumption, so with a nameless target an
  exception at that back edge's eval-breaker check (a signal, an async
  exception) skipped `__exit__`;
- `expression changed stack depth`: an assignment's deferred restore applied
  to every async comprehension compiled under it; only the stored one defers
  now. Its removed `SWAP` counts toward CPython's four-instruction exit-copy
  limit, so a terminal `y = [... async for ...]` keeps CPython's
  `JUMP_FORWARD` (the multi-line async list comprehension in `test_compile`);
- a `try`/`finally` whose body is only `pass`, nested in an exceptional
  `finally` copy, leaves its surviving marker without an exception owner;
- a NOP whose block predecessor has its line is dropped, as
  `basicblock_remove_redundant_nops` does, unless a label separates them, and
  a folded `while False:` marks its loop header. This removes the NOP after
  the module's deferred-annotation setup (`test_type_annotations`). The rule
  brings `assembler/passes/control_flow.rs` into the overlay, otherwise
  unchanged;
- `global` and `nonlocal`, which emit nothing, no longer locate an `except` or
  `except*` handler's normal cleanup;
- a `with` body ending in `if`/`elif`/`else` whose other arms leave by
  `break`, `continue`, `return` or `raise` keeps its `POP_BLOCK` marker with
  the remaining arm's line, also in a loop with an `else` suite;
- a folded constant jump is never a small-exit copy site on 3.16, since
  CPython folds it after `inline_small_or_no_lineno_blocks` has run.

**Abrupt exits from `finally` (2026-11-10).** A `return` in an exceptional
finalizer now follows `codegen.c`'s fblock order: iterators entered inside the
finalizer are discarded before `FINALLY_END`, enclosing `finally` bodies and
`with` exits run before the value is returned, and the handler region covers
the finalizer's `POP_TOP` but not its `POP_EXCEPT`. The separate direct-return
path was removed. Six marshal fixtures pin the crashing and skipping repros
(`try_finally_loop_return_under_except`,
`try_finally_direct_return_under_except`,
`try_finally_loop_return_under_handler`, `return_in_finally_under_finally`,
`return_in_else_finally_under_finally`,
`return_in_finally_under_finally_no_raise`); `serc_parity.py` now generates
270 `try`/`finally`/`except`/`with`/`for`/`while` programs (nesting depth up to
3, `return`/`break`/`continue`). `cargo test --lib` in the serc crate passes 1,250;
`perf-rust-serc-v3` builds clean; both review repros equal the C compiler; the
parity census reports 0 mismatches in 10,005 cases; the full suite fails
exactly the e891 set.

Evidence at `52091c9`, from an empty bytecode cache on macOS arm64:
`perf.py test --name perf-rust-serc --all` fails exactly the e891 set
(`test_asyncio.test_sendfile`, `test_asyncio.test_sslproto`,
`test_multiprocessing_forkserver.test_misc`, `test_ssl`, `test_support`), and
with `--serc-verify` that set plus `test_compile`. The in-process census
(`marshal.dumps(compile())` per C1 corpus file and level) equals `perf-rust`
(the C compiler, same source) and `perf-upstream` in all 9,135 cases, with and
without `PYTHONSERCVERIFY=1`. `serc_parity.py --oracle` against `perf-rust`,
whose corpus also holds that build's overlay modules, has 9,195 cases, 9,162
exact, 0 mismatches and 33 oracle refusals. `cargo test --lib` in the serc crate passes
1,243 tests, `tests.test_perf` and `tests.test_build` pass 200, and
`tests/test_serc_compile.py` passes on `perf-rust-serc`.

Under verify, `test_compile` now matches the C compiler.
`test_condition_expression_with_redundant_comparisons_compiles` (`if 9<9<9and
9or 9:9` and its longer forms) is fixed: CPython threads the comparison's jump
through the folded `and 9` jump, drops that now unreachable block, and then
removes the cleanup's jump to the block that follows. Serc removed redundant
forward jumps before the folded jump's retained NOP was compacted, so a
zero-length `JUMP_FORWARD` stayed after the cleanup's `POP_TOP`. The linear
pipeline now repeats the redundant-jump scan after the final NOP and borrow
marker compaction. `tests/fixtures/python316_parity/redundant_comparison_condition.py`
pins the comparison forms and `x or 0 and y`.

`test_try_except_in_while_with_chained_condition_compiles`
(`while name_3 <= name_2 > name_1:` around `try: raise` with `except` and
`finally`) now matches. Two causes, both in the small-exit copy:

- CPython's first loop-body block (the two try-setup records, the `try` NOP and
  `RAISE_VARARGS`) is a small exit, so `inline_small_or_no_lineno_blocks` copies
  it onto the chained comparison's `JUMP end` and the comparison cleanup lands
  after the raise. Serc marked every non-jumping final comparison's fallthrough
  jump as ineligible (`prevent_jump_inlining`), so it jumped over the cleanup.
  That jump now carries the retained-NOP policy the copy needs, and the copy
  leaves the jump a NOP, as CPython's does.
- The finalizer's converted `POP_BLOCK` had no line. Its sole normal
  predecessor is the handler's implicit return, which neither the folded-exit
  donor (which requires a terminating prefix, absent for a bare `raise`) nor any
  other donor covered. `sole_handler_normal_exit_range` supplies that handler
  tail's line.

`tests/fixtures/python316_parity/while_chained_try_finally.py` pins the result;
it fails on the unpatched assembly (exit 101, `co_code` and both tables differ)
and passes with these changes.

Evidence for the small-exit round, on macOS arm64 from an empty bytecode cache.
`perf.py build --name perf-rust-serc-v3 --serc` exits 0 (58 release Rust
extensions verified). `perf.py test --name perf-rust-serc-v3 --suite
test_compile --serc-verify` left exactly the four
`test_condition_expression_with_redundant_comparisons_compiles` subtests.
`perf.py test --name perf-rust-serc-v3 --all --serc-verify` fails six files:
the e891 set plus `test_compile` only (456 OK, 52,102 tests, 2,877 skipped).
`serc_parity.py --oracle stage-perf-rust/bin/python3.16` reports 9,195 cases,
9,162 exact, **0 mismatches** and 33 oracle refusals, exit 0.

The three verify classes are fixed. A statement-level Boolean that folds to a
constant (`9 and True`) keeps CPython's end boundary between the selected
constant and the discard; a terminal Boolean ending in a nested deciding
operand (`x or 0 and y`) lays its fallthrough and short-circuit joins out
separately, and the folded operand's location remains the fallthrough donor.
`tests/fixtures/python316_parity/folded_boolean_statement.py` and
`redundant_comparison_condition.py` pin them. An abrupt `return` in a nested
finalizer (`try: pass` / `finally: try: return finally: pass`) keeps the outer
handler region through the `FINALLY_END` `POP_TOP`: the cold-block pass now
keeps a region end at its exact boundary label instead of the containing
block's first label. `tests/fixtures/python316_parity/nested_finally_return_in_finally.py`
pins it. The review-relevant bytes are in `rust-cpython/serc-overlay` only.

Evidence for this round, on macOS arm64 from an empty bytecode cache.
`perf.py build --name perf-rust-serc-v3 --serc` exits 0 (58 release Rust
extensions verified, 374s). `cargo test --lib` in the serc crate passes 1,260, including
the three new marshal fixtures. `PYTHONSERCVERIFY=1` compiles each repro
(`l7src.py`, `r_or.py`, `r_9andtrue.py`) with `same`. `python -m test
test_compile test_grammar test_syntax test_patma test_exceptions` under verify
reports `Result: SUCCESS` (857 tests, 5 skipped). `serc_parity.py --oracle
stage-perf-rust/bin/python3.16` reports 10,005 cases, 9,972 exact, **0
mismatches** and 33 oracle refusals, exit 0.

Earlier differential fuzzing (300 deep-nesting programs and 120 Boolean
statements against `perf-upstream`) agreed on every static-block refusal.
Fuzzing against `perf-rust` still finds pre-existing differences outside the
CPython suite and the C1 corpus; none is new after this round:

- Boolean expression statements whose last operand folds to a constant
  (`9 and True`: CPython keeps `LOAD_CONST True; POP_TOP` because the Boolean's
  end label stops its pair removal, Serc drops both) or ends in a nested
  deciding constant (`x or 0 and y`: Serc leaves the fallthrough exit copy
  unlocated) differ in code or line table;
- generated programs: async comprehensions with nameless targets and nested
  comprehensions, 65 of 1,200 differ; Boolean conditions with constant and
  chained operands, 74 of 600; `with` bodies ending in conditional exits inside
  loops, 4 of 500.

The third round classified all 143 by running each program's top-level
function on both stage interpreters (`stage-perf-rust` and
`stage-perf-rust-serc`, verify unset) with an exception injected at every
instrumented operation (attribute load/store, call, iteration, await,
`__enter__`/`__exit__`) and comparing results, exception types and messages,
and side-effect logs, over three truth profiles. A code difference was
compared after collapsing `NOP`s and mapping jumps and exception ranges to
instruction indices, so cache, line-table and NOP placement do not count as
semantic.

| family | differ | layout-only | behaviour-identical, shape/latent delta | observable |
| --- | ---: | ---: | ---: | ---: |
| async comprehensions | 65 | 44 | 16 | 5 |
| Boolean statements | 74 | 34 | 40 | 0 |
| `with` in a loop | 4 | 4 | 0 | 0 |

In the exit's two classes, 5 of the 143 observe a difference and 138 are
behaviour-identical, 82 of them pure layout.

The five observable async programs all read a comprehension target whose
binding belongs to a sibling comprehension: Serc resolved it to the enclosing
function's fast local or cell (`LOAD_FAST_CHECK`/`LOAD_DEREF`), CPython to the
global (`LOAD_GLOBAL`). In normal mode this is silent wrong code: the fuzzed
programs raised `UnboundLocalError` where CPython read the global (one of them
inside an `async with`, so the manager's `__aexit__` saw a different
exception), and a program with a global of that name would read the wrong
value. The minimal repro is
`tests/fixtures/python316_parity/nested_comprehension_sibling_target.py`:
`def f(): return [[k for i.a.b in r] for j in q if [i for i in q]]`. The
scope analysis treated any binding found anywhere in a comprehension
expression, including one owned by a nested comprehension, as bound in the
enclosing comprehension, so no outside reference was recorded. The overlay
now carries `compiler/scope.rs`; `own_bound` is the current comprehension's
generator targets, and only those enter the seen-target set.

Sixteen async programs were behaviour-identical but not layout-only; three of
them shared the sibling-target cause and are now byte-identical. The
remaining thirteen are latent: twelve keep an extra comprehension cell
(`MAKE_CELL` and `co_cellvars` gain the target; some never read it, others
read it with `LOAD_DEREF` only in a generator expression the program never
executes) and one uses `LOAD_FAST` where CPython keeps `LOAD_FAST_CHECK`; no
executed path observes either. The forty Boolean shape deltas come from
CPython folding constant operands such as `1 and b` before the later jump and
exit cleanup; Serc evaluates the constant and folds later. The differing
instructions are constant loads, copies, jumps, pops and exits; the
non-constant loads, calls and stores keep their order, and the harness
observed the same effects. The four `with` cases are one `NOP` CPython keeps
at the manager-merge label, which changes code size, jumps and the exception
table's target offsets but no handler depth or protected instruction.

Evidence: `cargo test --lib` in the serc crate passes 1,243 tests (the new fixture fails
on the unpatched analysis); with the fixed Serc CLI the async corpus drops
from 65 to 57 differing, and the five observable programs and the three latent
sibling-target programs become exact; the C1 corpus census against
`stage-perf-rust` is 9,195 cases, 9,162 exact, 0 mismatches, 33 oracle
refusals.

**Memory.** Peak RSS regresses on every C2 workload, and its cause is Serc's
code size, not how it is loaded. Linking Serc grows `libpython` `__text` from
3,037,592 to 11,040,560 bytes (+7.6 MiB) and the file from 7,349,056 to
21,965,984 bytes. Peak RSS on
macOS counts clean text pages, so each process pays for every Serc page it
executes while its physical footprint barely moves. Page residency (each
16 KiB page of `libpython`'s `__TEXT` split into its own map entry by
alternating `madvise` advice, then read with `vmmap`) shows:

- `python_startup`'s memory command is `-c "import time; time.sleep(0.12)"`,
  and Serc compiles that string: it makes 212 more `__TEXT` pages resident
  (3.3 MiB) than the same interpreter running a `.pyc`, where the C compiler
  adds 26 (0.4 MiB). Linking alone, with no Serc compile, adds 224 KiB of
  dirty `__DATA_CONST` (rebased pointers) and 304 KiB of `__LINKEDIT`.
- A cold startup (`-B -X frozen_modules=off`, empty cache) makes 419 of the
  496 pages of Serc's text region resident (6.5 MiB).

A separately loaded image, mapped on first compile, would therefore change no
gated ratio: every C2 workload compiles through Serc, and only the 0.5 MiB of
link-time pages would move to first use. Reordering functions would help the
one-line `-c` compile, never a workload that compiles the stdlib. Shrinking
the code is the only lever: Serc's own CLI build (fat LTO, one codegen unit)
has 7,357,092 bytes of `__text` at `opt-level=3`, 5,734,732 at `s` (-22%) and
3,694,152 at `z` (-50%), and `z` spends about 50% more CPU compiling the
stdlib (one noisy pair of runs). Halved, a cold startup would still carry about
3.3 MiB per process, against a 1% verdict floor of about 0.4 MiB on
`cold_python_startup`'s 41.9 MiB peak RSS and 0.14 MiB on `python_startup`.
The bar cannot be met while Serc runs in the measured processes, so this run
changes neither the loading model nor the build profile.

OD15 makes that peak RSS a recorded cost and gates physical footprint
within +8% (`memory-only-serc-cost` in `perf_verdict.py`). With clean builds of
`perf-rust-serc` at `52091c9` and `perf-rust` at `7492896` (the same `overlay/`),
`perf.py bench --baseline perf-rust --candidate perf-rust-serc --memory-only
--matched-prefix --workload python_startup --workload cold_python_startup
--workload cold_import_django --workload compileall_source --runs 2` is
WITHIN-BUDGET under that policy with matching digests. Physical footprint:
`python_startup` 1.046 (CI95 1.041-1.060), `cold_python_startup` 1.035
(1.021-1.059), `cold_import_django` 0.979 (0.969-0.998), `compileall_source`
1.058 (1.041-1.063). Recorded peak RSS cost: 1.236, 1.345, 1.164 and 1.295.
An earlier pair at `7492896` read the same within noise (footprint
0.980-1.058).

### C3: all source modes

Extend the Serc path to `eval` and `single`, with C1's oracle extended to those
modes and C2's exit rerun. AST-object input (`compile(ast_obj, ...)`) stays on
the C compiler: Serc consumes Ruff's AST, and converting CPython's is out of
scope.

**Exit:** done-criterion 4 and a C3 ledger row.

**Exit evidence (2026-11-11).** Feature correctness is complete on
`campaign/c3` at `fb131e1`; performance acceptance stays deferred under OD16.
Both full default-resource suites on `perf-serc-c3`, normal and
`--serc-verify`, run 52,102 tests and match the non-Serc baseline's five-file
failure set: `test_asyncio.test_sendfile`, `test_asyncio.test_sslproto`,
`test_multiprocessing_forkserver.test_misc`, `test_ssl` and `test_support`.
There is no compiler verification difference. The expanded 12-suite focus,
including interactive compilation, Unicode lookup, unpacking and audit
behavior, passes with verification enabled, as do all 11 native integration
tests. The Serc census compares 30,318 cases across `exec`, `eval` and
`single`: 9,678 exact matches, 813 intentional finally routes, and 19,827
cases refused by the oracle, with no mismatch or uncovered mode. All 101
named source-mode fixtures compile directly through Serc at optimization
levels 0, 1 and 2. The 1,260 library tests, 24 CLI unit tests and mode/cache
integration pass. Independent reviews approve the glue, audit semantics and
parity corrections; the final review also checks 48 small differential
probes against CPython's exact marshal bytes. Native logs are in the C3
worktree's `rust-cpython/logs/perf-serc-c3-{all,all-serc-verify}.log`; the census
is `.work/tmp/c3-census-final.json` (release ThinLTO override recorded).

### C acceptance

A track C change lands only when, on `perf-serc-<item>`: the C1 corpus has zero
mismatches; `perf.py test --all` fails exactly `perf-rust`'s set; from C2 on,
the full-suite verify run reports no difference; and an independent reviewer
approves any change to FFI, ownership, or the compile hook. Under OD16 the C2
and C3 feature-completion exits do not wait for performance gates. Their
performance acceptance remains deferred to **Close**.

## Close

When performance acceptance resumes, Close first runs every deferred timing
gate on a quiet host: the
**Milestone gate** for the stacked J incumbent and the C2 and C3 cold-import
gates. A J regression found here is bisected
over the landed J steps and the offending steps reverted or fixed. Then, when
M4 and C3 hold on one epoch: build `perf-rust-jit-serc`, gate it with the
**Milestone gate** commands, rerun the C1 corpus and the J targeted suites on
it, then run done-criterion 6 on Linux x86-64. Write the closing ledger row,
rewrite **Status** with the final state, and clear resolved Owner queue
entries.

## Source epochs

An epoch is one pinned 3.16 commit with its own `perf-climb-ref`, incumbents,
A/A spread, and `S0`. Scores compare only within an epoch.

- A refresh happens only at a J milestone boundary, when the owner asks or
  when upstream has tagged a 3.16 pre-release since the current pin.
- A refresh reruns R0 on `main`'s code. The new epoch opens with an M0 row;
  milestones met earlier are re-gated before track J continues.
- If the refresh fails parity, the campaign stays on the previous epoch and
  retries at the next milestone boundary.
- Track C re-ports Serc to the new epoch and reruns C1's corpus before its next
  item. Track J never waits for it; C milestones count only on the epoch they
  were measured on.

## Owner decisions

- **OD1 (2026-11-01):** keep the pinned LLVM; see **Boundary**.
- **OD4:** memory gates stay as written; JIT against no-JIT keeps its peak RSS
  REJECT as a recorded cost.
- **OD7 (2026-11-02):** unboxing, escape analysis, inlining, and longer or
  speculative traces are in scope. Production stays JIT-disabled.
- **OD8 (2026-11-02):** fitness is kernel CPU.
- **OD9 (2026-11-02):** accepted steps land on `main` without owner review.
- **OD10 (2026-11-09):** track latest 3.16 `main` through **Source epochs**;
  CinderX is a performance reference only; new optimizer passes are Rust.
- **OD11 (2026-11-09):** track C ports Serc to the pinned 3.16 epoch only,
  runs in parallel with track J, and leaves TLS code untouched. The product
  compiler is not replaced.
- **OD12 (2026-11-09):** M4 stays the end goal. The campaign runs unattended;
  owner reviews are asynchronous except those listed in **Owner queue**.
- **OD13 (2026-11-10):** Serc's crate dependencies are fetched from crates.io,
  not the `stuff` mirror.
- **OD14 (2026-11-10):** feature completeness first. Timing gates are waived
  until **Close**; correctness, parity, memory, and code-size checks still
  gate every landing.
- **OD15 (2026-11-10):** peak RSS on `--serc` builds against a non-Serc
  baseline is a recorded known cost, as OD4 makes it for the JIT; a
  memory-only comparison of that pair gates physical footprint within +8%
  (`memory-only-serc-cost`).
- **OD16 (2026-11-11):** the host will remain busy. Finish feature completion
  with performance gates deferred, including gates otherwise due at **Close**.
  Keep correctness, corpus parity and independent review required. Preserve
  every performance threshold and report performance acceptance as deferred
  until measurements can resume; do not claim M4 or campaign completion from
  this waiver.
- **OD17 (2026-11-12):** Carbon is disabled for this campaign until the owner re-enables it. Independent reviews use an available worker through the Pi bridge. The review requirement and OD16 performance deferral remain unchanged.
- **Parked:** a method-based frontend (withdrawn PEP 836 prototype, observed
  head `04a79098`) opens only by owner decision.

**Integration reference (2026-11-11):** `campaign/jit-integrate` combines
`main` at `604ba93` with C3 and the four JIT slices `0b9a462`, `6acf8d2`,
`e7c970c` and `a03814c`. Main's version pins are preserved; the combined
Cargo-lock SHA-256 is
`bea6331da746e3a54a9d9184b1591392faffda038ab221b5638c5b8af4e4f846`.
The dependency refresh required three research `_ssl_rs` API adapters:
borrowed encrypted PKCS#8 decoding, the owned encryption-scheme type, and
discarding an entire malformed SAN extension as the earlier parser did.
Correct/wrong/no-password and malformed-SAN regressions pass on old and new
libraries; independent review confirms trust and verification paths are unchanged.

The matched reference is closed C3 plus those same main pins/adapters in
`.work/wt/jit-main-ref` (`perf-jit-main-ref`); the candidate is
`.work/wt/jit-integrate` (`perf-jit-integrated`). Both builds completed
incrementally after adapter correction; their reports identify the staged
merge over `604ba93` with dirty overlays. This is correctness evidence, not
a clean performance acceptance rebuild. All benchmark source files match
between these two trees. The candidate's JIT suites include descriptor,
lazy-import and eval-hook coverage; only the eight named superseded uop
assertions differ from JIT-off. Every training, held-out and guard digest
matches. Full compiler runs use `PYTHON_JIT=0`: both normal runs and the
candidate's verify run retain only `test_asyncio.test_sendfile`,
`test_asyncio.test_sslproto`, `test_multiprocessing_forkserver.test_misc`,
`test_ssl` and `test_support` failures. Evidence is in
`.work/tmp/integration-feature-acceptance.json`, `.work/tmp/full-compiler.json`,
`.work/tmp/native-serc-integration.log` and `rust-cpython/logs/perf-jit-integrated-all*.log`.
The stack landed on local `main` as `0e72f84`; its matched C3 reference is
`8083f4f` on `campaign/jit-main-ref`. Continue J feature work from the integrated tree; keep both reference stages
and earlier campaign stages for the deferred performance work. These changes are integrated in main.

**Owner queue:** R1 reference report (informational, no target change
requested): CinderX 3.15 steady JIT uplift `S = 0.7960`, held-out 1.0621, beside
M2 ≤ 0.48, M3 ≤ 0.38, M4 ≤ 0.25. T2 does not wait for an answer.

J continuation builds use `.work/wt/jit-pair`; its `perf-jit-pair` stage
was rebuilt incrementally for the store, list, float-call, loop-type, inline-instance-getter, virtual-float, virtual-local, virtual-exit, bound-method, regional-unboxing, constants/use-site-guard and integer scalar slices after
the tuple slice. Earlier reports and suite logs were archived under
`.work/tmp/tuple-tested-report.json`, `tuple-accepted/`,
`glue-tested-report.json`, `glue-accepted/`, `list-tested-report.json`
and `list-accepted/`, plus `arith-tested-report.json` and
`arith-accepted/`, followed by `loop-types-tested-report.json` and
`loop-types-accepted/`, then `managed-getter-tested-report.json` and
`managed-getter-accepted/`, then `virtual-tested-report.json` and
`virtual-accepted/`, then `virtual-locals-tested-report.json` and
`virtual-locals-accepted/`, then `virtual-exits-tested-report.json` and
`virtual-exits-accepted/`, then `bound-floats-tested-report.json` and
`bound-floats-accepted/`, then `regional-tested-report.json` and
`regional-accepted/`, then `constants-tested-report.json` and
`constants-accepted/`, before reuse. Each ledger row
has distinct digest-result directories and source identities. The committed
`4b66ce3` reference in `.work/wt/jit-integrate` remains unchanged.

**Owner queue:** T3 leaf calls (landed locally, async review). New private
`_PyJitOpt_PlanLeafCalls` ABI and `_CALL_PY_LEAF` tagged result caches;
ownership, pre-call deopts and frame elision independently reviewed. Seven
native leaf cases, five slot-getter traces and the ten unboxing kernels pass
together. The result tags now distinguish aligned constant addresses,
argument indices and slot offsets; the slot load requires the existing
optimizer's discharged layout proof and retries missing slots before the call.
The three
new named upstream uop-shape exclusions follow the existing owner ruling;
an independent run bypassed only those obsolete assertions and passed every
remaining original assertion. Performance acceptance is deferred under OD16.

**Owner queue:** T3 inline instance getters (async review). The private
leaf opcode map grows from 80 to 84 bytes and result tag 3 identifies managed
inline values. Mutable storage validity remains a pre-consumption runtime
guard, even when type guards have been proved outside the callee. Independent
source/native review approves dictionary replacement and clear during active
JIT execution; one additional named frame-count exclusion retains all original
numeric/type/function assertions. Performance acceptance remains deferred.

**Owner queue:** T3 global getters (landed locally, async review). The
private leaf opcode map now includes a pre-call callee-dictionary version
guard. Existing watcher dependencies protect cached mortal values; a native
reentrant-finalizer regression receives the replacement value after the old
global is collected. Independent source/native review approves. Dictionary
subclasses and unresolved lazy loads keep concrete calls. Performance
acceptance remains deferred under OD16.

**Owner queue:** T4 cache allocation (landed locally, async review). New
private `_PyJitOpt_PlanCache` ABI selects generated cache depths across the
trace without speculative stack prefetch. Independent source/native review
approves, including disabled optimization and unboxed exits. The native call
fixture emits 7 spill/reload stencils against J3's 13; the float fixture emits
2 against 3. These are instruction counts, with performance acceptance
deferred under OD16.

**Owner queue:** T3 bound virtual methods (async review). The private
leaf opcode map grows from 118 to 122 bytes, and virtual begin's second cache
selects standalone/bound shape. Receiver borrows and unknown owned attributes
have separate Rust states. Guard exits preserve their original callee targets;
self cleanup follows all scratch reads. Independent source/generated/native
review approves the held-method mutation and finalizer regressions. Performance acceptance remains
deferred under OD16.

**Owner queue:** T3 constant/use-site proofs (async review). Begin's
all-float argument gate is replaced by original-PC use-site guards; unused
arguments may now finalize during success cleanup. Type and ownership proofs
remain separate. C now consumes variable-length private leaf plans from an
immutable snapshot with transactional count/source/capacity checks; layouts
are unchanged. Independent source/generated/native review approves reverse
argument finalization with code-arity changes and later errors. Performance
acceptance remains deferred under OD16.

**Owner queue:** T3 integer scalar calls (async review). The private
opcode map grows from 122 to 150 bytes; five virtual-frame uops now use generic
names without aliases. Typed ownership preserves integer operand references,
compact checks and old-kind local cleanup. Helper overflow/allocation refusal
uses terminal interpreter resumption, rather than the ordinary cold-exit tracing
path; bounded injected allocation counts need not match, while eventual errors
retain the original operation and available locals. Independent review approves
source/generated/native semantics and four exact new shape exclusions; all
remaining original assertions pass. Performance acceptance remains deferred.

**Owner queue:** T3 loop-global prologue (async review). New private
`_PyJitOpt_PlanLoopGlobals` ABI and `_GUARD_LOOP_GLOBALS_VERSION` entry guard;
the backedge is derived from `_MAKE_WARM` rather than fixed at index 1.
Existing dictionary watchers and per-iteration validity checks remain.
Independent review approves the corrected side-exit progress path and both
entry-failure cases. Performance acceptance remains deferred under OD16.

**Owner queue:** T3 immediate tuple results (async review). Private
`_PyJitOpt_PlanLeafCalls` opcode-map size changes from 54 to 62 bytes;
`_CALL_PY_LEAF_PAIR` removes two-argument tuple results only at an immediate
unpack. Callable-lifetime refusal preserves tuples visible to finalizers;
trace/profile/monitoring observers retain ordinary frames and tuples.
Independent source/native review approves. Performance acceptance remains
deferred under OD16.

**Owner queue:** T3 immediate list results (async review). The private
leaf opcode map grows from 62 to 66 bytes and records list construction and
unpack identities. Container-kind matching and complete validity-check
consumption preserve deopt stack shape. The same pre-consumption side exits
and finalizer-visible container fallback apply to both types. Six actual
native traces and independent review approve; performance remains deferred.

**Owner queue:** T3 float-call materialization (async review). The private
leaf opcode map grows from 66 to 80 bytes. New `_CALL_PY_FLOAT_LEAF` and
`_MATERIALIZE_FLOAT_LEAF_ERROR` instructions retain a separate callee error
PC and borrowed-operand recipe while call exits still target the caller.
The error stub consumes untouched CALL inputs, moves their references into
an already-space-checked frame and propagates the original pending exception.
It allocates nothing and never retries. Native allocation failure, reverse
division, sole-callable traceback lifetime and successful finalizers all prove
active-JIT execution; independent review approves. Performance is deferred.

**Owner queue:** T3 numeric loop-local invariants (async review). New
private `_PyJitOpt_PlanLoopTypes` ABI and exact local entry guards prove a
callback-free closed cycle. Integer range checks remain; pending work takes
a terminal exit before callbacks. Failed guards use cold side exits for
progress. Allocation/capacity failure retains the original trace. Independent
source/native review approves; performance acceptance remains deferred.

**Owner queue:** T3 virtual float-call operands (async review). The private
leaf opcode map grows from 84 to 108 bytes. New begin/argument/return/error
instructions use an exclusive reserved callee operand stack and held caller
references, retaining only a minimal unlinked header on success. Precise
error materialization initializes that same address without copying live
operands or allocating. Original in-place forms are normalized together with
NULL cleanup; these traces stay boxed. Independent source/generated/native
review approves all seven active-JIT fault/finalizer checks. General locals,
callbacks and unboxing remain outside this slice; performance is deferred.

**Owner queue:** T3 virtual float locals (async review). Private leaf map
116 bytes and a new begin-cache extent guard bound scratch-local access.
`_COPY_VIRTUAL_FLOAT_ARGUMENT` preserves DUP tags; potentially borrowed copies
need explicit heap safety before storage. The proof rejects argument writes
and dangling local borrows, preserves aliases/NULL slots on error and closes
all extra locals before callback-capable cleanup. Independent review approves
source/generated code and six active-JIT native cases, including a generator
caller. Performance acceptance remains deferred.

**Owner queue:** T3 virtual-call side exits (async review). Private map
118 bytes adds power; `_MATERIALIZE_VIRTUAL_FLOAT_FRAME` replaces the error-
only name with an explicit error/resume selector. Cached side-exit operands
spill before materialization, while Begin guards still exit at the caller.
Independent source/generated/native review approves 29 activity samples,
complex resumption and exact progress; assertion-enabled tail validation
accepts the renamed stub and retains rejection of ordinary tail instructions.
Performance acceptance remains deferred.

**Owner queue:** T4 local-store fusion (async review). New private
`_PyJitOpt_PlanStores` ABI and `_STORE_FAST_FUSED` uop. Generated escape/local
flags retain stack publication and unboxing barriers; IP changes and validity
checks remain boundaries. Two additional named pop-count exclusions follow
the existing owner ruling, with every other original assertion independently
verified. Native finalizers observe the new local and can mutate it before
resumption. Performance acceptance remains deferred under OD16.

**Owner queue:** Recent T3 and T4 private protocols (landed, async review).
Borrowed argument proofs use begin-cache bit 31 to infer actual call shape
only when both Rust shape proofs agree. Typed ordinary stores initially
grow the private store map to 26 bytes. Guarded old-float cleanup extends
it to 36 bytes and adds a closed hint to 6-byte input descriptors. Output
steps stay 8 bytes. The hint selects a pre-store runtime check and never
proves the old value's type. Nested identity composition grows the leaf
map to 166 bytes and reserves bit 63 of argument-result recipes for virtual
mode. Readonly Boolean attribute predicates extend that map to 180 bytes with
COPY, exact-Boolean, NOT and popped bit-guard identities. Attribute-origin
aliases narrow together; arbitrary final owned attributes retain ownership.
Guarded state updates grow the map to 188 bytes and initialize a Boolean fact
at descriptor byte 5 without changing its 16-byte size. Each selected store
retains its original PC and layout guards; callback-capable old values resume
the ordinary store with prior writes intact.
Virtual mode checks recursion and non-finalizing ownership before
consuming inputs. The current `_PyJitOpt_PlanExitStubs` ABI uses 16-byte
descriptors with float and integer masks and transactional u32 source indices.
Identical complete resume recipes may share their boxing-error continuation.
Ordinary errors and separate virtual regions remain independent. Reviews approve
these contracts and their native paths. Production and public Python APIs
stay unchanged. Performance acceptance remains deferred under OD16.

**Owner queue:** Borrowed double-array unboxing protocol (landed, async review).
The existing 8-byte descriptor adds array-read kind 7 and closed pop details.
Synthetic action 16 is appended to the existing 15 actions. Callers supply
16 synthetic descriptors, with unchanged action numbers and 16-byte steps.
The original boxed recipe remains the refusal path. Owned mortal arrays
refuse before effects. C and Rust must be built together from one overlay.

**Owner queue:** Allocation-site tracing policy (landed, async review).
The lane stays in tier one while `tracemalloc` is active. Existing cached
executors remain available after tracing stops. Callback transitions use
the existing escape and validity boundaries. Python results and allocation
sites stay unchanged. This adds no public API, dependency or private ABI.

**Owner queue:** T4 virtual-body unboxing (landed, async review). The existing
Rust descriptor ABI now describes body instructions while entry and return
stay opaque. Virtual exits box raw masks before resume or clear them before
error materialization. Partial boxing failure retains converted references
and clears the remaining raw slots. The masked recipe above now permits
sharing identical boxed exits. Generated escape metadata recognizes the pure
frame stack getter. Independent source/generated/native review approves the unchanged
ABI and the new continuations. Performance acceptance remains deferred.

**Owner queue:** T1 thread-state (landed, async review). Every overlay build
now regenerates the interpreter cases from `bytecodes.c` with a 3.11+
`PYTHON_FOR_REGEN`, checked after configure; `libpython` exports three new
private symbols, `_PyFloat_FromDoubleTstate`, `_PyFloat_ExactDeallocTstate`,
and `_PyLong_ExactDeallocTstate`, which `_testinternalcapi` and the stencils
reference.

**Owner queue:** T1 generic thread-state deallocation (landed, async review).
`libpython` exports one more private symbol, `_Py_DeallocTstate`, which
dispatches exact floats and exact longs to `_PyFloat_ExactDeallocTstate` and
`_PyLong_ExactDeallocTstate` and every other object to `_Py_Dealloc`. In
`overlay/Python/bytecodes.c`, `_STORE_SUBSCR_LIST_INT` and the three
`_STORE_ATTR_*` sites use `_Py_DECREF_TSTATE`/`_Py_XDECREF_TSTATE`; `POP_TOP`
uses the NULL-tolerant `PyStackRef_XCLOSE_TSTATE`, because `STORE_FAST` hands
it the old local, which is NULL on first assignment; `_ITER_NEXT_RANGE` uses
`_PyLong_FromCompactTstate`. The inline helpers are private;
`_Py_DeallocTstate` is the only new exported name. Independent review: one
blocking finding (a non-X close in `POP_TOP` asserted on NULL in assert-enabled
builds), fixed; nothing else above low.

**Owner queue:** T1 frame pop fast-path inlining (landed, async review). New
overlay copy `Include/internal/pycore_interpframe.h` adds the private
static-inline helpers `_PyEval_FrameClearAndPopThreadFast` and
`_PyThreadState_PopFrameFast`; `overlay/Python/bytecodes.c` uses them at
`_RETURN_VALUE` and `RETURN_GENERATOR`. No new exported symbol; every other
case calls the existing `_PyEval_FrameClearAndPop` and
`_PyThreadState_PopFrame` unchanged.

**Owner ruling (2026-11-10):** a J candidate may fail, only with the JIT,
upstream tests whose sole failing assertion names a uop the candidate rewrites
away, when each test is listed by ID in `perf.CLIMB_SUPERSEDED_UOP_ASSERTIONS`
and its numeric checks still pass. `climb-step` compares suites per test from
regrtest's junit report, so any other failure in the same module still
rejects. Original question, for the record: four
upstream `test_capi.test_opt` tests assert that upstream's inplace float uops
appear in traces whose temporaries unboxing removes, so any T2 candidate that
unboxes a merge of two temporaries fails J acceptance step 5; int unboxing will
meet the same with the int inplace tests. The doc does not say whether such
superseded implementation assertions may differ with the JIT. The overlay
cannot patch `Lib/test/`, and keeping the inplace uops would forfeit the merges
this candidate exists for. Options: accept a named JIT-only failure list for
superseded uop-name assertions whose numeric results pass, recorded per
candidate (chosen); or allow an overlay patch to those tests.

**Owner queue:** T2 float unboxing contract (landed, async review). New
overlay crate `Modules/_jit_opt_rs` (no dependencies, linked into `libpython`
through `cpython-rust-staticlib`, exporting `_PyJitOpt_PlanFloatUnboxing`); its
hook in `uop_optimize` and the unboxed-slot stubs in `prepare_for_execution`;
the generated `_PyUop_StackShape` table from an overlaid
`uop_metadata_generator.py`; ten tier-2 uops (`_UNBOX_FLOAT_TOS/NOS`,
`_BINARY_OP_{ADD,SUBTRACT,MULTIPLY,TRUEDIV}_FLOAT_UNBOXED`,
`_COMPARE_OP_FLOAT_UNBOXED`, `_BOX_FLOAT`, `_BOX_FLOAT_STACK`,
`_CLEAR_FLOAT_STACK`); `PyStackRef_FromUnboxedFloat`/`AsUnboxedFloat`; and the
overlay `Cargo.lock` digest in `rust-cpython/sources.lock.json`.

**Owner queue:** T2 int unboxing contract (landed, async review). The crate
entry point is now `_PyJitOpt_PlanUnboxing` and `JitOptStep` carries
`exit_floats`, `exit_ints` and `error_unboxed` (16 bytes);
`_BOX_FLOAT_STACK`/`_CLEAR_FLOAT_STACK` become
`_BOX_UNBOXED_SLOTS`/`_CLEAR_UNBOXED_SLOTS`; seven tier-2 uops are new
(`_UNBOX_INT_TOS/NOS`, `_BINARY_OP_{ADD,SUBTRACT,MULTIPLY}_INT_UNBOXED`,
`_COMPARE_OP_INT_UNBOXED`, `_BOX_INT`); `libpython` exports
`_PyLong_FromCompactTstate`; `PyStackRef_FromUnboxedInt`/`AsUnboxedInt` join
the float helpers; the overlay now carries `Python/ceval.c` and
`Python/ceval.h` for the debug uop-interpreter dump; and
`test_int_inplace_chain_propagation` joins
`perf.CLIMB_SUPERSEDED_UOP_ASSERTIONS` (its result check passes first).

**Owner queue:** T2 array subscript specialization (landed; independent
review found no correctness defect, its low findings fixed). New overlay copies
`Python/specialize.c`, `Python/optimizer_bytecodes.c`,
`Modules/arraymodule.c`, `Tools/jit/template.c`,
`Include/internal/pycore_array.h` and
`Include/internal/pycore_arrayobject.h`; `Python/ceval.h` and `Python/jit.c`
gain the include. `arrayobject`/`arraydescr` move to the shared header so
`libpython` core never links against the builtin module. New specialized
opcodes `BINARY_OP_SUBSCR_ARRAY_D`/`_I`; new uops
`_GUARD_NOS_OBJECT_TYPE_VERSION`, `_BINARY_OP_SUBSCR_ARRAY_D` and
`_BINARY_OP_SUBSCR_ARRAY_I`; new exported symbols
`_PyArray_GetSpecializationKind`, `_PyArray_ReadDoubleItem` and
`_PyArray_ReadIntItem`, defined in `Python/specialize.c`. The
`BINARY_OP` cache carries the recorded type version in `external_cache[0..1]`.

**Owner queue (C2 contract changes, landed locally):**

- The compile hook in `overlay/Python/pythonrun.c` and `marshal.c` (compiled
  only under `Py_SERC`), the `serc-lane` C ABI, and its routing rule.
  Owner-accepted (2026-11-10): "A Serc error raises; it is never retried on
  the C compiler" is implemented with the C compiler as the diagnostic
  authority (it runs on a Serc refusal only to raise CPython's exception, and
  its code is never returned), and sources with the `barry_as_FLUFL` future or
  nesting deeper than 200 go to the C compiler by rule after Serc parses them.
- Routing added after the re-review, awaiting owner review: on 3.16, Serc
  leaves to the C compiler any `return` in a `finally` body and any `break` or
  `continue` that leaves one (the public `serc::CompileError` gains
  `RequiresCpythonCompiler`), and `serc_routes` sends requests from threads
  with under 2 MiB of C stack to the C compiler.
- `--serc` on `perf.py build` and `build.py build`, and `perf.py test
  --serc-verify`.
- `cold_python_startup`, `cold_import_django`, `cold_import_app_stack`: they
  add `-X frozen_modules=off` to the doc's `-B` and fresh empty
  `PYTHONPYCACHEPREFIX`, so the frozen startup modules compile too
  (owner-accepted, 2026-11-10), and they stay out of the default realworld and
  full suites.
- Serc API additions in `serc-overlay`: filenames as `surrogatepass` bytes,
  `decodes_as_utf8`, `CompiledModule::may_warn`, and `RequiresCpythonParser`.
- Code size: `libpython` `__text` +7.6 MiB on `--serc` builds.
- Serc now refuses sources with more than 21 statically nested blocks
  (`compiler/static_blocks.rs`); the hook then raises CPython's
  `SyntaxError` through the C compiler, as for every refusal.
- Memory (OD15, owner ruling 2026-11-10): peak RSS on `--serc` builds
  against a non-Serc baseline is a recorded known cost, and physical footprint
  gates within +8% (`memory-only-serc-cost`, `dabf1bf`). The C2 memory-only
  bench is WITHIN-BUDGET at `52091c9` (footprint 0.979-1.058, peak RSS
  1.164-1.345 recorded); no loading-model change was made (see C2).
- Independent re-review found nine defects, fixed in `852831a`..`b4c2a8a`.
  The close-out review of those fixes and their FFI/compile-hook assumptions
  finds no new defect; the empirical stack-cutoff validation limit is recorded
  in C2. Owner acceptance of the routing/API changes remains pending.

**Owner queue (C3, landed locally):** `serc::CompileMode` and
`CompileOptions.mode` select `exec`, `eval` or `single`; the default stays
`exec`, and non-exec modes currently require the 3.16 target.
`ParseCompileModeError` reports invalid mode spellings. The private C ABI
`_PySerc_CompileSource` replaces `_PySerc_CompileModule` and receives the
CPython parser start symbol. Source-mode routing now admits all three modes;
AST, `FILE *`, flags, decoding, stack and finally-exit boundaries stay as in
C2. The CLI adds `--mode` and separate non-exec cache names. The hook/FFI
reviews approve the glue, compiler API and CLI; the full suites match the
baseline failure set with no verification difference. Source entry points emit one `compile`
audit event before choosing a compiler, including verification and diagnostic
paths. The new Py_SERC-only private parser helper
`_PyParser_ASTFromStringWithoutAudit` requires that event to have been emitted
by its caller; an audit veto aborts before parsing. `PyRun_String` uses the C
parser if auditing changes the module-name snapshot or if dictionary keys can
invoke Python comparisons, preserving warning context and reference lifetime.
Sources containing `\N{` also use C so the interpreter owns Unicode-name
lookup and its import failures; this conservative test includes raw strings
and comments containing that spelling. Independent review approves these
rules and the source-mode parity fixes. C3's feature acceptance is complete;
performance acceptance remains deferred.

**Epoch e891** (`main` `e891a911`, pinned 2026-11-10) opened at R0 on macOS
arm64:

- **Parity.** `perf.py test --all` on the four builds: each overlay build
  fails only what its upstream counterpart fails
  (`test_multiprocessing_forkserver.test_misc`) plus known red: `test_ssl`
  (RTLS has no `HAS_ECH`, added upstream after a1), `test_support` (hashlib
  shim), and two post-a1 asyncio TLS tests, `test_sendfile` and
  `test_sslproto`, which depend on OpenSSL close-notify and BIO-buffer
  behaviour the frozen RTLS stack does not share. Fuzzer seeds 0-2 at 2,000
  programs: 0 mismatches. `perf.py` has no pydebug mode; the 3.16.0a1
  findings were rechecked on a pristine pydebug JIT build at the pin: the
  settrace block leak is gone, the `-R` failures of `test_capi.test_opt` and
  `test_monitoring` remain JIT-specific, and `test_optimizer` still leaks 6
  references per run with or without the JIT. The `effective_trace_length`
  pystats fix stays.
- **Measured.** A/A max 1.19% (`scimark_sparse_mat_mult`), geomean 1.0022.
  `S0 = 0.7748`, held-out 0.8216, both on a quiet host. Kernel peak RSS at
  most +5.4% (`hexiom`). Guard CPU ratios 0.980-1.006, from a run with one
  noisy host sample of six. M0 JIT code bytes (`perf.py jit-stats --ref
  perf-rust-jit --jit-set`): nbody 39,112; spectral_norm 18,880; float
  11,400; richards 144,612; raytrace 350,828; fannkuch 61,700; deltablue
  262,008; chaos 123,252; go 421,312; nqueens 37,352; hexiom 320,080;
  scimark_sor 48,428; scimark_sparse_mat_mult 11,684; scimark_fft 41,792;
  scimark_lu 56,796; scimark_monte_carlo 65,596.

**Epoch a1** (`v3.16.0a1`, `22d79d98`) closed at R0; its numbers do not carry
over. The P0 tooling it built stays: kernel-region CPU, the held-out set, the
differential fuzzer, and `climb-step`. Evidence from a1 that shapes the
backlog:

- Sampled cost ranking over 16 kernels: `_tlv_get_addr` 16.1%,
  `_PyFrame_ClearExceptCode` 3.2%, `PyFloat_FromDouble` 2.8%, `_POP_TOP` 2.3%.
  Executed-uop shares (Linux pystats): type guards 10.5%, `_SET_IP` 9.6%,
  `_CHECK_VALIDITY` 8.1%, `_SPILL_OR_RELOAD` 7.5%.
- Rejected one-site fixes: generic exact-float pop (REJECT, stencil 56 to 156
  bytes), outlined and inlined boxed-float reuse (INCONCLUSIVE and REJECT;
  parked at `lane/jit-boxed-float-reuse`), compact-int fast path
  (INCONCLUSIVE), whole-trace register cache (INCONCLUSIVE, 0.9993).
- Hybrid DynASM backend (python/cpython#146235): closed, not ported.

## Lane host

Linux x86-64 lane commands run in the local image `rust-cpython-linux:20260401`
(Ubuntu 24.04, the exact `linux-toolchain.lock.json` packages, rustup
`nightly-2026-09-15`):

```sh
docker run --rm --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  -v "$PWD:$PWD" -w "$PWD" -e TMPDIR="$PWD/.work/tmp" \
  rust-cpython-linux:20260401 <command>
```

Builds and `test --all` outlast 10 minutes; run them in the background. The
image is absent on the owner's macOS arm64 workstation (checked 2026-11-09).

## Debugging methods

1. **Compare against the sibling build.** `PYTHON_JIT=0` skips every
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
7. **pystats:** `perf.py build --jit --pystats`; per kernel,
   `sys._stats_clear()`, run, `sys._stats_off()`, `sys._stats_dump()` into an
   existing stats directory; summarize with the build's
   `Tools/scripts/summarize_stats.py`.
8. **Look in the locked LLVM archive, not the prefix,** for `opt`,
   `llvm-config`, `llvm-symbolizer`, headers, and the ASan runtime.
9. **Monitoring:** the JIT reports LINE events only at side exits, upstream
   included, so line sets are compared only between JIT-off runs. Event counts
   at `set_events` boundaries vary between identical runs.
10. **Bytecode mismatches:** diff `dis.dis(code, show_caches=True)` and the
    `co_linetable`/`co_exceptiontable` bytes before reading marshal output;
    most mismatches are in a table, not an instruction.
11. **Harness traps:** two containers in one directory collide on
    `test_python_<pid>`; a crashed test leaves mode-000 directories that break
    the next clean build (`chmod -R u+rwx`); delete a dropped `core`; never run
    bare `make` in a perf build directory; `rc=2` right after a crash is
    usually leftover scratch.

## Status

**Resumed 2026-11-13.** J feature completion continues under OD16; performance gates remain deferred.
Closeout cancellation reports inactive. Main and the retained candidate are clean; protected worktrees remain registered.
Integration: `main`, including guarded old-float cleanup, borrowed double-array producers, allocation-site tracing correctness, guarded Boolean state updates and readonly attribute predicates. Current correctness incumbent is `perf-jit-guarded-local-cleanup` in `.work/wt/jit-guarded-local-cleanup`, built clean with both `--jit --serc` to match its predecessor. All 58 Rust extensions verify and its raw overlay matches the final source. `perf-jit-guarded-local-cleanup-noserc` is the same-overlay, JIT-enabled OD15 control. Five interleaved resource trials pass matched RSS, isolated Serc footprint and M0 code-size bars. Raytrace maps 659,248 bytes, 1.8792x M0, below the unchanged 2x bar. This workflow does not push.

The lane controller now publishes extracted JIT inspection tools from writer-owned temporary directories. A deterministic two-extractor test reproduces the old shared `.part` collision and passes with complete executable contents after the fix; the locked-extraction and missing-archive tests also pass. The runtime overlay and LLVM pins are unchanged.

The repository moved from `~/d/python-build` to `/Users/josh/repos/laputa-systems/python-build`, including `.work/`, caches, Cargo homes, logs and overlay targets. Worktrees are re-registered; campaign branches and tags remain. Main now includes the shared build-repository contract, buildlib migration, opt-in cold-compile workloads and dependency refresh. Lane TLS forks are `laputa/0.23.46` (`9105f44f`) and `laputa/0.103.17` (`2978880e`); overlay Cargo.lock digest is `27c9e6ba…`. Distrusted certificates report verify code 28. The owner reports locked fetch, debug build, `test_httplib` and `test_urllib2_localnet` passing after refresh. Epoch e891 retains known `test_ssl` failures because RTLS lacks `HAS_ECH`.

| Lane | State | Resume / last evidence |
| --- | --- | --- |
| J | active: guarded old-float cleanup accepted | Root-frame observations select runtime guards before existing typed stores. No old type is assumed and no runtime stencil is added. Private store map/input/step widths are 36/6/8 bytes. 98 Rust tests and targeted FFI AddressSanitizer; 35 native integrations; 18 focused suites with the same 16 exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests. Independent source/generated review and verified leased final native review approve both variants. Matched RSS <=1.0045x incumbent; isolated Serc footprint <=1.0352x no-Serc; code <=1.8792x M0. Frozen nbody emits 18 guard/store pairs. Evidence: `.work/wt/jit-guarded-local-cleanup/.work/tmp/guarded-local-cleanup-feature-acceptance.json`. |
| C | feature complete | C3: integrated normal/verify each run 52,102 tests with the same five reference failures and 0 compiler mismatches; native 11 passes. C2/C3 performance acceptance remains deferred. |
| R1 / Close | done / deferred | R1 catalog below; Close still needs deferred gates and the owner-approved macOS-only scope. No M4 or overall completion claim. |

Completed slices: numeric parameter reassignment, typed store fusion, dual numeric entry guards, borrowed argument attributes, guarded nested identity methods, virtual-body unboxing, plain or masked resume stub reuse, readonly Boolean predicates, guarded state updates, allocation-site tracing, borrowed double-array producers and guarded old-float cleanup. The current profile basis is `perf-jit-array-read-unbox`: 60,701 samples and at least 95.39% named JIT attribution per kernel. Root `.work/tmp/jit-array-read-unbox-profile-summary.json` retains reports and shares. `jit-guarded-local-cleanup-frozen-shapes.json` proves 14 inner and 4 outer guarded stores in nbody. Next: remove redundant format parsing for exact-float double-array assignments in the existing C runtime. The profile traces `vgetargs1_impl` through `d_setitem`; its 1.41% mean self share grounds that bounded T1 candidate. Parked JIT array-store experiments remain parked. Broader lookup, allocation, loop-peeling and stencil work remain open. Carbon stays disabled under OD17; performance acceptance remains deferred under OD16.
Parked compiler work: `campaign/c0` (pre-existing report committed as `e2b4fde`), `campaign/c2-layout`, `campaign/c2-memgate`; historical/unreviewed alternatives, not required for closed C3. Review against main before any reuse or integration.
Parked J/reference work: `campaign/t2-store`, `campaign/t3-pending` (array-store experiment), `campaign/t3-inline` (dropped linked-frame experiment), `campaign/r1` (older CinderX study), `campaign/jit-main-ref` (matched C3 reference). Reopen only for an explicit feature/reference review.
Retained `.work/wt/jit-array-allocation-site` holds the accepted tracing policy, final fixture, matched clean stages and correctness/resource evidence. `jit-array-allocation-site-review` holds its runtime source snapshot; `jit-array-allocation-site-final-review` holds the fixture addendum. Root `.work/tmp/jit-array-allocation-site-{source-review,independent-review}.json` record approvals. Source review resolved a withheld proof by tracing existing escape metadata and raw-value barriers. The final review independently executed both variants. The weakref case checks post-callback allocation sites and progress; separate cold, warm and finalizer cases check JIT resumption.
Retained `.work/wt/jit-array-read-unbox` holds the accepted raw-array producer, final fixture, rebuilt matched stages and correctness/resource evidence. `jit-array-read-unbox-review` preserves the independent runtime snapshot. The final review covers its identical optimizer patch after rebase and executes both final fixtures on both variants. Root `.work/tmp/jit-array-read-unbox-{source-review,independent-review,rebase-proof}.json` retain the proof. Original pre-prerequisite stages, reports, configured trees and logs are preserved under `.work/tmp/jit-array-read-unbox-attempt1/`; their moved binaries require the matching `DYLD_LIBRARY_PATH` if inspected. The original numeric allocation observer remains alongside the isolated-read prerequisite because it checks a four-read arithmetic chain. `jit-array-read-unbox-frozen-counts.json` records actual emitted occurrences from every resource trial.
Retained `.work/wt/jit-guarded-local-cleanup` holds the accepted observation hints, final fixtures, clean matched stages and correctness/resource evidence. `jit-guarded-local-cleanup-review` holds the source snapshot; its generated-code review covers the unchanged runtime and fixture addenda through `9abde336`. The first final native run started before lease admission and is excluded in root `jit-guarded-local-cleanup-final-review-unleased.json`. `jit-guarded-local-cleanup-native-review` independently repeats all six native commands under the verified live TEST lease. Root `jit-guarded-local-cleanup-{source-review,independent-review,review-lease-audit}.json` retain the accepted proof and correction. Parent frozen nbody traces also run through a test lease. Early fixture failures are historical: reflected local writes invalidate executors, and two distinct loop backedges cannot exercise the intended fresh-frame entry. The final fixture preserves activity and exact shape assertions after correcting both capture conditions.
Retained `.work/wt/jit-tracemalloc` holds the accepted observer fix, stage and evidence; `.work/wt/jit-tracemalloc-review` holds its source-review snapshot. Retained `.work/wt/jit-invert` holds accepted inversion sources, rebuilt stage and final evidence. `jit-invert-review` preserves the independently reviewed inversion snapshot. Root `.work/tmp/jit-invert-before-observer-fix.json` retains the superseded blocked evidence.
Retained `.work/wt/jit-parameters` holds accepted numeric parameter stores, final stage and evidence. `jit-parameters-review` holds the initial source-review snapshot; its final review also covers the candidate working diff and native stage. The bootstrap-created local object cache is preserved at `.work/tmp/jit-parameters-bootstrap-objects` before linking the primary cache.
Retained `.work/wt/jit-parameter-fusion` holds the accepted typed store fusion, clean stage and evidence. `jit-parameter-fusion-review` holds its independently reviewed source snapshot. Its bootstrap object cache is preserved at `.work/tmp/jit-parameter-fusion-bootstrap-objects`.
Retained `.work/wt/jit-parameter-guards` holds accepted entry guards, rebuilt stage and final evidence. `jit-parameter-guards-review` holds the source-review snapshot; the final addendum also covers the stronger subclass-finalizer fixture. Its bootstrap object cache is preserved at `.work/tmp/jit-parameter-guards-bootstrap-objects`.
Retained `.work/wt/jit-multi-guards` holds accepted dual entry guards, matched JIT/Serc and no-Serc stages, and final correctness/resource evidence. `jit-multi-guards-review` holds the source-review snapshot. Root `.work/tmp/jit-multi-guards-serc-independent-review.json` records generated/native checks and the parent resolution of the reviewer's unnecessary separate epoch-field request; both reports identify e891 by the same pinned source SHA.
Retained `.work/wt/jit-argument-attrs` holds accepted borrowed owner proofs, final fixtures, clean matched JIT/Serc and no-Serc stages, and correctness/resource evidence. `jit-argument-attrs-review` holds the first source-review snapshot. The final review covers the additional Rust call-shape proof, C cache decode and generated/native paths. Root `.work/tmp/jit-argument-attrs-independent-review.json` preserves that approval; the acceptance record corrects its stale prose about indirect shape checks because the final fixture directly requires the inference bit and minimal extent.
Retained `.work/wt/jit-typed-local-stores` holds accepted typed cleanup fusion, final fixtures, clean matched JIT/Serc and no-Serc stages, and correctness/resource evidence. `jit-typed-local-stores-review` holds the runtime review snapshot. Root `.work/tmp/jit-typed-local-stores-independent-review.json` includes approval of the mixed-local assertion update. Pre-existing ignored Cargo output from the primary `rust-cpython/overlay/target` is preserved at `.work/tmp/preexisting-main-overlay-target`; post-integration raw overlay and allocation-observer checks pass after that scratch relocation.
Retained `.work/wt/jit-nested-identity` holds accepted inside-out planning, final fixtures, clean matched JIT/Serc and no-Serc stages, and correctness/resource evidence. `jit-nested-identity-review` holds the runtime review snapshot. Root `.work/tmp/jit-nested-identity-independent-review.json` approves the final fixture and correct no-Serc control invocation. The first review receipt and partial implementation reports remain historical in root scratch; the acceptance record names final proof.
Retained `.work/wt/jit-exit-stub-reuse` holds accepted Rust resume recipes, clean matched JIT/Serc and no-Serc stages, and correctness/resource evidence. `jit-exit-stub-reuse-review` holds the independent review snapshot. Root `.work/tmp/jit-exit-stub-reuse-independent-review.json` approves final source, generated code, native sharing and the no-Serc control. The initial sandbox scratch failure remains historical. Parent AddressSanitizer proof is in `jit-exit-stub-reuse-asan.log`; offline Miri setup was unavailable because `addr2line` was not cached.
Retained `.work/wt/jit-virtual-body-unbox` holds accepted body descriptors, continuation glue, final fixtures, clean matched JIT/Serc and no-Serc stages, and correctness/resource evidence. `jit-virtual-body-unbox-review` holds the first source snapshot. Its final review covers the getter classification and fixture addenda through `bdecd1e4`. Root `.work/tmp/jit-virtual-body-unbox-independent-review.json` records approval. The initial source proof gap and boxed-only native attempt are historical in `jit-virtual-body-unbox-source-review-initial.json` and `jit-virtual-body-unbox-attempt1.json`. The generator's 99 tests ran on corrected candidate Tools with the verified predecessor interpreter; the helper asserts that analyzer path and matching source bytes.
Retained `.work/wt/jit-bool-state-updates` holds the accepted 188-byte leaf map, Boolean descriptor fact, guarded store, final fixture, clean matched JIT/Serc and JIT-only stages, and correctness/resource evidence. `jit-bool-state-updates-review` preserves the independently reviewed `530ffdb6` source. Root `.work/tmp/jit-bool-state-updates-independent-review.json` approves both native variants and frozen Richards calls. `jit-bool-state-updates-before.log` records predecessor JIT, interpreter and no-optimization controls; Rust/ASan and generated-flag proof remain separate in root scratch. Slot support remains an ordinary-call control.
Retained `.work/wt/jit-bool-predicates` holds the accepted 180-byte leaf map, final fixture, clean matched JIT/Serc and JIT-only stages, and correctness/resource evidence. `jit-bool-predicates-review` holds its independent source snapshot; final review also covers fixture addenda through `93d2bd64`. Root `.work/tmp/jit-bool-predicates-independent-review.json` records approval of both native variants; source review and writer receipts remain separate. Baseline and interpreter semantic controls pass in `jit-bool-predicates-before.log`.
Retained `.work/wt/jit-masked-exit-stubs` holds accepted complete resume recipes, the 16-byte ABI, final fixtures, clean matched JIT/Serc and no-Serc stages, and correctness/resource evidence. `jit-masked-exit-stubs-review` holds its independent snapshot. Root `.work/tmp/jit-masked-exit-stubs-independent-review.json` records final approval; source review and writer receipts remain separate in root scratch. The final fixture requires two masked regions and both left/right managed or slot fallback behavior.
Retained `.work/wt/jit-unary` holds accepted negation sources, stage and evidence; `.work/wt/jit-unary-review` holds the independently reviewed source snapshot. The final regression isolates caller code objects per callee/type so it cannot mistake an older cached executor for the tested path.
Retained `.work/wt/jit-float-int` holds the accepted mixed-add sources, fresh stage and evidence. Its setup required preserving the bootstrap-created local objects directory at `.work/tmp/jit-float-int-bootstrap-objects` before linking the primary cache. No harness change was made.
Protected `.work/wt/` trees: `c2`, `c3`, `j3`, `jit-loop`, `r0` retain compiler/epoch/per-step gate stages; `jit-integrate` retains frozen `4b66ce3` oracle; `jit-main-ref` retains matched C3/pins; `jit-pair` retains the pre-refresh candidate/evidence; `c0`, `r1`, `t2-unbox` retain parked report/source and their evidence.
Cleanup complete: retired `.work/wt/r1b` and `.work/wt/t3-measure`; verified archives and report at `.work/campaign-archive/winddown-20261112/cleanup-report.md`. Protected stage identities and interpreter imports pass; root scratch remains. `campaign/r1b` is retained after the helper’s upstream branch-deletion refusal; no force used.
Owner routing/API reviews remain asynchronous in **Owner decisions**; acceptance details are in the feature sections and **Ledger**. Short-lived implementation and review workers serve each slice. A sandbox lease-file restriction is handled by having the parent hold the harness test lease during independent runtime review.

## CinderX catalog

CinderX `e48f370dfcc045db32ac19142419043342b10cfb` (MIT) on stock CPython
3.15.1, macOS arm64. Every number is a within-version 3.15 uplift (CinderX JIT
over `-X cinderx-jit-disable`) and is never compared with the lane's 3.16
numbers.

**Steady-state score.** Profile-guided steady mode is `cinderx.jit.auto()` with
`-X cinderx-jit-background-compile=0 -X cinderx-jit-recompile-threshold=2`: hot
functions compile only after the interpreter has specialized their bytecode, so
the JIT's inline-cache profile can drive a recompile. The harness reuses cached
code objects, warms up until compiled-function count, summed code size, and
compilation time stop changing, then times 10 further passes. Five interleaved
runs over all 24 kernels, 24/24 digests matching the harness, `perf-gate-ready`
exit 0 before and after, no retried attempts:

- **`S` = 0.7960** over the 16 training kernels; held-out **1.0621**.
- Per kernel the JIT compiled 7-58 functions summing to 80-198 KB of code,
  compilation took 0.19-0.74 s, warmup 0.09-1.36 s, and peak RSS was 76-132 MB.
  Timed kernel CPU excludes warmup and compilation.
- Context, with compilation inside the timed region and no profile: default
  `auto()` is 1.031 training / 1.026 held-out, and compile-all
  (`-X cinderx-jit-all -X cinderx-jit-background-compile=0`) is 1.188 / 1.119.
  Compile-everything runs before the specializing adaptive interpreter has
  produced a profile, so it takes generic paths and is not a stand-in for
  steady state.

**Ablation.** One `-X` switch per variant, appended to the steady flags,
5 interleaved runs over the 16 training kernels; 16/16 digests match across all
18 modes, gates exit 0 before and after, no retries. Contribution is
`disabled / steady - 1`: positive means removing the mechanism slowed the run.
Two identical steady runs moved individual kernel medians by up to ~6% and the
training geomean by 0.04%, so treat `|Δ|` below ~2% as unresolved.

| # | Mechanism | Switch | Δ | Source | Semantics it must preserve | Tier |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | Specialized-opcode consumption | `specialized-opcodes=0` | **+21.0%** | `Jit/hir/builder.cpp:1467`, `:2196`, `:2818`; `Jit/config.h:229` | use a specialized form only where the interpreter's cache guarantees its types, else emit the generic form; `FOR_ITER_RANGE` | T2 |
| 2 | Attribute inline caches | `attr-caches=0` | **+18.9%** | `Jit/lir/generator.cpp:3677`; `Jit/inline_cache.h:505`, `:664`; `Jit/inline_cache.cpp:35`, `:1212` | descriptors, class and instance dicts, inheritance, `__getattr__`/`__getattribute__`, immutable types left unwatched, `PyType_Modified` invalidation | T3 |
| 3 | HIR simplify and type specialization | `simplify=0` | **+16.9%** | `Jit/hir/simplify.cpp:514`, `:719`, `:1302`, `:1515`; `Jit/compiler.cpp:152` | exact-float and compact-int forms, overflow/NaN/zero-division guards, guards implied by dataflow | T2 |
| 4 | Global and import caches | `global-caches=0` | **+6.0%** | `Jit/global_cache.cpp:110`, `:300`; `Jit/hir/preload.cpp:639` | globals and builtins rebinding, dict update/clear/unwatch, lazy imports, subinterpreters | T3 |
| 5 | Sink primitive box | `sink-primitive-box=0` | +2.3% | `Jit/hir/sink_primitive_box.cpp:102` | delay boxing without moving allocation visibility or reference counts | T2 |
| 6 | Guard-type removal | `guard-type-removal=0` | +2.2% | `Jit/hir/guard_removal.cpp:86` | remove only guards implied by dataflow; keep mutation-sensitive guards | T1 |
| 7 | BeginInlinedFunction elimination | `begin-inlined-function-elim=0` | +1.8% | `Jit/hir/inliner.cpp:1149` | remove bookkeeping only; keep deopt metadata for inlined callers | T3 |
| 8 | HIR inliner | `enable-hir-inliner=0` | +1.0% | `Jit/hir/inliner.cpp:948`, `:956`, `:964`; `Jit/config.h:60` | traceback and frame materialization, lazy inlined frames, generator refusal, cost/depth/cold-call limits | T3 |
| 9 | Dead code elimination | `dead-code-elim=0` | +0.7% | `Jit/hir/dead_code_elimination.cpp:19` | side-effect model; keep reference-count and deopt-relevant instructions | T3 |
| 10 | Builtin LoadMethod elimination | `builtin-load-method-elim=0` | +0.4% | `Jit/hir/load_method_elimination.cpp:316` | descriptor semantics, bound-method identity, allocation visibility | T3 |
| 11 | Common-subexpression elimination | `common-subexpression-elim=0` | +0.2% | `Jit/hir/common_subexpression_elimination.cpp:243`; `Jit/compiler.cpp:177` | never CSE across borrows, mutation, or refcount insertion | T3 |
| 12 | Clean CFG | `clean-cfg=0` | +0.0% | `Jit/hir/clean_cfg.cpp:9` | exception edges and deopt targets | T4 |
| 13 | TLS thread-state load | `tls-thread-state=0` | -0.1% | `Jit/codegen/tls.cpp:236`; `Jit/config.h:220` | correct tstate under threads and subinterpreters; verified offset or fallback | T1 |
| 14 | Phi elimination | `phi-elim=0` | -0.1% | `Jit/hir/phi_elimination.cpp:9` | parallel-copy and refcount ownership on edges | T4 |
| 15 | Dynamic comparison elimination | `dynamic-comparison-elim=0` | -0.7% | `Jit/hir/dynamic_comparison_elimination.cpp:50` | specialize only behind a guard; preserve rich-comparison semantics | T3 |
| 16 | Binary-op inline caches (default off) | `binary-caches=1` | -0.8% | `Jit/config.h:224`; `Jit/lir/generator.cpp:3884`; `Jit/inline_cache.h:1029` | operand and result typing, deopt on unsupported operands | T3 |

Row 16 is the one enable variant, so its table entry is headroom (`1 - enabled /
steady`): enabling the caches costs 0.8%, i.e. no headroom on these kernels.

Mechanisms without an isolating switch, read from source only: float/int
unboxing (`Jit/hir/simplify.cpp:514`, `:719`, `:1302`, `:1515`; measured only
through row 3), refcount insertion and elision
(`Jit/hir/refcount_insertion.cpp:1270`), type and `__code__` watcher
invalidation (`Jit/context.cpp:582` type, `:664` function;
`Jit/inline_cache.cpp:1212`), type feedback and recompilation
(`Jit/profile.h:228`, `Jit/profile.cpp:149`, stale-profile drop
`Jit/context.cpp:465`), deopt metadata and frame materialization
(`Jit/deopt.h:306`, `:323`, `:343`; `Jit/codegen/gen_asm.cpp:187`), LIR
rewrite/post-allocation/DCE (`Jit/lir/rewrite.cpp:32`, `Jit/lir/dce.cpp:39`),
native code emission (`Jit/codegen/gen_asm.cpp`, `Jit/codegen/arch/aarch64.cpp`),
and linear-scan register allocation (`Jit/lir/linear_scan.cpp:369`; deopt
spill rule `:662`). The alternate allocator was not measured: the earlier
attempt aborted, and it was not rerun. Their tiers are T2 (unboxing), T3
(refcount, watchers, profile), and T4 (LIR, codegen, registers).

**Version assumptions.** CinderX declares `>=3.14.5,<3.16`; this build targets
3.15.1 and is the GIL build. `Jit/hir/builder.cpp` selects opcodes by
`PY_VERSION_HEX`, `Common/opcode_stubs.h` makes other versions' opcodes
unreachable, and the caches assume 3.15's type watcher and adaptive bytecode.
Lightweight frames, parallel GC, and Static Python are off on 3.15, so deopt and
frame observations are for stock frames; free-threaded-only passes
(`CallSiteLiveValues`, `MaterializeSteals`) are compiled out and refcount
elision assumes the GIL. A 3.16 port re-derives the builder, frame, and watcher
contracts: these mechanisms are design references, not transplantable code.

**Reading for the lane.** The three dominant mechanisms are the numeric path
(specialized opcodes and simplify, together 38%) and attribute caches (19%).
Global caches add 6%; the inliner adds 1%; TLS and the backend passes are at or
below the resolution. That ranks T2 unboxing and T3 attribute caches ahead of
inlining and backend work on this reference, but CinderX's own steady mode is
slower than its interpreter on several kernels (`generators` 2.55x, `richards`
1.40x, `richards_super` 1.31x, `comprehensions` 1.21x, `raytrace` 1.18x), so a
JIT reference does not imply every kernel wins. Raw records:
`.work/r1/steady-24-20261110T045247Z.json`,
`.work/r1/steady-ablation-20261110T045945Z.json`, and
`.work/r1/timed-24-20261110T053554Z.json`.

## Ledger

Track C and R1 rows use the same columns, with the gate's headline ratio in
`S`.

| Date | Epoch | Lane | Step | Mechanism | Ref | `S` before → after | Moved >1% | Verdict | Directory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-02 | a1 | J | M0 | instrument | `perf-rust-jit` | — → 0.7474 | A/A max 0.19% | MEASURED | `rust-cpython/results/perf-bench/20261102T174650Z-perf-climb-ref-vs-perf-rust-jit` |
| 2026-11-10 | e891 | J | M0 | instrument | `perf-rust-jit` | — → 0.7748 | A/A max 1.19% | MEASURED | `rust-cpython/results/perf-bench/20261110T041724Z-perf-climb-ref-vs-perf-rust-jit` |
| 2026-11-11 | e891 | C | C2 | in-process source-mode `exec`, nine re-review defects fixed; finally-exit and small-stack routing | `perf-rust` | timing deferred (OD14); prior memory bar met (OD15) | — | CORRECT on `campaign/c2`: both full suites match baseline five-file failure set; no verify difference; census 0 mismatches; independent close-out review finds no new defect | `8930b8a`; C2 worktree `rust-cpython/logs/perf-rust-serc-v3-all{,-serc-verify}.log`, `.work/tmp/c2-close-parity.json` |
| 2026-11-11 | e891 | C | C3 | source-mode `eval` and `single`, exact mode-aware CLI/census, compile audits and interactive parity | `perf-rust` | performance deferred (OD16) | — | CORRECT on `campaign/c3`: both full suites match baseline five-file failures; no verify difference; 30,318 census cases with 0 mismatches; independent reviews approve | `fb131e1`; C3 exit evidence above |
| 2026-11-11 | e891 | J | T3 | fuse complete exact-argument calls returning an argument or constant without a physical callee frame | clean `perf-jit-c3-ref` on `d52991a` | performance deferred (OD16) | — | CORRECT: 31 workload digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; targeted suites plus `test_capi.test_eval` differ only by 8 named superseded uop assertions; 30 Rust tests, 7 leaf traces, 10 unboxed kernels; independent review approves | `rust-cpython/results/perf-bench/20261112T041852Z-perf-jit-c3-ref-vs-perf-jit-j3`, `20261112T042029Z-perf-jit-c3-ref-vs-perf-jit-j3`; `.work/tmp/j3-feature-acceptance.json` |
| 2026-11-11 | e891 | J | T4 | whole-trace Rust cache-depth planning minimizes explicit spill/reload stencils, then stack-reference transfers; preserves greedy endpoint and forbids speculative prefetch | `perf-jit-j3` | performance deferred (OD16) | call fixture 13 → 7 stencils, float fixture 3 → 2; instruction counts only | CORRECT: 31 workload digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 8 superseded suite assertions; 32 Rust tests including 15,625 exhaustive traces; native cache, leaf and unboxing checks pass; independent source/native review approves, including disabled optimization | `rust-cpython/results/perf-bench/20261112T043205Z-perf-jit-j3-vs-perf-jit-j4`, `20261112T043347Z-perf-jit-j3-vs-perf-jit-j4`; `.work/tmp/j4-feature-acceptance.json` |
| 2026-11-11 | e891 | J | T3 | extend frame-free calls to preguarded borrowed-argument slot getters; missing slots retry the original call before consuming inputs | `perf-jit-j4` | performance deferred (OD16) | — | CORRECT: 31 workload digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 8 superseded assertions; descriptor suite passes JIT on/off; 33 Rust tests, 5 getter traces plus prior integration checks; independent source/native review approves, including an unrelated owner with a different slot layout | `rust-cpython/results/perf-bench/20261112T044643Z-perf-jit-j4-vs-perf-jit-j5`, `20261112T044811Z-perf-jit-j4-vs-perf-jit-j5`; `.work/tmp/j5-feature-acceptance.json`, `.work/tmp/j5-descr-{on,off}.log` |
| 2026-11-11 | e891 | J | T3 | fuse global-return getters while preserving a pre-call guard against the callee's dictionary and existing watcher dependencies | `perf-jit-j5` | performance deferred (OD16) | — | CORRECT: 31 workload digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 8 superseded assertions; lazy-import suite passes both modes; 34 Rust tests and three native global-getter traces plus prior integrations; independent source/native review approves, including reentrant lookup during old-value finalization | `rust-cpython/results/perf-bench/20261112T045911Z-perf-jit-j5-vs-perf-jit-j6`, `20261112T050048Z-perf-jit-j5-vs-perf-jit-j6`; `.work/tmp/j6-feature-acceptance.json` |
| 2026-11-11 | e891 | C/J | integration | combine C3 and four JIT slices with main's dependency update; repair three SSL dependency API mismatches while preserving behavior | `perf-jit-main-ref` (`8083f4f`) | performance deferred (OD16) | — | CORRECT: matched 31 digests; 2,000 clean fuzz programs; targeted JIT suites only 8 prior superseded assertions; normal reference/candidate and candidate verify each run 52,102 tests with the same five known failing files; no compiler mismatch; native compiler and SSL regressions pass; independent adapter review approves | local main `0e72f84`; integration evidence above |
| 2026-11-11 | e891 | J | T3 | peel one watched globals guard into a side-exit entry prologue; preserve callback validity checks and derive the zero-depth backedge from warm marking | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; 9 named shape exclusions; 37 Rust tests; all native integrations pass; independent review approves progress for foreign and mapping globals, exact effects, invalidation and original builtins assertions | `rust-cpython/results/perf-bench/20261112T065626Z-perf-jit-integrated-vs-perf-jit-loop`, `20261112T065803Z-perf-jit-integrated-vs-perf-jit-loop`; `.work/tmp/loop-feature-acceptance.json` |
| 2026-11-11 | e891 | J | T3 | remove non-escaping two-argument tuple returns and immediate unpack while retaining finalizer-visible tuple fallback | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 9 shape exclusions; 39 Rust tests; three pair traces plus all native integrations pass; independent source/native review approves | `rust-cpython/results/perf-bench/20261112T074531Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T074700Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/pair-feature-acceptance.json` |
| 2026-11-12 | e891 | J | T4 | fuse adjacent local swap and generic decref into one stencil while preserving callback-visible state | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; 11 named shape exclusions; 42 Rust tests; four native store traces and all prior integrations pass; independent source/native/policy review approves | `rust-cpython/results/perf-bench/20261112T075752Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T075931Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/glue-feature-acceptance.json` |
| 2026-11-12 | e891 | J | T3 | remove immediately unpacked two-argument list returns, retaining container-kind matching and finalizer-visible fallback | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 11 shape exclusions; 44 Rust tests; six tuple/list traces and all prior native integrations pass; independent source/native review approves | `rust-cpython/results/perf-bench/20261112T081044Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T081214Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/list-feature-acceptance.json` |
| 2026-11-12 | e891 | J | T3 | fuse exact-float arithmetic calls; materialize original callee locals, operands and PC only on error | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 11 shape exclusions; 46 Rust tests; eight operation/order kernels, four active-JIT faults and two success callbacks; prior integrations pass; independent source/native review approves | `rust-cpython/results/perf-bench/20261112T084734Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T084903Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/arith-feature-acceptance.json` |
| 2026-11-10 | 3.15.1 | R1 | R1 | CinderX steady JIT vs off | `cinderx-e48f370d` | — → 0.7960 (held-out 1.0621) | — | MEASURED | `.work/r1` |
| 2026-11-10 | e891 | J | T1 | thread-state: executor tstate to float/int freelists (`_tlv_get_addr` 4.61% of samples, targeted chains 1.36% → 0%) | `perf-rust-jit` | timing deferred (OD14) | — | CORRECT | `campaign/t1-tstate` |
| 2026-11-10 | e891 | J | T2 | float unboxing across binary ops and compares (float boxing 8.9-38.6% of samples; float-op chains nbody 27.5% → 26.3%, fft 11.4% → 9.5%) | `perf-rust-jit` | timing deferred (OD14); advisory 0.9962 | — | CORRECT (4 named superseded `test_capi.test_opt` uop assertions, owner ruling) | `aadaf25` |
| 2026-11-10 | e891 | J | T2 | compact-int unboxing across binary ops and compares (int uops 29.6% of samples on spectral_norm, 8.2% mean; spectral_norm 29.6% → 27.0%, int runtime calls 17.0% → 12.0%) | `perf-jit-unbox` | timing deferred (OD14); advisory 0.9939 | — | CORRECT (5 named superseded `test_capi.test_opt` uop assertions); independent review clean | `60e3a7b` |
| 2026-11-10 | e891 | J | T2 | unboxed loop-carried locals (float-local stores 3.1% of numeric samples, int 0.7%; 0.0% with no barrier between store and next store) | `perf-jit-int-unbox` | — | — | DROPPED (under P1's 1% floor) | `rust-cpython/results/perf-profile/20261110T14*-perf-jit-int-unbox-*-sample` |
| 2026-11-10 | e891 | J | T1 | frame push and pop: 13.0% sampled share; clear-loop slots 96.4-99.8% live on return-heavy kernels; prologue/epilogue plus NULL-slot scan 1.3% removable | `perf-jit-int-unbox` | — | — | MEASURED (above P1's 1% floor; ranked first in T1) | `rust-cpython/results/perf-profile/20261110T14*-perf-jit-int-unbox-*-sample` |
| 2026-11-10 | e891 | J | T1 | type and version guards: 8.5% sampled share; 4.6% first checks, 3.8% on values with no trace-local fact, 0.01% provably removable (go `_CHECK_MANAGED_OBJECT_HAS_VALUES` and same-frame globals versions); code-version repeats 0.06% are not provable | `perf-jit-int-unbox` | — | — | DROPPED (under P1's 1% floor) | `rust-cpython/results/perf-profile/20261110T14*-perf-jit-int-unbox-*-sample` |
| 2026-11-10 | e891 | J | T1 | `_CHECK_VALIDITY` and `_SET_IP`: 3.47% sampled share (2.90% + 0.57%); 0 of 227 sampled `_SET_IP` and 0 of 835 sampled `_CHECK_VALIDITY` removable, every one has a following escaping/needs-IP observer or a preceding escape; the removal pass runs before T2 unboxing, which leaves one unsampled stale pair | `perf-jit-int-unbox` | — | — | DROPPED (under P1's 1% floor) | `rust-cpython/results/perf-profile/20261110T14*-perf-jit-int-unbox-*-sample` |
| 2026-11-10 | e891 | J | T1 | frame pop fast-path inlining: `_PyEval_FrameClearAndPop` + `_PyThreadState_PopFrame` self richards 7.85% → 0.00%, raytrace 5.24% → 0.00%, deltablue 5.48% → 0.00%, sor 4.89% → 0.00%; `_RETURN_VALUE_r11` stencil 60 → 192 bytes | `perf-jit-tlv` | timing deferred (OD14) | — | CORRECT (clean build; independent review clean) | `campaign/t1-popinline` |
| 2026-11-10 | e891 | J | T1 | generic thread-state deallocation: `_tlv_get_addr` sampled share fft 3.68%/3.70% → 2.49%/2.84%, fannkuch 6.80% → 6.34%/5.31% (one incumbent repeat 18.52% sampling outlier), raytrace 2.70% → 2.16%, nqueens 5.25% → 5.26%, richards 0.42% → 0.63%; fft `float_dealloc` 3.97%/3.49% → 2.13%/2.03%, `long_dealloc` 1.16%/0.93% → 0.50%/0.51%, `PyLong_FromLong` 0.71%/1.15% → 0.08%/0.08%; the remaining `PyFloat_FromDouble` TLV enters through `array_subscr` | `perf-jit-int-unbox` | timing deferred (OD14) | — | CORRECT | `rust-cpython/results/perf-profile/20261110T1607*-perf-jit-{int-unbox,tlv}-{nqueens,fannkuch,scimark_fft,richards,raytrace}-sample`, repeats `20261110T1609*-...-{fannkuch,scimark_fft}-sample` |
| 2026-11-10 | e891 | J | T2 | `array.array` `d`/`i` subscript loads: generic `_BINARY_OP` with callees plus the `array_subscr` subtree sparse 54.2% + 8.2% → 0.0%, fft 25.2% + 2.6% → 4.8%, lu 18.1% + 2.4% → 0.0%, replaced by `_BINARY_OP_SUBSCR_ARRAY_D/I` at 29.2%, 9.2% and 9.1%; JIT code bytes ≤1.30x M0, peak RSS 0.997-1.008x incumbent | `perf-jit-popinline` | timing deferred (OD14); advisory 0.9277 | sparse generic `_BINARY_OP` 54.2% → 0.0%, fft 25.2% → 4.8%, lu 18.1% → 0.0% of samples | CORRECT (5 incumbent superseded `test_capi.test_opt` uop assertions; differential array test) | `rust-cpython/results/perf-bench/20261110T184902Z-perf-jit-popinline-vs-perf-jit-array`, `rust-cpython/results/perf-profile/20261110T1851*-perf-jit-{popinline,array}-scimark_{sparse_mat_mult,fft,lu}-sample` |
| 2026-11-10 | e891 | J | T2 | generic `_BINARY_OP` specialization: monte_carlo `float += 2**31-1` 9.6% → 0.0% (replaced by `_BINARY_OP_ADD_FLOAT_INT` 8.8%), nbody `float ** float` 11.6% → 0.0% (replaced by `_BINARY_OP_POWER_FLOAT` 6.4%); JIT code bytes ≤1.303x M0, peak RSS 0.989-0.995x incumbent | `perf-jit-array` | timing deferred (OD14); advisory 0.9992 | monte_carlo generic `_BINARY_OP` 9.6% → 0.0%, nbody 11.6% → 0.0% of samples | CORRECT (5 incumbent superseded `test_capi.test_opt` uop assertions; genops integration test) | `rust-cpython/results/perf-bench/20261110T204736Z-perf-jit-array-vs-perf-jit-genops`, `rust-cpython/results/perf-profile/20261110T2021*-perf-jit-array-{scimark_monte_carlo,nbody}-sample`, `20261110T2046*-perf-jit-genops-{scimark_monte_carlo,nbody}-sample`, `20261110T205313Z-perf-jit-array-vs-perf-jit-genops` |
| 2026-11-10 | e891 | J | T3 | Python-to-Python inline frames: eligible exact-args leaf call segments build the callee frame in datastack spare without linking it; side exits, deopts and errors materialize it before resuming; entry resume and stack-space checks fold into the call; generators, nested calls, escaping/error/periodic callees stay concrete | `perf-jit-genops` | timing deferred (OD14); advisory 1.0123 | incumbent-named bucket (a) richards 24.0% → 17.2%, raytrace 11.6% → 9.6%; including `_INLINE_*` call/frame uops 23.9% → 24.6% and 11.5% → 11.7% over two runs, so no fall; JIT code bytes ≤1.071x incumbent (sor), raytrace 457,404 (M0 457,068 at 1.30x) | DROPPED (correct; concrete-frame argument setup and refcount clear remain, under P1's 1% floor; 5 incumbent superseded `test_capi.test_opt` assertions; `jit_inline_integration.py` 18 absolute checks; lane 245/246, one pre-existing incumbent failure) | `rust-cpython/results/perf-bench/20261111T012030Z-perf-jit-genops-vs-perf-jit-inline`, `rust-cpython/results/perf-profile/20261110T2337*-perf-jit-{genops,inline}-{richards,raytrace}-sample`, repeat `20261111T0119*-...` |
| 2026-11-12 | e891 | J | correctness | match specialized float division error text to the pinned interpreter | JIT disabled in the same stage | no timing | — | CORRECT: native regression proves active JIT at the fault and identical ZeroDivisionError text, traceback function, bytecode and line; independent review approves | `rust-cpython/tests/jit_float_division_integration.py`; tested alongside the pending loop-type slice in the incremental `perf-jit-pair` stage |
| 2026-11-12 | e891 | J | T3 | prove callback-free numeric loop-local invariants under exact entry guards; retain integer range checks and terminal pending exits | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 11 shape exclusions; 50 Rust tests; numeric/mutation/refusal/capacity native coverage plus prior integrations; independent source/native review approves | `rust-cpython/results/perf-bench/20261112T101737Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T101904Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/loop-types-feature-acceptance.json`; incremental stage based on `3fafd75` with this slice and the separate division fix |
| 2026-11-12 | e891 | J | T3 | fuse borrowed-argument inline instance getters while retaining mutable storage and missing-value refusals | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; 12 reviewed shape exclusions; 51 Rust tests; slot/managed regressions in nine JIT/optimizer modes plus prior integrations; independent source/native/policy review approves | `rust-cpython/results/perf-bench/20261112T103312Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T103439Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/managed-getter-feature-acceptance.json`; incremental stage based on `ff3f2b0` with this slice |
| 2026-11-12 | e891 | J | correctness | match both shared in-place float division error strings to the interpreter | JIT disabled in the same stage | no timing | — | CORRECT: scalar, left-reuse and right-reuse kernels each prove the expected opcode and active-JIT fault, with identical interpreter error text and traceback location; independent source/provenance/native review approves | `rust-cpython/tests/jit_float_division_integration.py`; new `ceval_macros.h` overlay is the exact pinned header with only that string changed; tested alongside the pending virtual-call slice in incremental `perf-jit-pair` |
| 2026-11-12 | e891 | J | T3 | retain longer float-body arithmetic on a reserved virtual operand stack; initialize/link the full callee only on error | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 12 reviewed shape exclusions; 53 Rust tests; four kernels, seven active-JIT fault/finalizer assertions, wide stack and observers; all prior integrations; independent source/generated/native review approves | `rust-cpython/results/perf-bench/20261112T110914Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T111043Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/virtual-feature-acceptance.json`; incremental stage based on `1b3c1fc` with this slice and separate in-place division fix |
| 2026-11-12 | e891 | J | T3 | preserve heap-safe virtual float locals, shared aliases and NULL slots without copying arguments on success | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 12 reviewed shape exclusions; 55 Rust tests; six active-JIT local/error/finalizer cases including heap-frame generator caller; all prior integrations; independent source/generated/native review approves | `rust-cpython/results/perf-bench/20261112T113401Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T113528Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/virtual-locals-feature-acceptance.json`, `.work/tmp/god-virtual-locals-generator.log`; incremental stage based on `777cdd6` with this slice |
| 2026-11-12 | e891 | J | correctness | recognize private error materializers in the debug executor-tail validator | unchanged validator before fix | no timing | — | CORRECT: actual assertion-enabled C checker rejects both omitted materializer classes before the fix and accepts both afterward; an ordinary load remains rejected; independent source/native checker review approves | `rust-cpython/tests/jit_tail_validation.py`; `.work/tmp/tail-validation-before.log`, `.work/tmp/tail-validation.log`; checked alongside pending terminal-stub rename; no full `Py_DEBUG` interpreter build claimed |
| 2026-11-12 | e891 | J | T3 | materialize virtual calls on power side exits, preserving cached operands and original callee resumption PC | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 12 reviewed shape exclusions; 56 Rust tests; 29 active-JIT complex/error/progress cases and prior integrations; assertion-enabled tail checker; independent source/generated/native review approves | `rust-cpython/results/perf-bench/20261112T122325Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T122452Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/virtual-exits-feature-acceptance.json`; incremental stage based on `2720c27` with this slice; debug tail policy checked directly |
| 2026-11-12 | e891 | J | T3 | virtualize bound float methods with guarded receiver attributes and original-PC storage/type materialization | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 12 reviewed shape exclusions; 57 Rust tests; 20 active-JIT method/mutation/error/finalizer cases and prior integrations; independent source/generated/native review approves | `rust-cpython/results/perf-bench/20261112T131439Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T131913Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/bound-floats-feature-acceptance.json`, `.work/tmp/bound-floats-native.log`; incremental stage based on `e8cf127` with this slice |
| 2026-11-12 | e891 | J | T4 | preserve independent outer unboxing around opaque boxed virtual-call regions and box live caller prefixes before entry | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 12 reviewed shape exclusions; 58 Rust tests; 11 active-JIT prefix/error/complex-exit cases and prior integrations; independent source/native review approves | `rust-cpython/results/perf-bench/20261112T132914Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T133046Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/regional-feature-acceptance.json`; incremental stage based on `df56e47` with this slice |
| 2026-11-12 | e891 | J | T3 | retain scalar constants and guard virtual-call arguments at their original arithmetic PCs, allowing unused generic arguments | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; same 12 reviewed shape exclusions; 59 Rust tests; 6 active-JIT constant/fallback/error/OOM/reverse-finalizer cases and prior integrations; independent source/generated/native review approves | `rust-cpython/results/perf-bench/20261112T134732Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T134902Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/constants-feature-acceptance.json`; incremental stage based on `e61add3` with this slice |
| 2026-11-12 | e891 | J | T3 | virtualize compact integer scalar calls with typed local cleanup, literal in-place ownership and original-PC helper refusal | committed `perf-jit-integrated` (`4b66ce3`) | performance deferred (OD16) | — | CORRECT: 31 digests equal; 2,000 fuzz programs, 97.45% JIT coverage, 0 differences; 16 reviewed shape exclusions; 62 Rust tests; 68 active-JIT integer/reuse/overflow/mixed-local/receiver/generator/allocation cases and prior integrations; independent source/generated/native review approves, rebuilt with operand-cleanup restriction | `rust-cpython/results/perf-bench/20261112T142521Z-perf-jit-integrated-vs-perf-jit-pair`, `20261112T142657Z-perf-jit-integrated-vs-perf-jit-pair`; `.work/tmp/integers-cleanup-feature-acceptance.json`; `.work/tmp/god-integers-original-assertions.log`; incremental stage based on `1751091` with this slice |

| 2026-11-12 | e891 | J | resumption | repository move and main dependency refresh; preserve protected worktrees and rebuild incumbent before new gates | main `bccb4436` | performance deferred (OD16) | — | Main and retained `jit-pair` at `f74aad6` clean; final historical acceptance JSON remains CORRECT; post-refresh fetch/debug/HTTP checks reported green by owner; fresh incumbent pending | `.work/wt/jit-pair/.work/tmp/integers-cleanup-feature-acceptance.json`; current Status |

| 2026-11-12 | e891 | J | T3 | virtualize existing float-plus-exact-int addition; retain compact-int refinement, ownership and conversion/OOM PCs | clean `perf-jit-refresh` from main `cfc8221` | performance deferred (OD16) | — | CORRECT: 63 Rust tests, 20 native integrations, same 16 reviewed shape exclusions, 2,000 fuzz programs (1,949 JIT-active, 0 differences), 31 equal digests. Independent source/generated/native review approves under OD17; post-rebase stage identity and focused regression pass, including strengthened one-shot OOM PC assertion | `.work/wt/jit-float-int/.work/tmp/float-int-feature-acceptance.json`; corpus runs `20261112T211605Z-perf-jit-refresh-vs-perf-jit-float-int`, `20261112T211740Z-perf-jit-refresh-vs-perf-jit-float-int` in that worktree; root `.work/tmp/jit-float-int-{rust-tests,native,rebase-check}.log` and `jit-float-int-independent-review.json` |
| 2026-11-12 | e891 | J | routing | OD17 temporarily disables unavailable Carbon and permits independent worker reviews through the bridge | existing correctness evidence | performance deferred (OD16) | — | Independent Codex worker reviewed the diff, owning source, generated metadata and native regression and approved integration; no review or correctness gate waived | owner instruction; `.work/tmp/jit-float-int-independent-review.json` |
| 2026-11-12 | e891 | J | T3 | virtualize proven exact numeric negation and retain fresh-float in-place transfer with NULL cleanup | accepted `perf-jit-float-int` at main `1622358` | performance deferred (OD16) | — | CORRECT: clean JIT/Serc build verifies 58 Rust extensions; 64 Rust tests; 21 native integrations, 35 active samples and 11 unary traces; same 16 reviewed shape exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves, including explicit variants and integer/float OOM PCs | `.work/wt/jit-unary/.work/tmp/unary-feature-acceptance.json`; corpus runs `20261112T215124Z-perf-jit-float-int-vs-perf-jit-unary`, `20261112T215357Z-perf-jit-float-int-vs-perf-jit-unary`; root `.work/tmp/jit-unary-rust-tests.log`, `jit-unary-native.log`, `jit-unary-independent-review.json` |
| 2026-11-12 | e891 | J | correctness | retain physical callee allocation frames for memory/reference observers; recognize the pure integer atomic load in the case generator | accepted `perf-jit-unary` at main `e993507` | performance deferred (OD16) | — | CORRECT: incumbent reproduces missing callee in both allocating entry paths; fixed regression passes JIT/on/off/noopt before/after warming with observer and progress parity; 22 native integrations; same 16 shape exclusions including added `test_tracemalloc` suite; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves. Incremental retry follows a root-caused generator failure; unchanged 64 Rust tests reuse accepted evidence | `.work/wt/jit-tracemalloc/.work/tmp/tracemalloc-feature-acceptance.json`; corpus `20261112T232608Z-perf-jit-unary-vs-perf-jit-tracemalloc`, `20261112T232756Z-perf-jit-unary-vs-perf-jit-tracemalloc`; root `.work/tmp/jit-tracemalloc-independent-review.json`, `jit-tracemalloc-regression-baseline.log`, `jit-tracemalloc-generator-{before,after}.log` |
| 2026-11-12 | e891 | J | T3 | virtualize proven integer inversion with an ExactInt result, preserving compact guards, ownership and original unary PCs | accepted `perf-jit-tracemalloc` at main `b4201dae` | performance deferred (OD16) | — | CORRECT: incremental rebuild verifies 58 Rust extensions; 65 Rust tests; 23 native integrations, 16 active samples and five virtual inversion traces; same 16 reviewed shape exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests. Original allocation-stack regression passes both modes after observer repair. Independent inversion approval is reused for byte-identical source after rebase, alongside the separately approved shared guard | `.work/wt/jit-invert/.work/tmp/invert-feature-acceptance.json`; corpus `20261112T234548Z-perf-jit-tracemalloc-vs-perf-jit-invert`, `20261112T234720Z-perf-jit-tracemalloc-vs-perf-jit-invert`; root `.work/tmp/jit-invert-{rust-tests,composition-check,source-proof}.log`, `jit-invert-independent-review.json` |
| 2026-11-12 | e891 | J | T3 | virtualize proven numeric parameter stores in held argument slots with live-anchor rejection and immediate typed cleanup | accepted `perf-jit-invert` at main `0b9f27e0` | performance deferred (OD16) | — | CORRECT: 66 Rust tests; 24 native integrations, 20 active samples and 18 virtual trace samples; same 16 reviewed shape exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent final-source/generated/native review approves. A root-caused no-op-cleanup refusal required an incremental rebuild after the clean build; final stage verifies 58 Rust extensions and matches the source overlay | `.work/wt/jit-parameters/.work/tmp/parameters-feature-acceptance.json`; corpus `20261113T001612Z-perf-jit-invert-vs-perf-jit-parameters`, `20261113T001754Z-perf-jit-invert-vs-perf-jit-parameters`; root `.work/tmp/jit-parameters-{rust-tests,native,source-proof}.log`, `jit-parameters-independent-review.json` |
| 2026-11-12 | e891 | J | T4 | fuse virtual parameter swaps and typed numeric cleanup while preserving ownership, bound indices and cleanup metadata | accepted `perf-jit-parameters` at main `b90b486f` | performance deferred (OD16) | eligible swap/cleanup pairs become one stencil; instruction count only | CORRECT: clean JIT/Serc build verifies 58 Rust extensions; 67 Rust tests; 24 native integrations, including exact fused kinds/counts across 18 virtual trace samples and 20 active samples; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves | `.work/wt/jit-parameter-fusion/.work/tmp/fusion-feature-acceptance.json`; corpus `20261113T003515Z-perf-jit-parameters-vs-perf-jit-parameter-fusion`, `20261113T003648Z-perf-jit-parameters-vs-perf-jit-parameter-fusion`; root `.work/tmp/jit-parameter-fusion-{rust-tests,native,source-proof}.log`, `jit-parameter-fusion-independent-review.json` |
| 2026-11-12 | e891 | J | T3 | guard one unproved old parameter as exact numeric at virtual entry; fuse its cleanup and honor bound-method setup | accepted `perf-jit-parameter-fusion` at main `de1c820e` | performance deferred (OD16) | — | CORRECT: 69 Rust tests; 25 native integrations, including repeated generic and numeric-subclass finalizers, bound indices, generator errors and one-shot OOM before/after replacement; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review and strengthened-fixture addendum approve. The bound planning regression required an incremental rebuild after the clean build; final stage verifies 58 Rust extensions and matches the source overlay | `.work/wt/jit-parameter-guards/.work/tmp/guards-feature-acceptance.json`; corpus `20261113T012850Z-perf-jit-parameter-fusion-vs-perf-jit-parameter-guards`, `20261113T013018Z-perf-jit-parameter-fusion-vs-perf-jit-parameter-guards`; root `.work/tmp/jit-parameter-guards-{rust-tests,native,subclass-check,source-proof}.log`, `jit-parameter-guards-{independent-review,subclass-independent-review}.json` |
| 2026-11-12 | e891 | J | T3 | guard two independently unproved old numeric parameters in the existing begin cache; preserve single high-index eligibility, bound shape and pre-CALL refusal | accepted `perf-jit-parameter-guards` at main `baa24464` | performance deferred (OD16) | five interleaved resource trials: matched RSS <=1.0007x incumbent, isolated Serc footprint <=1.0299x no-Serc, code <=1.3094x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 70 Rust tests; 25 native integrations; 32 new active call samples, three active OOM samples, 35 virtual traces; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native checks approve. Initial mismatched flags and combined-versus-frozen resource diagnostic are excluded from acceptance | `.work/wt/jit-multi-guards/.work/tmp/multi-guards-feature-acceptance.json`, `multi-guards-resources.json`; matched corpus `20261113T035901Z-perf-jit-parameter-guards-vs-perf-jit-multi-guards`, `20261113T040033Z-perf-jit-parameter-guards-vs-perf-jit-multi-guards`; root `.work/tmp/jit-multi-guards-{source-review,independent-review,serc-independent-review}.json`, `jit-resume-profile-summary.json` |
| 2026-11-12 | e891 | J | controller correctness | isolate concurrent cold JIT-tool extraction before atomic cache publication | old shared `.llvm-objdump.part` path | no timing | — | CORRECT: deterministic overlapping extractors fail before the fix and both publish complete executable tools afterward; all three JIT-tool controller regressions pass. Unique temporary directories own partial files and clean up on exit. No runtime overlay, source pin or compiler change | root `.work/tmp/jit-tool-cache-{before,check}.log`; `rust-cpython/tests/test_build.py::JitConfigurationTests.test_concurrent_jit_tool_extraction_publishes_complete_executables` |
| 2026-11-12 | e891 | J | T3 | virtualize guarded attribute reads from borrowed arguments and infer actual call shape only after equivalent Rust proofs | accepted `perf-jit-multi-guards` at main `fd32b98d` | performance deferred (OD16) | five interleaved resource trials: matched RSS <=1.0035x incumbent, isolated Serc footprint <=1.0334x no-Serc, code <=1.3094x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 74 Rust tests; 26 native integrations; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves. Initial native proof caught ambiguous fixed call shape; both proofs must now produce identical rewrites and guards before runtime inference | `.work/wt/jit-argument-attrs/.work/tmp/argument-attrs-feature-acceptance.json`, `argument-attrs-resources.json`; corpus `20261113T050548Z-perf-jit-multi-guards-vs-perf-jit-argument-attrs`, `20261113T050728Z-perf-jit-multi-guards-vs-perf-jit-argument-attrs`; root `.work/tmp/jit-argument-attrs-{source-review,independent-review}.json`, `jit-argument-attrs-final-{rust-tests,native,source-proof}.log` |
| 2026-11-12 | e891 | J | T4 | fuse ordinary local swaps with exact-float or exact-int cleanup while preserving metadata and alias ownership | accepted `perf-jit-argument-attrs` at main `b34da508` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0000x incumbent, isolated Serc footprint <=1.0287x no-Serc, code <=1.3094x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 75 Rust tests; 26 native integrations; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review and mixed-local assertion addendum approve. Original finalizer, simple-loop guards, alias results and observers remain checked | `.work/wt/jit-typed-local-stores/.work/tmp/typed-local-stores-feature-acceptance.json`, `typed-local-stores-resources.json`; corpus `20261113T053229Z-perf-jit-argument-attrs-vs-perf-jit-typed-local-stores`, `20261113T053400Z-perf-jit-argument-attrs-vs-perf-jit-typed-local-stores`; root `.work/tmp/jit-typed-local-stores-{independent-review,independent-review-initial}.json`, `jit-typed-local-stores-{rust-tests,native,source-proof}.log` |
| 2026-11-12 | e891 | J | T3 | compose guarded zero-argument identity methods with numeric virtual bodies using inside-out Rust planning | accepted `perf-jit-typed-local-stores` at main `50618514` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0102x incumbent, isolated Serc footprint <=1.0313x no-Serc, code <=1.7059x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 80 Rust tests; 27 native integrations; expanded sys/call/function-attribute/traceback suites retain the same 16 reviewed exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/final-native and no-Serc control review approve. Active recursion-boundary, exact error PCs/locals, callback fallback, ownership and observers pass. Private leaf map grows to 166 bytes; caches and exported descriptor widths stay fixed | `.work/wt/jit-nested-identity/.work/tmp/nested-identity-feature-acceptance.json`, `nested-identity-resources.json`; corpus `20261113T061734Z-perf-jit-typed-local-stores-vs-perf-jit-nested-identity`, `20261113T062011Z-perf-jit-typed-local-stores-vs-perf-jit-nested-identity`; root `.work/tmp/jit-nested-identity-{independent-review,independent-review-initial}.json`, `jit-nested-identity-{rust-tests,native,source-proof,before}.log` |
| 2026-11-12 | e891 | J | T4 | reuse identical virtual resume materializer stubs by region, bytecode target and exit-cache variant | accepted `perf-jit-nested-identity` at main `a873387f` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0021x incumbent, isolated Serc footprint <=1.0260x no-Serc, code <=1.6075x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 84 Rust tests; targeted FFI AddressSanitizer; 28 native integrations; expanded suites retain the same 16 reviewed exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native and no-Serc control review approves. The regression observes 424 resume guards and verifies actual sharing plus separated regions/entry refusals. Private descriptor is 8 bytes with u32 source indices; refused plans leave output untouched | `.work/wt/jit-exit-stub-reuse/.work/tmp/exit-stub-reuse-feature-acceptance.json`, `exit-stub-reuse-resources.json`; corpus `20261113T070739Z-perf-jit-nested-identity-vs-perf-jit-exit-stub-reuse`, `20261113T071012Z-perf-jit-nested-identity-vs-perf-jit-exit-stub-reuse`; root `.work/tmp/jit-exit-stub-reuse-{rust-tests,asan,native,source-proof,before}.log`, `jit-exit-stub-reuse-independent-review.json` |
| 2026-11-13 | e891 | J | T4 | apply existing Rust unboxing inside virtual bodies with precise boxed resume and error continuations | accepted `perf-jit-exit-stub-reuse` at main `648dd7c5` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0051x incumbent, isolated Serc footprint <=1.0444x no-Serc, code <=1.9823x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 86 Rust tests; 99 upstream generator tests; 29 native integrations; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves both variants and corrected partial-boxing proof. Exact raw-prefix callback progress, overflow, errors/locals, allocator injection and observers pass. First attempt retained boxed bodies because the pure getter was misclassified as escaping; final generated metadata fixes that fact centrally | `.work/wt/jit-virtual-body-unbox/.work/tmp/virtual-body-unbox-feature-acceptance.json`, `virtual-body-unbox-resources.json`; corpus `20261113T080103Z-perf-jit-exit-stub-reuse-vs-perf-jit-virtual-body-unbox`, `20261113T080338Z-perf-jit-exit-stub-reuse-vs-perf-jit-virtual-body-unbox`; root `.work/tmp/jit-virtual-body-unbox-{rust-tests,generator-check,generated-cases,native,before,source-proof}.log`, `jit-virtual-body-unbox-independent-review.json` |
| 2026-11-13 | e891 | J | T4 | share complete virtual boxing/resume/error recipes only for identical target, cache variant and float/integer masks within one region | accepted `perf-jit-virtual-body-unbox` at main `dc21c28a` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0014x incumbent, isolated Serc footprint <=1.0289x no-Serc, code <=1.8602x M0 | CORRECT: clean matched JIT/Serc and JIT-only control builds each verify 58 extensions; 87 Rust tests; targeted FFI AddressSanitizer; 30 native integrations; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/sanitizer/generated/native review approves both variants. Two masked regions use 5 targets each for 8 and 6 guards, versus 8 and 6 distinct predecessor targets. Exact fallback, errors, locals, payloads, progress, overflow and partial boxing pass. Private descriptor grows to 16 bytes; overlap refuses atomically. Raytrace code is 652,592 bytes in every trial | `.work/wt/jit-masked-exit-stubs/.work/tmp/masked-exit-stubs-feature-acceptance.json`, `masked-exit-stubs-resources.json`; corpus `20261113T084451Z-perf-jit-virtual-body-unbox-vs-perf-jit-masked-exit-stubs`, `20261113T084726Z-perf-jit-virtual-body-unbox-vs-perf-jit-masked-exit-stubs`; root `.work/tmp/jit-masked-exit-stubs-{rust-tests,asan,before,native,source-proof}.log`, `jit-masked-exit-stubs-independent-review.json` |
| 2026-11-13 | e891 | J | T3 | fuse readonly short-circuit Boolean attribute predicates with origin-aware alias proof and existing virtual materializers | accepted `perf-jit-masked-exit-stubs` at main `850ccc9a` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0018x incumbent, isolated Serc footprint <=1.0283x no-Serc, code <=1.8602x M0 | CORRECT: clean matched JIT/Serc and JIT-only builds each verify 58 extensions; 89 Rust tests and targeted FFI AddressSanitizer; 31 native integrations; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves both variants. All 64 truth-table/layout/call traces fuse. Active branch flips preserve original return PCs; exact callbacks, local rebinding without replay, owned results, aliases, finalizers and observers pass. Private leaf map grows to 180 bytes; descriptors/steps stay 16 bytes; no runtime kernel is added | `.work/wt/jit-bool-predicates/.work/tmp/bool-predicates-feature-acceptance.json`, `bool-predicates-resources.json`; corpus `20261113T092448Z-perf-jit-masked-exit-stubs-vs-perf-jit-bool-predicates`, `20261113T092727Z-...`; root `.work/tmp/jit-bool-predicates-{rust-tests,asan,before,native,source-proof}.log`, `jit-bool-predicates-independent-review.json` |
| 2026-11-13 | e891 | J | T3 | fuse guarded managed-inline Boolean constant setters returning their receiver, with exact continuation after prior writes | accepted `perf-jit-bool-predicates` at main `7b07b597` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0014x incumbent, isolated Serc footprint <=1.0337x no-Serc, code <=1.8602x M0 | CORRECT: clean matched JIT/Serc and JIT-only builds each verify 58 extensions; 91 Rust tests and targeted FFI AddressSanitizer; generated store flags EXIT only; 32 native integrations; same 16 reviewed suite exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves both variants. 64 managed and 64 slot-control traces pass. Exact store PCs, partial progress, finalizers, rebinding, insertion order, storage fallback, owned receiver and observers pass. Eight frozen Richards calls use three new stores each. Leaf map grows to 188 bytes; Boolean fact occupies descriptor byte 5, preserving 16-byte descriptor/step widths | `.work/wt/jit-bool-state-updates/.work/tmp/bool-state-updates-feature-acceptance.json`, `bool-state-updates-resources.json`; corpus `20261113T100446Z-perf-jit-bool-predicates-vs-perf-jit-bool-state-updates`, `20261113T100723Z-...`; root `.work/tmp/jit-bool-state-updates-{rust-tests,asan,metadata,before,native,source-proof,frozen-shapes}.log`, `jit-bool-state-updates-independent-review.json` |
| 2026-11-13 | e891 | J | correctness | preserve allocation source lines by staying in tier one while tracemalloc is active, including callback transitions and cached-executor resumption | accepted `perf-jit-bool-state-updates` at main `b42a47ea` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0014x incumbent, isolated Serc footprint <=1.0372x no-Serc, code <=1.8792x M0 | CORRECT: clean matched variants verify 58 extensions each; nine incumbent failures become exact-site regressions with interpreter and no-optimization controls; 33 native integrations; 18 focused suites retain the same 16 exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source and final generated/native reviews approve both variants. Rust source and private ABI remain unchanged; prior 91-test and sanitizer evidence remains applicable. Raytrace code is 659,248 bytes in every trial | runtime `6415084b`, fixture `5911115d`; `.work/wt/jit-array-allocation-site/.work/tmp/array-allocation-site-{feature-acceptance,resources}.json`; corpus `20261113T111548Z-perf-jit-bool-state-updates-vs-perf-jit-array-allocation-site`, `20261113T111829Z-...`; root `jit-array-allocation-site-{before,native,source-proof}.log` and `{source-review,independent-review}.json` |
| 2026-11-13 | e891 | J | T2 | connect borrowed double-array reads to profitable raw float arithmetic components while preserving boxed inputs, cleanup and exit state | accepted `perf-jit-array-allocation-site` at main `ebdaf009` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0063x incumbent, isolated Serc footprint <=1.0355x no-Serc, code <=1.8792x M0 | CORRECT: clean matched variants verify 58 extensions each; unchanged patch after correctness-prerequisite rebase; 95 Rust tests and targeted FFI AddressSanitizer; 34 native integrations; 18 focused suites retain the same 16 exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests; independent source/generated/native review approves both variants. Exact bits, masks4/12, bounds, callbacks, owned arrays, observers and injected boxing failures pass. Synthetic action16 is appended; descriptor/step sizes stay8/16. Frozen emitted counts per trial: sparse4, SOR2, LU2, MonteCarlo12, FFT0 | runtime `bd8c5ca6`, fixture `4de52240`; `.work/wt/jit-array-read-unbox/.work/tmp/array-read-unbox-{feature-acceptance,resources}.json`; corpus `20261113T113242Z-perf-jit-array-allocation-site-vs-perf-jit-array-read-unbox`, `20261113T113523Z-...`; root `jit-array-read-unbox-{rust-tests,asan,metadata,before,native,noserc-native,source-proof}.log`, `{source-review,independent-review,rebase-proof,frozen-counts}.json` |
| 2026-11-13 | e891 | J | T4 | guard observed exact-float old locals before reusing existing typed-store cleanup in the initial root interval | accepted `perf-jit-array-read-unbox` at main `b772dd17` | performance deferred (OD16) | five interleaved resource trials pass: matched RSS <=1.0045x incumbent, isolated Serc footprint <=1.0352x no-Serc, code <=1.8792x M0 | CORRECT: clean matched variants verify 58 extensions each; 98 Rust tests and targeted FFI AddressSanitizer; 35 native integrations; 18 focused suites retain the same 16 exclusions; 2,000 fuzz programs (1,949 JIT-active, 0 differences); 31 equal digests. Independent source/generated and verified leased native-only reviews approve both variants. Two adjacent guard/store pairs preserve original finalizer PC, ownership, reflected local mutation and observers. Fresh-frame NULL fallback preserves sole input and JIT progress. Private map/input/step widths36/6/8; no new stencil. Frozen nbody emits14+4pairs and maps44,396 bytes; raytrace stays659,248 | runtime `f7472384`, fixtures through `9abde336`; `.work/wt/jit-guarded-local-cleanup/.work/tmp/guarded-local-cleanup-{feature-acceptance,resources}.json`; corpus `20261113T121041Z-perf-jit-array-read-unbox-vs-perf-jit-guarded-local-cleanup`, `20261113T121625Z-...`; root `jit-guarded-local-cleanup-{rust-tests,asan,before,native-final,source-proof,frozen-shapes}.log` and `{source-review,independent-review,review-lease-audit,frozen-shapes}.json` |
