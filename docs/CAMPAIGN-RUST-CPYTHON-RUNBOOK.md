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

On resume, inspect local state and fetch the steering before selecting work:

```sh
git status --short --branch
git fetch origin
git log --oneline --left-right HEAD...origin/integ/od22-reconcile
```

The current integration branch is `integ/od22-reconcile`. It reconciles
`main` with `campaign/cloud-ubuntu-baseline` and carries every OD22
checkpoint. If you are on it with a clean tree, fast-forward with
`git merge --ff-only origin/integ/od22-reconcile`.

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
| C (Serc) archive | C-ALLOC-1 `c411d42`: retired at attempt 1/1 on memory (`RESULT-CALLOC1.md`). | Corpus `Ir` −8.54%, but source-mode private memory +762 KiB and peak RSS +11.9%. | None. | A bounded-retention allocator needs its own plan. | None. |
| C (Serc) observation | C-LOC-1 `6e5568c`: retired at attempt 1/1. | The corrected probe shows the original control failure was a probe artifact (`2026-11-22-probe2/`). | None for the record. | None until the owner rules on re-admission. | Owner decision. |
| J (JIT) | J-ES-2 `0c3b192` (J-ES-1 + J-LT-2 combined). This was the single authorized attempt; the frame representation stays paused. | Predictions P1–P4 held (`RESULT-JES2.md`). Prepared, unqualified. Instruction-count screen (`RESULT-JES2-IR.md`): sparse executed instructions −3.58%, FFT and nbody within ±0.01%. These are instruction counts, not timing. | None on this host. | The next JIT hypothesis needs a fresh refusal census of the candidate (`ltcensus/`) before any prediction. | Timing needs a qualified host. |
| J (JIT) | J-TLS-1 `4a4bf84`: initial-exec TLS model for libpython's C thread-locals. | Predictions T1–T5 held (`RESULT-JTLS1.md`). Prepared, unqualified. The J track is full (2/2). | None. | P0 on a qualified host. | No qualified Ubuntu 24.04 host. |
| H (harness) | `tools/p0-bench` must defer before the bench when the precheck fails. | Open. | Compact CPU-replay inputs for the diagnostic receipts. | Patch `p0-bench` plus a controller test | None |
| Timing / IPC | None | Deferred | Fresh H0/A/A/P0 on a qualified host. PMU counters. | `tools/p0-bench` on a qualified host | No qualified Ubuntu 24.04 host. `perf_event_paranoid=4` on this VM. |

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

> Continue the Linux Rust CPython campaign on integ/od22-reconcile
> after fetching origin and safely reconciling the latest steering with local
> work. Follow AGENTS.md, docs/CAMPAIGN-RUST-CPYTHON-RUNBOOK.md and OD21. Keep working
> across checkpoints and bounded experiment rounds toward qualified M4/C3,
> then continue measured improvements within the same boundary until I pause
> the campaign. Start from the latest Status and exact verified artifacts;
> do not redo completed baseline work. Complete the next unblocked code or
> evidence experiment, preserve strict acceptance and isolated candidates,
> checkpoint and continue. A noisy host defers timing acceptance, not all
> research. Do not declare completion on a turn boundary or plateau. If all
> defensible work is blocked or the platform limits execution, preserve the
> exact resume state and identify the concrete condition that will unblock it.
