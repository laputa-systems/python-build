# rust-cpython JIT campaign — closeout results

**Outcome: BLOCKED.** The campaign did not reach COMPLETE_MACOS. Two
independent blockers were established with receipts on native macOS arm64:

1. **J acceptance gate cannot qualify any real candidate on this host.**
   The all-metrics five-run gate requires every entity bounded within the
   1% floor in every run. Same-source A/A is WITHIN-BUDGET, but every
   different binary — including two semantically null probes (one C
   file-scope global, one Rust `.so` export) — produces stable paired
   0.4–1% offsets on layout-sensitive workloads (import_django wall,
   zlib_decode_1m cpu, nqueens, gzip/django_* wall/cpu). Deeper sampling
   resolves some entities but import_django wall's ~1.009 offset is a
   stable detected regression under the runner's noise-relative fail
   check. No threshold, workload, or estimator was relaxed.
2. **C3 cold-compilation gate REJECTED.** `perf-rust-serc` vs `perf-rust`
   on the locked set regressed in all five runs: cold_python_startup
   wall/cpu 1.157/1.082, cold_import_django 1.122/1.115,
   compileall_source 1.164/1.060, with peak RSS 1.19–1.37×. The OD15
   physical-footprint check was INCONCLUSIVE (most workloads 1.007–1.063×,
   cold_python_startup and gzip_extract_1m unbounded). Serc's cold
   compilation cost is real, not measurement noise, and consistent with
   the bounded warm-workload guard regressions.

## Tested source and environment

- CPython source commit: `e891a9117985888df3e5d48145cf07ddda098335`
  (3.16.0a1+dev), lane `main` at closeout `a009bf1` plus this document.
- Host: Apple M1 Max, macOS 27.2.1 (26B131), arm64, 10 cores.
- Toolchain: Xcode 27.0, Apple clang 21.0.0, SDK 27.0; pinned Rust
  `nightly-2026-09-15-aarch64-apple-darwin` (rustc 1.100.0-nightly
  574ff7d98); `-mcpu=apple-m1`, deployment target macOS 27.0.
- Linux evidence: **not collected** — deferred until the owner supplies a
  native Linux host.

## Runnable variants (same epoch, verified build reports)

| Build | Flags | Report |
| --- | --- | --- |
| `perf-rust` | none | `rust-cpython/results/perf-rust.json` |
| `perf-rust-jit` | `--jit` | `rust-cpython/results/perf-rust-jit.json` |
| `perf-rust-jit-aa` | `--jit` (A/A twin) | `rust-cpython/results/perf-rust-jit-aa.json` |
| `perf-rust-serc` | `--serc` | `rust-cpython/results/perf-rust-serc.json` |
| `perf-final-jit-serc` | `--jit --serc` | `rust-cpython/results/perf-final-jit-serc.json` |

Rebuild: `python3 rust-cpython/perf.py build --name <name> [--jit]
[--serc] --jobs 10` from the lane root. `perf-final-jit` has no separate
stage: no JIT candidate was ever accepted, so the final JIT variant is
the incumbent `perf-rust-jit` itself.

## Measured results (all quiet-host, `perf-gate-ready` clean both sides)

- **H0 calibration**: `20261116T183903Z` CALIBRATION-OK, all 23
  workloads within 1% over 5 runs after verdict-layer repairs
  (`f750e0a`, `67606dc`, `14e3af6`, `856c824`, `f876f66`, `fdf0c89`).
- **A/A**: `20261116T223944Z` WITHIN-BUDGET on every entity.
- **QUALIFY eval 9** (three-layer bundle, `17ade05`): CORRECT — 2,000
  fuzz programs 0 mismatches at 97.4% JIT coverage, digests equal,
  targeted suites clean; independent review APPROVE. Gate INCONCLUSIVE,
  point 0.9752 (`20261116T213216Z`). JIT code bytes ≤ ~1.25× incumbent.
- **QUALIFY eval 10** (two-layer subset, `25da18e`): gate INCONCLUSIVE,
  point 0.9755 (`20261116T234252Z`) — the list-read layer was not the
  cause. Evals 11–12 preserved unspent.
- **Null probes**: `perf-probe-layout` INCONCLUSIVE with ~10 straddling
  entities (`20261117T004023Z`); `perf-probe-rust` INCONCLUSIVE with 2
  (`20261117T015234Z`). These prove the signature is binary-layout
  sensitivity, not candidate defect.
- **Guard controls** (exploratory, not acceptance): `perf-guard-jit`
  bounded wall regressions vs frozen reference (coroutines 1.014,
  django_wsgi 1.020, generators 1.022; `20261116T201817Z`);
  `perf-guard-serc` adds gzip cpu 1.032, import_django ~1.015
  (`20261116T203003Z`). S = 0.7463 vs M0 0.7748 recorded for the
  incumbent — a measurement, not a qualified milestone; the earlier A/A
  was INCONCLUSIVE and the frozen-reference guard REJECTED.
- **C3**: cold gate REJECT (`20261117T025435Z`); OD15 footprint
  INCONCLUSIVE (`20261117T033603Z`). Feature correctness remains
  CORRECT from `campaign/c3` `fb131e1` (30,318-case census, zero
  mismatches).

## Measured gaps to the done criteria

- **M4 unmet**: no candidate ever passed the acceptance gate; the
  recorded incumbent score does not qualify.
- **C3 performance unmet**: cold-compilation ACCEPT and OD15 footprint
  ACCEPT both failed on the locked set.
- **Combined qualification unmet**: `perf-final-jit-serc` is built and
  runnable but the same-epoch final matrix was not run to acceptance
  because neither constituent track produced an accepted result.
- **Parked work**: `campaign/jit-subscr-virtual` worktree holds the
  unaccepted three-layer bundle (`17ade05`), subset (`25da18e`), and
  probe branches; unpack-fusion remains parked with a documented
  deoptimization/stack-depth hazard and a Rust-rule violation.

## Limitations

- All evidence is native macOS arm64 on one M1 Max; no Linux data.
- The 1% all-metrics gate is unmeasurable for different-binary candidates
  on this host at any sampling depth tried (5–300 rounds per workload).
- Serc cold compilation is 6–16% slower wall / up to +37% peak RSS than
  the C compiler path on the locked workloads.

## Owner queue / smallest unblock actions

1. **J track**: decide whether the acceptance gate may use a different
   statistic (e.g., pooled-ratio bound without per-run replication) or
   whether qualification requires different hardware. Until then no JIT
   candidate can be accepted; evals 11–12 remain unspent.
2. **C track**: Serc cold compilation needs a real performance effort
   (compiler-side) or an owner scope decision; the locked three-workload
   gate cannot pass at current costs.
3. Resume condition: owner supplies a native Linux host (deferred scope)
   or amends the gate policy; then rerun H0 calibration and the bounded
   CLIMB backlog (attribution specialization r2, subscript specialization
   r2, virtual-call overhead, unpack-sequence specialization, call/frame
   overhead).

## Evidence manifest

Raw receipts live under `rust-cpython/results/perf-bench/<UTC>-<a>-vs-<b>/`
(`verdict.json` plus per-round samples) and build reports under
`rust-cpython/results/*.json`; lane logs under `rust-cpython/logs/`.
Key receipts: `20261116T183903Z` (CALIBRATION-OK),
`20261116T192836Z` and `20261116T223944Z` (A/A), `20261116T213216Z` and
`20261116T234252Z` (evals 9–10), `20261117T004023Z` and
`20261117T015234Z` (null probes), `20261117T025435Z` (C3 REJECT),
`20261117T033603Z` (OD15). The campaign ledger
`docs/CAMPAIGN-RUST-CPYTHON-JIT.md` carries the full row-level history
and per-candidate correctness/review evidence.
