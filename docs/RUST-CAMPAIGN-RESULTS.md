# rust-cpython JIT campaign — results

**Current state (2026-11-18): ACTIVE.** The campaign reopened for Linux
x86-64 under OD20. The owner authorized build and baseline bring-up in
an Ubuntu 24.04 container on the x86-64 cloud host. Live progress and
receipt IDs are in the campaign's [Status](CAMPAIGN-RUST-CPYTHON-JIT.md#status).

## Linux cloud evidence

| Area | Outcome |
| --- | --- |
| Build | Debug smoke, all six optimized stages, and an independent Rust JIT twin built with the locked Ubuntu userspace and verified offline boundary. |
| Correctness | Full suites matched the explained failure sets, including Serc verification. The Serc corpus had zero mismatches over 30,138 cases. Rust JIT and combined differential fuzz each found zero mismatches over 6,000 programs across three seeds. All 16 training and eight held-out workload digests matched. All 114 Rust JIT optimizer unit tests passed. |
| Track J timing | PERFORMANCE_DEFERRED. The quiet five-run self-calibration left 18 metrics unbounded at the 1% floor. P0 timing and the Linux milestone score remain pending. |
| Track C memory | Both five-run resource gates rejected on Linux PSS and private-memory regressions. The collector did not report the OD15 physical-footprint metric. |
| Profiling | The host and container denied a self-only hardware CPU-cycle counter, so IPC qualification is deferred. |

The historical figures below were measured on macOS arm64. Three
conditions apply to those receipts:

- **OD20 (2026-11-18): qualification moves to Linux x86-64.** The Linux
  build and correctness results above apply to the authorized cloud
  environment. A host with bounded self-calibration and A/A evidence is
  required for timing qualification.
- **Eval 9 is unqualified.** Its recorded ACCEPT came from a 4-run retry
  after a 5-run INCONCLUSIVE under the same policy. A predetermined 5-run
  requalification is pending, and on the Linux host it must re-run against
  a Linux-rebuilt reference.
- **The scores cross build configurations.** `S = 0.7258` and the guard
  REJECT compare a fat-LTO incumbent against a thin-LTO reference
  (`perf-climb-ref`). The first qualified score of the current build epoch
  is pending against a fat-LTO reference built from the same source — on
  Linux, after bring-up.

The Serc numbers are same-configuration (`perf-rust` and `perf-rust-serc`
are both fat LTO at `5e09c5f`) as measured on macOS; C3 REJECTED at
attempt 3 there. Linux re-verifies.

## Historical closeout (2026-11-18 03:00, superseded)

The text below is the INCOMPLETE_PLATEAU closeout as written. It
superseded an earlier BLOCKED closeout (2026-11-17). Its "bounded space
exhausted" conclusions predate the Serc IPC finding and the eval-9 audit.

**Outcome then: INCOMPLETE_PLATEAU.** The resumed campaign (owner decisions of
2026-11-17: macOS arm64 only, J measurement-gate repair authorized, Serc
cold-compile optimization authorized) ran its bounded search to
exhaustion on native macOS arm64. The best qualified result is recorded
below with the measured gap to the done criteria; no threshold was
lowered and no gate was waived.

- **Track J**: two accepted CLIMB landings improved the incumbent from
  the recorded S = 0.7463 to a milestone-gated **S = 0.7258**. M4
  (S ≤ 0.25, every kernel ≤ 0.45) is missed by ~2.9×; the remaining
  cost is boxed-value churn into container storage and boxed locals,
  which requires a frame-representation change, not a planner tweak.
- **Track C**: Serc is feature-complete and byte-parity-correct, but the
  locked C3 cold-compilation gate REJECTED (6–16% wall regression) and
  the bounded compiler-optimization space is measured exhausted.

### Tested source and environment

- CPython source commit: `e891a9117985888df3e5d48145cf07ddda098335`
  (3.16.0a1+dev); lane `main` at closeout `0748280`.
- Host: Apple M1 Max, macOS 27.2.1 (26B131), arm64, 10 cores.
- Toolchain: Xcode 27.0, Apple clang 21.0.0, SDK 27.0; pinned Rust
  `nightly-2026-09-15-aarch64-apple-darwin` (rustc 1.100.0-nightly
  574ff7d98); `-mcpu=apple-m1`, deployment target macOS 27.0.
- Linux evidence: **not collected** — deferred until the owner supplies a
  native Linux host.

### Runnable variants (same epoch, verified build reports)

| Build | Flags | Report |
| --- | --- | --- |
| `perf-rust` | none | `rust-cpython/results/perf-rust.json` |
| `perf-rust-jit` | `--jit` | `rust-cpython/results/perf-rust-jit.json` |
| `perf-rust-jit-aa` | `--jit` (A/A twin) | `rust-cpython/results/perf-rust-jit-aa.json` |
| `perf-rust-serc` | `--serc` | `rust-cpython/results/perf-rust-serc.json` |
| `perf-final-jit-serc` | `--jit --serc` | `rust-cpython/results/perf-final-jit-serc.json` |

All five were rebuilt clean at `0748280` (source-identical to the frozen
revision; only campaign docs changed since `285f619`). Rebuild:
`python3 rust-cpython/perf.py build --name <name> [--jit] [--serc]` from
the lane root.

### Measured results (all quiet-host, `perf-gate-ready` clean both sides)

- **M4 milestone gate** (`perf-rust-jit` @ `285f619` vs `perf-climb-ref`):
  - `S = 0.7258` — `20261118T013628Z`, `--jit-set --runs 5`, pooled-median
    kernel-CPU geomean over all 16 training kernels.
  - Held-out `0.8368` ≤ S^0.5 = `0.8519` — passes
    (`20261118T021917Z`, `--jit-heldout --runs 5`).
  - Guard gate REJECT under unchanged policy (`20261118T023948Z`):
    `django_wsgi_request` wall 1.016 in all five runs (cpu 0.979
    improved) — the same wall-only django signature recorded at M0.
  - Memory within OD4: peak RSS pooled medians 1.014–1.073× on the
    training set and down to 1.006× on held-out (< +8%); JIT code bytes
    ≤1.28× M0.
  - Per-kernel cpu: nbody 0.554, scimark_fft 0.534, scimark_lu 0.564,
    scimark_sparse 0.564, scimark_sor 0.578, scimark_monte_carlo 0.731,
    spectral_norm 0.691, richards 0.648, raytrace 0.818, float 0.836,
    fannkuch 0.853, chaos 0.860, nqueens 0.887, deltablue 0.902,
    hexiom 0.907, go 0.915. No kernel ≤ 0.45.
- **Accepted landings since the recorded 0.7463**: the eval-9 three-layer
  bundle (store-subscript, array index unboxing, list-read unboxing;
  merged `574946e`) and `2f6284b` post-specialization `_CHECK_VALIDITY`
  pruning (climb-step ACCEPT, point 0.9977, nbody `_CHECK_VALIDITY`
  samples 10.23% → 4.35%).
- **Suite matrix** (52,102 tests per variant, this epoch):
  `perf-rust` 4 files/5 failures; `perf-rust-serc` identical under
  `--all` and `--all --serc-verify`; `perf-rust-jit` and
  `perf-final-jit-serc` identical 5 files/24 failures under both modes —
  the documented known-red set plus `test_capi`'s 19 superseded
  `test_opt` uop-shape assertions.
- **Combined build**: all 16 training-kernel digests equal
  `perf-rust-jit` (`20261118T035428Z`); differential fuzz seeds 0–2
  clean on both JIT builds (2,000 programs/seed, 0 mismatches,
  ~97.4% JIT coverage); `test_serc_compile.py` 11/11 on both Serc builds.
- **C3**: cold-compilation gate REJECTED (`20261117T025435Z`) —
  cold_python_startup 1.157/1.082, cold_import_django 1.122/1.115,
  compileall_source 1.164/1.060 wall/cpu; OD15 footprint INCONCLUSIVE
  (`20261117T033603Z`). C1/C3 census correctness carries from `fb131e1`
  (30,318 cases, zero mismatches); `serc-overlay` changed only by
  env-gated `SERC_PROFILE` timers since.

### Measured ceilings (why the plateau is architectural)

- JIT remaining hot costs are boxed-value churn: `_STORE_SUBSCR_LIST_INT_FLOAT`
  ~19% and fused-float local stores ~16% of nbody JIT samples require
  boxing into `PyListObject`/`GETLOCAL` storage; unboxed locals need a
  frame-representation change with materialization at `f_locals`,
  deopt, calls, and observation points.
- `_CHECK_FUNCTION_VERSION` cannot be dependency-hoisted: function
  versions have no globals-style executor dependency mapping.
- Guard/bookkeeping residual is ~1% aggregate; `_SET_IP` already optimal.
- Serc `pipeline-optimize` ~27% of compile is interleaved
  correctness-required transformation/ownership machinery; `parse` ~29%
  is third-party; measured bounded probes (rules pre-filters, scan
  consolidation, Py316 linear-path relaxation) were all sub-floor or
  bytecode-mismatched.

### Measured gaps to the done criteria

- **M4 unmet**: S = 0.7258 vs ≤ 0.25; needs ~66% further CPU reduction.
- **C3 performance unmet**: cold-compilation ACCEPT and OD15 footprint
  ACCEPT both failed on the locked set.
- **Guard wall signature**: `django_wsgi_request` wall ~1.016 persists
  across epochs; unattributed, cpu improves.
- **Combined performance qualification unmet**: correctness fully
  evidenced; no accepted Serc performance result exists to qualify a
  combined score.

### Limitations

- All evidence is native macOS arm64 on one M1 Max; no Linux data.
- The all-metrics gate still shows layout-sensitive ~0.4–1% offsets on
  unrelated workloads for different-binary candidates; the repaired
  harness (paired-RSS fix, cluster bootstrap, CALIBRATION-OK) resolved
  the shared blocker but sub-floor candidate effects remain real.
- Serc cold compilation is 6–16% slower wall / up to +37% peak RSS than
  the C compiler path on the locked workloads.

### Owner queue / smallest unblock actions

1. **JIT unboxed locals / frame representation** — the only identified
   mechanism with material headroom; multi-session research project
   with deopt/observability/refcounting obligations.
2. **Serc pipeline fusion** — research-grade; the Py316 linear-path
   port has since landed (see post-closeout note below).
3. **django_wsgi_request wall signature** — ~1.6% wall regression with
   improved cpu; needs attribution before it can be waived or fixed.
4. **Linux** — deferred until a native Linux host is supplied.

### Post-closeout note (2026-11-18, `5c64210`)

After the plateau closeout, the Py316 linear-assembler port was completed
and landed as an incremental Serc compiler improvement. The two required
fixes: `LOAD_COMMON_CONSTANT` lowering plus `co_consts` compaction on the
linear item stream, and Py316-faithful fusion (`fuse_borrowed_superinstructions`
after borrow-load analysis instead of pre-3.16 `fuse_pair`). Verified by a
clean 30,318-case census (0 mismatches, identical totals to the incumbent
census), per-file marshal parity at -O0/-O1/-O2, and `test_serc_compile`.
Measured: -5.3% Serc compile-phase time on 907 django modules (~50% of
code objects now take the linear path); ~0.6-1.4% improvement on the
locked C3 workloads and -1.2% on pure `compile()` timing — real but at the
practical floor end-to-end. **C3 remains REJECTED**; the gap narrowed, not
closed. `perf-rust-serc` was rebuilt at `5c64210`.

### Evidence manifest

Raw receipts live under `rust-cpython/results/perf-bench/<UTC>-<a>-vs-<b>/`
(`verdict.json` plus per-round samples) and build reports under
`rust-cpython/results/*.json`; lane logs under `rust-cpython/logs/`.
Key receipts: `20261118T013628Z` (S = 0.7258), `20261118T021917Z`
(held-out), `20261118T023948Z` (guard), `20261118T035428Z` (combined
digests), suite logs `logs/perf-*-all*.log`, fuzz receipts in session
records. The campaign ledger `docs/CAMPAIGN-RUST-CPYTHON-JIT.md` carries
the full row-level history, per-candidate correctness/review evidence,
rejected probes, and measured ceilings.
