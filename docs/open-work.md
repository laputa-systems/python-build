# Open work

Remaining work after the production closeout and Rust campaign were retired.
Priorities: P1 blocks a production release, P2 is important, P3 is worth
doing, P4 is optional. A debt entry is not success.

## Production

- [ ] **P1 — Linux x86-64 notices.** Produce a Linux x86-64 archive that lists
  the bundled notices and `SOURCES.md`. Berkeley DB 6.0.19 is Sleepycat,
  links only into the Linux `_dbm` module, and stays (see
  [product-targets.md](product-targets.md)). macOS arm64 and Linux arm64
  notices are recorded in git history.
- [ ] **P1 — Linux wave (L).** At the release-candidate revision, build and
  qualify `x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl` on
  native hosts, and collect the P1, P3, and P4 CI evidence below.
- [ ] **P2 — reproduction.** Reproduce Linux x86-64 from two clean builds and
  collect two CI release runs. macOS and Linux arm64 reproduce compiled
  content only at an unchanged build path; the builder path is baked into
  sysconfigdata, pkg-config files, `python-config`, `config.c`,
  `build-details.json`, and some extension images. Removing that path
  dependence is a separate change, not a prerequisite.
- [ ] **P2 — crash-consistent publication in CI.** A release run must publish
  and assemble through the immutable generation and `ACTIVE` switch.
- [ ] **P2 — TLS audit.** Complete the human or external review of verification
  and the Rust/C/Python FFI, the seven remaining PEM/DER loading
  differences, and a larger fuzz corpus for SNI, socket shutdown, and
  subinterpreters. Details live in `backend/`.
- [ ] **P1 — final release gate.** Require P1-P5 receipts, the Linux wave, a
  fresh three-platform qualification at the release revision, exact
  uv-installed archive checksums, source and tag identity, full failure
  accounting, and no stale production claims.

## Rust-for-CPython lane

- [ ] **P2 — quiet macOS performance confirmation.** Re-run the matched a1 gate
  without host noise. The last accepted runs were `quiet=no`; the only
  rejected row was `python_startup` peak RSS at 1.027 against the 1.01 floor,
  and `django_template_realistic` CPU read 1.126 in the noisy run. Decide
  whether the startup RSS residual is accepted or attacked.
- [ ] **P3 — Linux timing pass.** The integrated Linux timing pass is parked.
  Run it when the host is quiet, with the same matched control.
- [ ] **P3 — S5 route comparison on macOS.** The private compression routes were
  kept on Linux evidence; re-run the private-versus-upstream-compression
  comparison on macOS if the route decision is revisited.
- [ ] **P3 — memory debt re-measurement.** Re-measure the stale module and
  workload memory map under the current verdict policy before quoting any
  old ratio.
- [ ] **P3 — `_fnmatch_rs` cache.** Compare its B-tree, eviction queue, and
  spinlock cache against a maintained hash cache; no ordered workload needs
  the current structure.
- [ ] **P2 — shared hash and HMAC engine.** Sharing the pure engine with
  production `backend/hashing` is authorized (OD13) and still open. The
  production build's own gates apply.
- [ ] **P3 — source TOML pilot.** The deser pilot lost on peak RSS and private
  code size; revisit only with a different, streaming mechanism.
- [ ] **P3 — `compileall_source`.** Its output still differs from control, so it
  is not timed. Fix the difference before using it.
- [ ] **P3 — J8 register-cache allocator.** The timing gate was inconclusive
  (kernel wall geometric mean 0.9993). It adds a DP allocator to the overlay
  `Python/optimizer.c` and forbids voluntary 2→0 and 3→0 spills because those
  crash for an unisolated reason. Recommendation: revert it unless the crash is
  explained and a larger win appears. Owner decides.
- [ ] **P2 — macOS arm64 JIT.** Nothing JIT ran on macOS. Run the
  `docs/CAMPAIGN-JIT.md` "Needs environment" commands: Mach-O stencils with
  LLVM 23.1.4 (J1M), `perf.py profile --tool sample`, and JIT code residency.
  Packaging a JIT build would need `com.apple.security.cs.allow-jit`.
- [ ] **P3 — JIT register-cache allocation.** Spilling from cached depth 2 or
  3 to 0 when the uop could run at its current depth crashes `test_pathlib`,
  `test_tarfile`, and `test_profiling`, and the cause in the tier-2 code
  generator is not isolated (bisect in `docs/CAMPAIGN-JIT.md` "Debugging
  methods"). The allocation forbids those two transitions. Find the cause, then
  weight spills by distance instead of the special case.
- [ ] **P3 — upstream tier-2 findings on 3.16.0a1.** A function that started
  function-entry tracing and then runs under `sys.settrace` leaks one allocator
  block per function with the JIT on (repro: `def f(): return 1`, 8 calls,
  `settrace`). `test_capi.test_opt` and `test_monitoring` fail from the second
  `-R` repetition because the GC invalidates cold executors. `test_optimizer`
  leaks 6 references per run with or without the JIT. All reproduce without
  the Rust overlay. The pystats abort in `effective_trace_length` is fixed in
  `overlay/Python/optimizer.c`.
- [ ] **P3 — JIT sanitizer coverage.** ASan plus the JIT builds from the locked
  archive's runtime and passes 15 of 16 test files (`docs/CAMPAIGN-JIT.md`
  "Debugging methods"). Run the wider suites and the pydebug `-R` files under
  ASan, and add UBSan, if the JIT is ever promoted past the lane.
- [ ] **P4 — beyond the first assignment.** A speculative or optimizing tier,
  unboxed floats and ints in traces, and escape analysis were out of scope.
  The cost table in `docs/CAMPAIGN-JIT.md` ranks type guards (10.5% of
  executed uops), `_SET_IP` (9.6%), and `_CHECK_VALIDITY` (8.1%) first.

Linux arm64 is not implemented in the lane and must not be described as
qualified. PGO stays deferred. Do not relax suite or benchmark thresholds to
hit a score; a stopped or blocked run is incomplete, never a completed goal.
