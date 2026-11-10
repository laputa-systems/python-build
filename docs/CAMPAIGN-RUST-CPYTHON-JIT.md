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
- Until **Close**, timing gates are waived (OD14): no step waits for a quiet
  host or a `perf-gate-ready` window, and no step lands or is rejected on a
  timing result. The rules below apply to the timing gates **Close** runs.
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

`--serc` does not exist yet; C2 adds it to `perf.py build` and
`rust-cpython/build.py build`. Serc is linked only into `--serc` builds.
"Rebuild clean" means `perf.py clean --name <build>` then `perf.py build`
with the table's flags at `main`'s head.

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

### T3: inlining and allocation removal, toward M3

Inline Python-to-Python calls with lazy frame materialization on deopt. Add
attribute and global inline caches whose invalidation honours descriptors,
mutation, inheritance, lazy imports, and subinterpreters. Remove non-escaping
allocations. Lengthen traces with loop peeling and invariant-guard hoisting.

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
  --with-ensurepip=install`, `make`, `make install`.
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
bytes. `cargo test -p serc --locked` passes 8,624 tests on `nightly-2026-09-15`
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
private Cargo home. Offline `cargo test -p serc --locked --no-fail-fast` runs
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
reruns the census; it exits nonzero on any mismatch. `cargo test -p serc -p
serc-cli --locked --offline` passes with every `SERC_TEST_PYTHON*` oracle
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

### C3: all source modes

Extend the Serc path to `eval` and `single`, with C1's oracle extended to those
modes and C2's exit rerun. AST-object input (`compile(ast_obj, ...)`) stays on
the C compiler: Serc consumes Ruff's AST, and converting CPython's is out of
scope.

**Exit:** done-criterion 4 and a C3 ledger row.

### C acceptance

A track C change lands only when, on `perf-serc-<item>`: the C1 corpus has zero
mismatches; `perf.py test --all` fails exactly `perf-rust`'s set; from C2 on,
the full-suite verify run reports no difference; and an independent reviewer
approves any change to FFI, ownership, or the compile hook. Under OD14 the C2
and C3 exits are met without their timing gates, which run at **Close**.

## Close

Close first runs every timing gate OD14 deferred, on a quiet host: the
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
- **Parked:** a method-based frontend (withdrawn PEP 836 prototype, observed
  head `04a79098`) opens only by owner decision.

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

| Lane | State | Current item | Last result |
| --- | --- | --- | --- |
| J | active | T2 generic `_BINARY_OP` specialization landed; next: T3 inlining | T2 generic ops: monte_carlo `float += 2**31-1` 9.6% → 0.0% (new `_BINARY_OP_ADD_FLOAT_INT` 8.8%), nbody `float ** float` 11.6% → 0.0% (new `_BINARY_OP_POWER_FLOAT` 6.4%); CORRECT (5 incumbent superseded assertions), advisory 0.9992 |
| R1 | done | — | `S = 0.7960` steady JIT vs off, held-out 1.0621; catalog below |
| C | active | C2 in-process `exec` | C0, marshal parity and C1 landed: census 9,102 / 9,135 exact, 0 mismatches on e891 |
| Close | deferred | macOS only by owner ruling (2026-11-09); Linux image absent | — |

**Owner queue:** R1 reference report (informational, no target change
requested): CinderX 3.15 steady JIT uplift `S = 0.7960`, held-out 1.0621, beside
M2 ≤ 0.48, M3 ≤ 0.38, M4 ≤ 0.25. T2 does not wait for an answer.

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
