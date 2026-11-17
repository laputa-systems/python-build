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

## Correctness (same epoch, completed at closeout)

- **Suite matrix**: all four variants ran `--all` (52,102 tests) plus
  `--serc-verify` on both Serc builds. Non-JIT builds fail exactly
  `test_asyncio.test_sendfile`, `test_asyncio.test_sslproto`, `test_ssl`,
  `test_support` (the historical `test_multiprocessing_forkserver.
  test_misc` known-red passed this epoch). JIT builds add only
  `test_capi` — 19 `test_opt` uop-shape assertions, all explained
  (semantics verified; catalogued in `CLIMB_SUPERSEDED_UOP_ASSERTIONS`).
  Serc verify introduces no failed-test difference.
- **Combined build** (`perf-final-jit-serc`): 2,000-program differential
  fuzz, 97.45% JIT coverage, 0 mismatches; targeted JIT suites differ
  only by the superseded assertions; all 8 held-out kernel digests equal
  the incumbent (`20261117T041840Z`).
- **C3 feature correctness** remains CORRECT from `campaign/c3` `fb131e1`
  (30,318-case census, zero mismatches). Verified at closeout that
  `serc-overlay/crates/serc/src` is unchanged since `fb131e1` except a
  doc comment in `config.rs`, so the census carries to this epoch.

## Measured gaps to the done criteria

- **M4 unmet**: no candidate ever passed the acceptance gate; the
  recorded incumbent score does not qualify.
- **C3 performance unmet**: cold-compilation ACCEPT and OD15 footprint
  ACCEPT both failed on the locked set.
- **Combined performance qualification unmet**: `perf-final-jit-serc`
  correctness is fully evidenced, but no accepted JIT or Serc result
  exists to qualify a combined score against.
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

**Resolved 2026-11-17:** the owner resumed the campaign — Linux is
indefinitely out of scope (deferred, not a blocker); J measurement-gate
repair is authorized (principled statistic correction, 1% floor and M4
preserved); Serc cold-compile optimization is authorized as a dedicated
profile-backed effort. Campaign resumed toward COMPLETE_MACOS on macOS
arm64 only.

Historical blockers, for the record:

1. **J track**: the all-metrics gate could not ACCEPT any
   different-binary candidate (layout-sensitive ~0.4–1% offsets,
   null-probe proven).
2. **C track**: Serc cold compilation ran 6–16% slower wall / up to
   +37% peak RSS than the C compiler path on the locked workloads.
3. **Linux**: deferred until a native Linux host is supplied.

## Evidence manifest

Raw receipts live under `rust-cpython/results/perf-bench/<UTC>-<a>-vs-<b>/`
(`verdict.json` plus per-round samples) and build reports under
`rust-cpython/results/*.json`; lane logs under `rust-cpython/logs/`.
Key receipts: `20261116T183903Z` (CALIBRATION-OK),
`20261116T192836Z` and `20261116T223944Z` (A/A), `20261116T213216Z` and
`20261116T234252Z` (evals 9–10), `20261117T004023Z` and
`20261117T015234Z` (null probes), `20261117T025435Z` (C3 REJECT),
`20261117T033603Z` (OD15), `20261117T041840Z` (combined digests).
The campaign ledger
`docs/CAMPAIGN-RUST-CPYTHON-JIT.md` carries the full row-level history
and per-candidate correctness/review evidence.

Named closeout evidence archive:
`.work/campaign-archive/closeout-20261117.tar.gz`
(SHA-256 `ed1dacc13ff903e871323cb7bfde56ff1abd708f1fd3e1043c6bf3c19144ec1c`).
It contains all 37 `verdict.json` receipts from both the primary lane and
the `jit-subscr-virtual` worktree (path-preserved under `verdicts/`),
`manifest.tsv` mapping each receipt to its baseline/candidate build
labels, commits, flags, decision, and harness digest, the six
`perf-*-all*.log` suite logs, the three stage build logs, and
`builds/*.json` build reports. Full per-round sample data remains in
place under `rust-cpython/results/perf-bench/` and the worktree's
`results/perf-bench/` on this host; the archive preserves every harness
JSON needed to audit the decisions without it.
