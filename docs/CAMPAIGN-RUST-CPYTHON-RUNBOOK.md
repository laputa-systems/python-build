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
git log --oneline --left-right HEAD...origin/campaign/cloud-ubuntu-baseline
```

If already on `campaign/cloud-ubuntu-baseline` with a clean working tree,
fast-forward with `git merge --ff-only origin/campaign/cloud-ubuntu-baseline`.
If the local branch has work or a command still owns a lease, preserve and
reconcile that state first. Do not reset, force push or overwrite an active
candidate. Then reread this runbook and current Status. A remote steering
commit does not update a running worker's checkout by itself.

The last operational checkpoint reviewed for this steering is
`af2371da5351f449e665981fc84bc133ec642a7c` on
`campaign/cloud-ubuntu-baseline`. Its Linux reports describe complete baseline
build/correctness preparation, reconstructed P0/M0 controls, and **zero
qualified Linux optimization gains**. The latest commit verifies M0 output
parity; it is not a speedup. See the campaign's compact
[Status](CAMPAIGN-RUST-CPYTHON-JIT.md#status) for live updates.

At this checkpoint, do the following in order; a blocked item does not hold
the others:

1. **Recover evidence once, then reuse it.** On the original cloud checkout,
   verify the recorded stage/build identities and archive hashes. The cited
   `rust-cpython/results/*.tar.zst`, recipe bundle and logs are ignored and
   absent from the published Git tree. Treat them as host-only until an actual
   retrievable copy is verified. Commit compact receipt JSON/manifests and
   review records under `docs/campaign-evidence/`, outside the ignored results
   directory; record archive location, retention boundary and SHA-256. Keep
   large archives in an already authorized durable artifact destination when
   available. A missing archive is evidence debt, not permission to invent it
   or redo every test. Recreate only evidence required for the next decision.
2. **Finish the isolated CPU-accounting repair.** Review
   `b3f896aeb0d2e1083475585de5f65cfe021f7620` on
   `campaign/cloud-h0-linux-cpu` against its parent, including the waited-child,
   cold-ZIP double-count and incomplete-coverage controls. Integrate only the
   reviewed repair as a separate harness commit. Re-run affected controller
   tests; preserve old raw verdicts and label replays diagnostic. This fixes
   evidence coverage, not host variance. New gates use one exact reviewed
   harness identity and still need fresh H0/P0.
3. **Start a Serc code-residency experiment.** The Linux probes already
   distinguish compilation-driven executable residency from baseline link
   cost. Use that evidence without waiting for unavailable PMU counters:
   compare one package-local size-oriented release profile for Serc/parser
   code with its exact unchanged parent, in a separate worktree. Keep global
   LTO and all unrelated crates fixed. Record ELF sections, touched mappings,
   source/`.pyc` control results and exact compiler-output parity. A smaller
   binary alone is not a speedup or C3 acceptance. If the mechanism fails its
   declared locality/semantic prediction, retire it; next investigate cold
   error-path outlining, not the same profile with another noise retry.
4. **Prepare one JIT allocation/materialization experiment.** First obtain
   Linux-specific structural evidence on training workloads: emitted trace
   shapes, boxing/allocation call sites or separately instrumented software
   counts. Do not use Mac CPU shares as Linux profiles. Select one trace shape
   with repeated box/unbox traffic and write its exit, observer and ownership
   obligations before prototyping Rust planner changes. Instrumented counts
   support a mechanism; they are neither timing receipts nor a speed ranking.
   Drop the prototype if it cannot remove the predicted work or preserve
   semantics. Keep required frame/deopt/refcount review and targeted tests.
5. **Reopen timing only on changed evidence.** Inspect an already authorized
   controlled Linux worker if available. Record its host identity, verify the
   pinned setup and run the predetermined full H0 self-calibration and clean
   equal-prefix-length A/A pair, then P0 G1–G5. Use matched stages on that
   worker; do not transport cloud timings as qualification. If access or a
   suitable host is absent, record that exact infrastructure blocker and
   continue the independent work above. Do not provision paid infrastructure
   or change host isolation/security policy implicitly.

The queue is renewed as experiments finish. Every proposed item states its
observed mechanism, concrete change, falsifier, smallest useful test and the
later acceptance gate. Rank using current training-workload evidence; held-out
results are qualification only. No more full suites on unchanged baselines
merely to produce a new checkpoint. Run full acceptance when integrating a
candidate, and repeat other checks only for a changed risk or required gate.

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

> Continue the Linux Rust CPython campaign on campaign/cloud-ubuntu-baseline
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
