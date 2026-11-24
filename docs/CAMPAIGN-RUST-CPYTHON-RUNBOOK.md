# Continuous Rust CPython campaign

**Owner direction, 2026-11-19 (OD21): keep hillclimbing on Linux indefinitely.**
This is the operating entry point for the existing
[JIT/Serc campaign](CAMPAIGN-RUST-CPYTHON-JIT.md). Read it before the long
ledger. It supersedes older instructions to end the campaign after a finite
round, stop all work for a timing deferral, or prohibit continuation scheduling.
The campaign's source epoch, production boundary, Rust rule, acceptance gates,
semantic requirements and independent review requirements remain in force.

## What continuous means

Work through **hypothesis → smallest useful experiment → evidence → decision
→ checkpoint → next hypothesis**, without asking the owner to say “continue.”
Commit/checkpoint completion, a status answer, a context compaction and a
normal turn ending are not campaign completion. While execution is available,
checkpoint and proceed to the next useful action in the same session.

Keep the existing three root-caused attempts/two design variants per mechanism
and the 8+4 review trigger. Exhaustion retires that mechanism or search round;
it triggers fresh attribution, source study and a different, evidence-backed
hypothesis. Preserve all counters and failed results across sessions. Do not
rename a rejected mechanism to reset its budget. A platform-invalid run is a
deferral, not a win or a measured optimization failure.

M4 and C3 remain the first milestones. If qualified, publish their evidence and
continue improving the measured incumbent within the same boundary. Preserve
the achieved bars. A milestone, plateau in one mechanism, or finite work batch
does not revoke the owner's continuous mandate. Changes to the frozen workload
sets, source epoch or production scope still need their existing decisions.

Only an owner pause/cancellation, an enforced execution/access/budget limit, or
the absence of any defensible action within the authorized boundary may idle
the worker. Record the precise condition and recovery action. Do not label
idling as COMPLETE or consume compute on repeated checks to appear active.
Respect an explicit stop and platform controls; continuation never bypasses
them or silently raises a budget.

## Current position and immediate queue

**Steering (OD25, 2026-11-24, executing OD24).** Read
[OD25](CAMPAIGN-RUST-CPYTHON-JIT.md#od25-2026-11-24-owner-execute-od24-comparison-contract-before-measurement)
and then
[OD24](CAMPAIGN-RUST-CPYTHON-JIT.md#od24-2026-11-23-owner-qualify-prepared-work-one-c-loc-1-correction).
OD25 governs where this runbook disagrees, until a worker reconciles it.
The musl qualification-path repair is complete: measured-environment identity
is verified before every measurement on the container and direct Alpine paths,
and the pair comparison contract rejects incomplete or incompatible identities.
Order of work: while timing is blocked, run the one preregistered C-LOC-1
correction; qualify J-ES-2, J-BITCAST-1 and C-REL-3 on an eligible host;
J-ARRAY-1 when a J slot clears.

**Resume checkpoint (2026-11-24, this host).** `main` is ahead of
`integ/od22-reconcile` and contains OD24; a clean `main` needs no reconciliation.
Work resumed on `campaign/musl-qualification-path` off `main`; no prior campaign
worker, worktree or automation was observed. `tools/perf-gate-ready` now checks
host readiness only and records host identity for the receipt; measured-stage
identity stays with `perf.py`'s build reports and stage digests, and H0/A/A
statistical qualification stays with the harness verdicts. `p0-bench`'s precheck
path is portable on Alpine, and `musl-run` uses Docker `--init`. Lane
`perf.py doctor` is `ok: true` in the pinned Alpine container. `_measure` now
requires a clean pinned-musl environment and a comparable pair before sampling:
target, source epoch, toolchain/runtime and allocator must match, and missing
identities fail closed. Focused tests and 314 other lane tests passed (25
skipped); the five C-core contract test files need a built stage and were not
run. Direct `perf.py bench` on this host failed the environment check (exit 1)
before resolving a stage; in-container doctor passed and `p0-bench --runs 1`
returned `DEFERRED` (exit 4) with no benchmark. No build, calibration, A/A or P0
was attempted, and qualified gains remain 0. The exact blocker is host
contention from unrelated resident services; timing reopens only on a host that
passes the pre/post gate.

Owner steering arrives on `origin/main`. On resume, inspect local state and
active leases, then discover and consume the latest steering before selecting
work:

```sh
git status --short --branch
git stash list
git fetch origin
git log --oneline --left-right HEAD...origin/main
git log --oneline origin/main -- docs/CAMPAIGN-RUST-CPYTHON-JIT.md docs/CAMPAIGN-RUST-CPYTHON-RUNBOOK.md
git merge --ff-only origin/main || git merge --no-ff origin/main
```

The first log shows what the checkout lacks (`>`) and what it holds
unpublished (`<`). The second lists the steering commits; read the newest OD
section in Status from `origin/main` before acting. Merge only with a clean or
committed working tree; never reset, stash-drop or force-push to make it
apply. If the merge conflicts with candidate work, stop, keep both sides and
record how the steering was incorporated. Record the worker/session, checkout
SHA, steering SHA consumed, last completed action and next command at the
checkpoint. `integ/od22-reconcile` (`44de748a`) is an ancestor of `main` and is
historical; do not resume from it.

If the local branch has work, or a command still owns a lease, preserve and
reconcile that state first. Never reset, force-push or overwrite an active
candidate. Then reread this runbook and the current Status. A remote steering
commit does not update a running worker's checkout by itself.

**Worker (verified 2026-11-22).** The only observed writer is the Devin
session `3f1f17e563f145baaa3379dab72f794e`, running on the OD22 Ubuntu 22.04
VM. It continues through that session's own turns. No scheduler or Goal
relaunches it. The `rcp-build-queue` reminder automation is stale and is not a
continuation mechanism. A pushed commit does not show that a worker is
running.

Per-track state. The Status table carries the same rows, with evidence links.
Completed items (evidence recovery, CPU-accounting repair, C-LOC-1, J-ES-1,
J-LT-2, refusal census) live in Status and the Ledger, not here.

| Track | Current candidate | Disposition | Evidence debt | Next command | Blocker |
| --- | --- | --- | --- | --- | --- |
| C (Serc) | C-REL-3 merged into `main` via PR #20 (`98ede3a`), under the owner's instruction to merge outstanding work. Structural only; not timed. C-REL-2 is archived, not landing: the owner keeps panic file:line. | No prepared candidate. | None. | Open the next Serc hypothesis with predictions written before any build. | Timing of the landed C-REL-3 needs a qualified host. |
| C (Serc) glibc-only | C-TLS-2 `04124a0`: staticlib Rust initial-exec TLS. | Null on musl (OD23); its code applies only to glibc and is kept there. Occupies no musl slot (OD25). | None. | None on the musl lane. | None. |
| C (Serc) archive | C-ALLOC-1 `c411d42`: retired at attempt 1/1 on memory (`RESULT-CALLOC1.md`). | Corpus `Ir` −8.54%, but source-mode private memory +762 KiB and peak RSS +11.9%. | None. | A bounded-retention allocator needs its own plan. | None. |
| C (Serc) correction | C-LOC-1 `6e5568c`: retired at attempt 1/1; that retirement and its evidence stay. | The corrected probe shows the original control failure was a probe artifact (`2026-11-22-probe2/`). OD24 authorizes one measurement-defect correction; do not ask again. | Preregistered plan, then memory, correctness and instruction-count results. | Write the preregistered matched musl correction plan (corrected pyc/source/repeated-source probe) before any build. | None; native acceptance still needs a qualified host. |
| J (JIT) | J-ES-2 `0c3b192` (J-ES-1 + J-LT-2 combined). This was the single authorized attempt; the frame representation stays paused. | Predictions P1–P4 held (`RESULT-JES2.md`). Prepared, unqualified. Instruction-count screen (`RESULT-JES2-IR.md`): sparse executed instructions −3.58%, FFT and nbody within ±0.01%. These are instruction counts, not timing. | None on this host. | The next JIT hypothesis needs a fresh refusal census of the candidate (`ltcensus/`) before any prediction. | Timing needs a qualified host. |
| J (JIT) | J-BITCAST-1: union casts replace stencil `memcpy` bit casts (96 relocations removed). | Holds on musl (`RESULT-JBITCAST1.md`). Prepared, unqualified. With J-ES-2 the J track is full (2/2). | OD24 item 4: compact disassembly/relocation evidence and a narrow regression check for bit patterns and absent `memcpy` under the stencil flags. | P0 on a qualified host. | Timing needs a qualified host. |
| J (JIT) glibc-only | J-TLS-1 `4a4bf84`: initial-exec TLS model for libpython's C thread-locals. | Null on musl (OD23); kept for glibc. Occupies no musl slot (OD25). | None. | None on the musl lane. | None. |
| J (JIT) queued | J-ARRAY-1: inline array element helpers (`2026-11-23-jit-uop-profile/`). | Queued; starts only when a J slot clears. | Inspect emitted code and derive the saving from removable costs before implementing. | None until a slot clears. | J track full. |
| H (harness) | Done: host-readiness-only gate, Alpine-portable precheck, precheck-before-benchmark DEFERRED, `musl-run --init` reaping, pinned-environment check on both measurement paths, pair comparison contract (target, source epoch, toolchain/runtime, allocator) before sampling, and lane `libgcc_s` scope documented. | None for OD25 item 2. | Compact CPU-replay inputs for the diagnostic receipts. | Write the preregistered C-LOC-1 correction plan; qualify prepared candidates on a quiet host. | None |
| Timing / IPC | None | Deferred. Qualified gains: 0; accepted: 0. | Fresh musl H0/A/A/P0 on a quiet host. PMU counters. | `tools/p0-bench` on a quiet host with matched stage identities | This Alpine host fails idle, iowait and load from resident services; do not repeat timing on it unchanged. Reopens on an eligible x86-64 host that passes the pre/post gate. |

Every new queue item states its observed mechanism, concrete change,
falsifier, smallest useful test and later acceptance gate. Rank items by
current training-workload evidence. Held-out results are for qualification
only.

Do not run more full suites on unchanged baselines just to produce a new
checkpoint. Run full acceptance when integrating a candidate. Repeat other
checks only for a changed risk or a required gate.

## Separate development from qualification

The owner-selected Linux x86-64 worker may be bare metal, a cloud VM, or the
pinned Ubuntu 24.04 container. This supersedes OD20's blanket VM/container/
remote qualification prohibition. Emulation and other architectures stay out
of scope. Use the checked-in `rust-cpython/tools/cloud-ubuntu.sh` runner for
cloud work and preserve the existing host leases and sealed build boundary.

Qualification depends on evidence: full unchanged H0 self-calibration, a
clean same-host A/A pair, and P0 completed with each verdict handled as
prescribed. Successful builds, idle checks or a particular host type confer
no performance qualification. Record virtualization, CPU allocation, relevant
cgroup limits and exact host/toolchain identity. Host or configuration changes
invalidate affected qualification; claims apply only to the measured setup.
No Mac result transfers to Linux. The current cloud instance remains deferred.

The current shared cloud host failed full H0 despite passing some idle checks;
pinning a vCPU did not fix it, and native C controls varied well above the 1%
scale. **Do not rerun identical H0/A/A hoping to get lucky.** A materially
changed host, a demonstrated harness defect with an independently reviewed
repair, or a justified predeclared measurement protocol is required before a
new attempt. Changes to the statistical contract still follow the existing
owner-decision requirement. Keep the 1% floor, five runs, workload membership,
metrics and all verdicts intact. A two-workload smoke is not full H0.

`PERFORMANCE_DEFERRED` and `PROFILING_DEFERRED` describe specific evidence
channels. They do not forbid source work. Keep at most one active prototype
per track and two fully prepared, unqualified candidates per track. Build
independent candidates against the recorded incumbent, not a chain of ungated
changes. On reaching that queue limit, retire candidates contradicted by
structural/correctness evidence, work on attribution or the blocked evidence
path, or record an infrastructure wait if none can proceed. Do not evade the
limit by piling up branches or make a third candidate that needs the same
missing evidence to decide anything.

Keep distinct labels: `PROTOTYPE`, `CORRECT / PERF_DEFERRED`, `REJECT`, and
`ACCEPT`. Only complete acceptance advances the measured incumbent. Proven
correctness/harness repairs may integrate separately under their own rules;
they never inherit a performance win. P0 remains required before candidate
timing acceptance. Linux PSS is not silently relabeled as OD15's unavailable
physical-footprint metric. Report the resource REJECT and policy gap honestly.

## Checkpoint and resume contract

Maintain a short **Status** in the campaign and a compact evidence manifest.
Move narrative to the ledger/evidence sections instead of appending it to
Status. At each completed experiment, record:

| Field | Required content |
| --- | --- |
| Source | Integration branch/HEAD, pinned source/toolchain, harness SHA, accepted incumbent source/build digests, exact candidate and parent refs |
| State | Active track/phase, hypothesis, attempt counters, result, and qualification debt |
| Evidence | Exact command, exit/result JSON, verdict, relevant tests/review, host identity, archive hash and retrievable location; label host-only or missing copies |
| Progress | New hypotheses tested, candidates prepared, accepted optimizations and qualified score separately; a docs commit or repeated suite is not a new optimization |
| Continuation | One exact next command once paths are verified, its expected decision, alternative unblocked work, and blocker/reopen condition |
| Execution | Live worker/session ID when known, any child command/lease still running, and why execution yielded; never invent a restart or claim a worker is live without observing it |

Do not overwrite the original raw evidence, reset counters, or rebuild a
verified unchanged stage merely because the conversation restarted. Verify
files/reports after recovery; a Git SHA does not preserve ignored build trees.
Push coherent source and compact evidence checkpoints to the authorized
campaign branch under the existing **Boundary → Network** permission. Fetch
first, integrate concurrent changes without overwriting them, and never force
push. Do not push rejected prototypes to `main` or publish upstream messages.
Large logs/build trees stay out of Git. A checkpoint with only a local archive
must say that it is not portable yet.

## Execution across turns and sessions

Repo instructions select work; they do not keep a Cloud VM or agent process
alive. Prefer the running surface's supported persistent Goal/continuation
mechanism. Record whether it is actually active. On Codex surfaces supporting
Goals, `/goal` provides thread-scoped continuation while active and within
budget; it remains subject to pauses, limits and real blockers. See
[official Goals documentation](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)
and [long-running work](https://learn.chatgpt.com/docs/long-running-work).

For an existing persistent Linux CLI worker, supported non-interactive
continuation is `codex exec resume <SESSION_ID> "<next instruction>"`; use the
recorded exact ID, not `--last` across workers. Any existing supervisor must
allow one writer per checkout, consume the durable checkpoint, and respect
pause/cancel, authentication and budget limits. A scheduler is permitted for
this campaign, but must not blindly relaunch an explicitly stopped worker.
Do not install a second orchestrator just to duplicate a supported active
Goal. Verify CLI flags with local `--help` before using them. See
[official command reference](https://learn.chatgpt.com/docs/developer-commands).

Resume the original Cloud task while its workspace exists. A new task is a
new workspace: recover source plus durable artifacts, then rebuild only what
is unavailable. Saved VM state is not permanent artifact storage; see
[official Cloud environment documentation](https://learn.chatgpt.com/docs/environments/cloud-environments).
If this task has no access to the original worker, report the steering commit
and next action without saying that worker was restarted.

Use this as the persistent task instruction, or as the body of `/goal` on a
surface that supports it:

> Continue the Linux Rust CPython campaign on the authorized execution
> branch after fetching origin and safely reconciling the latest owner
> steering from origin/main with local work. Follow AGENTS.md, docs/CAMPAIGN-RUST-CPYTHON-RUNBOOK.md and OD21. Keep working
> across checkpoints and bounded experiment rounds toward qualified M4/C3,
> then continue measured improvements within the same boundary until I pause
> the campaign. Start from the latest Status and exact verified artifacts;
> do not redo completed baseline work. Complete the next unblocked code or
> evidence experiment, preserve strict acceptance and isolated candidates,
> checkpoint and continue. A noisy host defers timing acceptance, not all
> research. Do not declare completion on a turn boundary or plateau. If all
> defensible work is blocked or the platform limits execution, preserve the
> exact resume state and identify the concrete condition that will unblock it.
