# OD22 Devin VM evidence (2026-11-20/21, PROVISIONAL)

Host: Ubuntu 22.04.5 / glibc 2.35, kernel 6.8.0-1060-aws, KVM, 8 vCPU Intel Xeon
(microcode 0x1), `perf_event_paranoid=4`. Harness: `main@ffeca5f` era harness
(pre-`b3f896a` CPU accounting). Every verdict here is PROVISIONAL and establishes
no qualified Linux result.

| Path | Content |
| --- | --- |
| `verdicts/*.verdict.json.xz` | all 24 `perf-bench` verdicts in the window, byte-identical to the raw files (see `raw-tree-digests.tsv`) |
| `build-reports/*.json` | `perf.py build` reports for every stage, including map twins |
| `logs/*.xz` | orchestration logs, stage build logs (`<worktree>__<file>`), `test --all` parity logs |
| `recipes.bundle` | 8 recipe branches `campaign/recipes/od22/*` (also pushed to origin); requires `ffeca5f`, `574946e`, `f19fb2f`, `3bd21f6` |
| `raw-tree-digests.tsv` | 3,855 keys: per `run-dir/run-N/workload` subtree, file count, bytes and sha256 over its sorted `sha256  path` lines; single files carry their own sha256 |
| `replay_cpu_repair.py`, `replay-*.json` | DIAGNOSTIC replay of all 24 decisions under the pre-repair (`43a26e1`) and repaired (`b3f896a`) `perf_verdict.py` |
| `copies.sha256` | sha256 of every file in this directory except itself |

Raw result trees (~2.0 GiB, 176,381 files) remain git-ignored under
`rust-cpython/results/` on the VM; the full per-file manifests are host-only at
`/home/ubuntu/campaign-evidence-host/`. No authorized durable large-artifact
destination was available, so raw retention ends with the VM.

**Missing evidence.** No P0 run (A/A, calibrate, G1, both G2, G3 x3, G4, G5,
fuzz seeds) retained an external `perf-gate-ready` pre or post check; only the
harness's internal `quiet=yes` host samples exist. The q9 stages predate Linux
JIT maps, so G1 code bytes come from instrumentation twins.

**Diagnostic CPU replay.** The pre-repair code reproduces all 24 recorded
decisions. Under `b3f896a` only G5 `20261121T120910Z` changes, ACCEPT ->
INCONCLUSIVE: all 10 of its workloads recorded `incomplete: wait4 root only;
descendants excluded`, which the old verdict dropped silently. The same
coverage gap removed CPU from the 7 application workloads in A/A, G1, G2, G3
gate and G4. Raw verdicts are unchanged; the replay is not a receipt.
