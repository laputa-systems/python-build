# Rust-for-CPython campaign

## Status

Goal: a faster CPython 3.16 research interpreter that has less private code
to trust. The campaign builds shared runtime infrastructure, deletes private
implementations, and proves wins on application workloads with optimized
builds. Route count is no longer a goal. D1 coverage (71 routes) and D2
upstream parity were accepted in 2026-10 and remain the behavior baseline.

Course change 2026-10-31 (owner, after an external review of `d17607f`):
the per-module, memory-first hill climb is closed. That ledger is preserved
exactly at `git show d17607f:rust-for-cpython.md`. It covers memory
batches 11-33, U0-U8 parity records, and the decision log. Raw verdicts stay
in the ignored `rust-cpython/results/` directory.

Host: macOS arm64 for the optimized performance harness. The deferred
Linux x86-64 coverage proof ran on 2026-10-31 in the locked Ubuntu 24.04
userland (see Status and Baseline).

Last measured map (batch 33, `perf-rust@9f254a3`, stale). Old verdict
policy: 42 MET, 15 OVER and 14 UNCLEAR module memory rows. Replayed under the
S2 policy from the stored intervals: 33 MET, 15 OVER and 23 UNCLEAR. Nine
rows that the old median test called MET have an interval upper bound above
1.01. Six workload peak-RSS regressions against control, old policy:
`python_startup` 1.046x, `django_wsgi_first_request` 1.026x,
`django_orm_10k` 1.016x, `django_template_realistic` 1.016x,
`django_asgi_request` 1.016x, `django_wsgi_request` 1.015x. Those were not
replayed: the stored workload `verdict.json` keeps only old classes and point
ratios, without `absolute_change`, `noise_allowance` or `baseline.median`.
Re-measure them before quoting a decision. No CPU work has run.

Slices on `m1-scaffold` (never merged into `main`; the owner decides):

- S1 done. `_bz2_rs` lost its scoped allocator; `_fractions_rs` and
  `_fnmatch_rs` keep their allocators, now pinned by `GlobalAlloc` contract
  tests. `_fnmatch_rs` is a close call (+65 KiB, +9 percent retained with
  libc malloc; the heap is 190 unsafe lines). The bz2 load row from `500b5b0`
  stays void until re-measured. Parked `lane/*` branches are evidence only.
- S2 done: verdicts read improved, regressed, within budget, or
  inconclusive; decisions read REJECT, INCONCLUSIVE, ACCEPT, or
  WITHIN-BUDGET.
- S3 done: `perf.py cargo --name N -- <cargo args>` runs cargo over an
  assembled perf build. It syncs overlay files without rebuilding the stage,
  so the build report's overlay digest goes stale and the build is no longer
  a clean baseline; run `build --incremental` or rebuild before benching.
- S4 done. `cpython-binding` (one `ffi` module checked against `cpython-sys`,
  buffers, bytes writer, module and outcome helpers, abort-on-panic) and
  `cpython-deflate` (one DEFLATE engine; `_zip_rs` is folded into
  `_zlib_rs`) are in use. The acceptance grep lists only the shared crate.
  Overlay Rust lines: 96,662 (100,510 at `d17607f`). `test --all` on the
  merged tree: 50,158 run, only the Baseline known-red files fail
  (`test_ssl`, `test_support`, `test_unicodedata`) plus one load-flaky
  `test_concurrent_futures` case that passes alone. A job started from a
  background shell inherits SIGINT ignored and fails nine signal suites
  falsely; reset SIGINT first. About 25 modules still wrap the bindgen
  `PyModuleDef_Slot` and about 15 std modules keep private `extern "C"`
  blocks for functions `cpython-sys` provides; neither is in the acceptance.
- S5 decided, private routes kept. The pinned fork has no
  `--with-zlib=zlib-rs` or `--with-bzip2=bzip2-rs`; its configure takes
  `ZLIB_CFLAGS/LIBS` and `BZIP2_CFLAGS/LIBS`, and `perf.py build
  --upstream-compression` feeds those from `cpython-libz-rs` and
  `cpython-libbz2-rs`. Matched suites are green on both routes, but
  `zlib_decode_1m` peak RSS reads 1.085 to 1.131 (regressed) and the bz2 load
  row 1.547, and timing is deferred (host busy), so the deletion condition is
  unmet. Re-decide on a quiet host with `bench --gate`.
- S6 lost. The deser TOML pilot regressed peak RSS in every run (1.072) and
  grew `_tomllib_rs.so` by 37 percent, because `deser-toml` builds the whole
  document before emitting events. `_tomllib_rs` is unchanged; the pilot is
  on `s6-toml-pilot`. The `catalog_toml_load` workload landed. JSON and plist
  do not follow. `_tomllib_rs` hardcodes key and nesting limits that
  `tomllib` takes from the recursion limit.
- S7 done, accepted by the owner at the 1 percent floor. `b65843a` (urllib
  quote and unquote in one Rust call) reads wall 0.72, 0.78 and 0.87 and CPU
  0.81, 0.81 and 0.88 on `catalog_request_path`, `catalog_search_form` and
  `catalog_url_normalize`, improved in every run of a 6-run matched gate with
  no guard regressed. Peak RSS reads 0.998 to 1.005 with upper bounds up to
  1.014, so the gate decision is INCONCLUSIVE; the owner accepted that. On
  the rebased tree the differential test passes (14), a subinterpreter call
  returns `None`, and `test --all` shows only the Baseline known-red files
  (run=50,158, failures=9). The gate numbers were measured before the
  rebase onto the binding and deflate fixes; the rebased tree was not
  re-gated.
- Integrated gate. `main` against `m1-scaffold` at `990f7fd` (`bench --gate
  --all-workloads --runs 4`, matched clean builds, quiet) read REJECT on
  `rust_base64_small` wall 1.234, `zip_read_wheel` peak RSS 1.017 and
  `zlib_decode_1m` peak RSS 1.019. Causes: the binding's `BytesWriter` made a
  separate allocation and copy, nothing inlined across the crate boundary,
  and `BytesSink` grew by a further quarter on top of the engines'
  doubling. Fixed in `cpython-binding`; the second `ExactBytesSink` was
  folded away. At `b605237` the same gate reads INCONCLUSIVE with no regressed
  row: `rust_base64_small` wall 1.012 [0.998,1.029], `zip_read_wheel` and
  `zlib_decode_1m` peak RSS 1.000 and 1.001, and no application row has an
  interval wholly above 1.01. Wall intervals straddle the 1 percent floor on
  this shared host, so no slice has an ACCEPT. The affected suites pass
  (7,279 run, 190 skipped).
- Linux x86-64 proof, 2026-10-31, M1 head `05cbbc3`. The host is Alpine
  3.25 x86-64 (kernel 7.2.9, 32 cores); the lane ran in an Ubuntu 24.04
  userland container built from snapshot.ubuntu.com 20260401T000000Z with
  the exact `linux-toolchain.lock.json` package versions, glibc 2.39, the
  locked LLVM 23.1.2, and rustup nightly-2026-09-15 gnu. Execution is
  native x86-64, not emulated, but the Ubuntu host is a container, not a
  native Ubuntu installation. `build.py test --all --jobs 4` reads
  run=50,745, failures=10, skipped=2,675, 496/505 files, nine resource
  denials. The only failed files are the accepted Baseline known-red:
  `test_ssl` (5 failures plus 8 errors, RTLS policy gaps), `test_support`
  (14 errors, hashlib shim), and `test_unicodedata` (5 failures, fork test
  file predates Unicode 18). The 2026-09-28 Linux host's VSOCK failure is
  absent; `test_socket` passes here. `test_re`, `test_locale`, and
  `test_str` pass; the U2 run repeats the same 5 `test_unicodedata`
  failures (run=450, skipped=90). The old Linux baseline at `4710c23` read
  50,782/2,828 with only the host VSOCK failure; the M1 head matches the
  accepted known-red set, not that host-VSOCK-only checkpoint. `perf.py`
  still refuses Linux hosts, so the performance queue below remains
  macOS-only.

Needs a quieter host: more `bench --gate` runs for S5, S7, and the borderline
wall rows `rust_base64_small` and `gzip_extract_1m`. The bench `peak_rss` metric is sampled and
noisy below 100 ms.

M1 check: S1-S7 landed or recorded (S6 a loss), overlay Rust lines 96,662 against
100,510, and three catalog workloads improved on an optimized build. The
Linux x86-64 D1/D2 and U2/U8 proof at the M1 head now matches the accepted
known-red set. No slice gate reads ACCEPT because wall intervals straddle the
1 percent floor on this shared host; the owner accepted S7 on that basis.

Resume: "Resume the Rust-for-CPython campaign in rust-for-cpython.md with
the orchestrate skill." First command: `python3 rust-cpython/perf.py status`.

## Charter

- Decide and keep going within the execution grant below. Park an item
  that needs authority outside the grant, with its evidence, missing
  prerequisite, and retry condition.
- Hard stops park the item: an irreversible or outside-the-repo action, a
  boundary below that forbids it, or a loop that cannot check anything.
  Pushing is always forbidden.
- Fix egregious problems met on the way, in their own commit. A focused
  loop slower than 10 minutes gets one bounded repair. An application
  workload regressed more than 2x parks the slice as a blocker.
- Correctness before speed. No slice lands with a red relevant suite.
- One writer by default. Parallel lanes only for the disjoint pairs named in
  **Sequence**, in worktrees at `.work/wt/<lane>`, under the global
  `orchestrate` skill and its worktree lifecycle. This doc names no models.
- Builds, suites, and measurements share the host through the `perf.py`
  host lease. [rust-cpython/README.md](rust-cpython/README.md) documents the
  harness.
- Every commit is a Conventional Commit with the area as scope. The item ID
  goes in the body. Code comments never cite this doc.
- Rewrite **Status** in place. Git is the log.

### Engineering contract

1. Never violate a Rust contract (`GlobalAlloc`, aliasing, initialized
   memory), an allocator contract, or a CPython C-API contract (reference
   ownership, attached thread state, interpreter lifetime) to improve a
   metric. A violation found is a correctness defect, fixed before any
   further optimization of that route.
2. Once S4 lands, Python object, type, and module layouts, refcount
   constants, `Py_buffer` wrappers, and panic handlers exist only in the
   shared binding crate. Until then, new code adds no new copy.
3. A new custom allocator, synchronization primitive, parser, or cache
   needs a comparison with maintained alternatives and the semantic reason
   in the commit body.
4. A consolidation deletes implementations or removes a boundary. A shared
   abstraction that leaves every old copy in place is unfinished.
5. A fast path falls back before any observable side effect, or carries a
   proven continuation. User callbacks never run twice.
6. A performance claim names the exercised path and the application
   workload it moves.
7. A new or changed unsafe kernel gets targeted differential or fuzz tests,
   plus Miri or a sanitizer where either can run. CPython suites stay
   required but do not prove Rust soundness.
8. A route has no tenure. A Rust adapter may be merged, bypassed, or
   deleted when a shared, upstream, or maintained implementation keeps its
   line in [rust-cpython/ROUTES.md](rust-cpython/ROUTES.md) green within
   budget. A Rust engine behind an upstream C wrapper still counts.
9. Crates for this lane pin exact versions in the overlay `Cargo.lock` and
   record their license in the route's `THIRD_PARTY_LICENSES.md`. No
   OpenSSL, no production dependency, no handwritten assembly or SIMD
   (maintained crates may use SIMD internally).

## Decisions

Owner (2026-10-31; overrule by saying so):
- OD6: change course to shared infrastructure, deletion, and measured
  application wins. Supersedes the 2026-10-29 sequencing and OD2's order.
- OD7: memory is a per-change budget, not a global prerequisite for CPU
  work. Supersedes "R1 before R2" and the memory-first lane rule.
- OD8: route tenure ends (contract rule 8). Supersedes prescribed P2 ("keep
  every Rust route") and the old route-removal ban.
- OD9: work on the macOS host only; Linux gates wait until feature work is
  done.
- OD10: this lane no longer waits for production P1-P6. Production
  qualification still gates production releases (plan.md).
- OD11: delegation uses the global `orchestrate` skill. The lane-specific
  coordinator and climber skills, agents, model pins, and agent hook are
  removed.
- Kept: OD3 (no redesign around the proposed 3.17+ upstream Rust API;
  upstream compression routes in the shipped 3.16 fork are not that)
  and the OpenSSL-free rule.

Prescribed (reversal needs counter-evidence):
- PR1: marshal keeps occurrence-based reference tagging (U1). Reversal: a
  byte differential against pristine that fails.
- PR2: ICU4X 2.4.0 with Unicode 18 data stays (U2).
- PR3: S4 picks its binding source on evidence. Candidates: the fork's
  bindgen `cpython-sys`, which matches the fork headers but links `std`
  (about 400 KiB mapped per extension, measured in `_glob_rs`), and
  `pyo3-ffi`, which is maintained. The review reports `no_std` support and
  a 3.15 version ceiling for `pyo3-ffi`. Neither is checked here.
  Fork-specific declarations live in one crate with a layout check against
  the fork headers.
- PR4: no `bbtree`. Its concurrent tree documents racing reads that are
  undefined behavior, and no workload here needs key order. The
  `_fnmatch_rs` `PatternCache` (B-tree plus eviction queue plus spinlock) is
  compared against a maintained hash cache instead. Reversal: an ordered
  workload plus a sound implementation.
- PR5: deser is a bounded pilot (S6), not a mandate. Never `unsafe impl
  Send` a Python reference to satisfy its sink bounds.

Delegated (executor decides within the bound, records the call in Status):
- G1: lane width per `orchestrate`, within `perf-gate-ready -s` headroom.
- G2: tactics inside a slice's owned files.

## Baseline

Known red on accepted main (macOS arm64, debug, `python3 rust-cpython/build.py
test --all --jobs 4`, 50,158 run): `test_ssl` 4 failures plus 6 errors
(RTLS policy gaps), `test_support` 14 errors (hashlib shim), and
`test_unicodedata` 5 failures (fork test file predates Unicode 18). Seen
intermittently: `test_audit` (parallel `.pyc` churn), `test_os`, and
`test_mailbox`. Nine files are resource-denied: `test_curses`,
`test_peg_generator`, `test_smtpnet`, `test_socketserver`,
`test_urllib2net`, `test_urllibnet`, `test_winsound`, `test_xpickle`, and
`test_zipfile64`.

Linux x86-64 at the M1 head (2026-10-31, Ubuntu 24.04 container on the
Alpine 3.25 x86-64 host, exact `linux-toolchain.lock.json` packages):
`build.py test --all --jobs 4` reads run=50,745, failures=10,
skipped=2,675, 496/505 files, nine resource denials. The three failed
files are the same accepted known-red: `test_ssl` (5 failures plus 8
errors here against 4 plus 6 on macOS; all RTLS policy gaps),
`test_support` (14 errors, hashlib shim), and `test_unicodedata` (5
failures, fork test file predates Unicode 18). `test_socket` passes; the
2026-09-28 Linux host's VSOCK loopback failure is host-specific and
absent here. The 2026-09-28 Linux baseline at `4710c23` read
50,782/2,828 with only that VSOCK failure; the 37 fewer runs and 153
fewer skips at the M1 head are host and overlay differences, while the
failed-file set is the parity result.

Loop timings (macOS arm64): focused suite 20-90 s; clean perf build 436 s
at `--jobs 8` (2026-10-31), coverage build 10-20 min; `test --all` about
9 min; verdict unit tests (`python3 -m unittest discover -s
rust-cpython/tests -p 'test_perf.py'`) about 2 s. Linux x86-64 (Ubuntu
24.04 container, 32 cores): clean debug build about 9 min at `--jobs 16`;
`test --all --jobs 4` 20 min 19 s; the focused U2/U8 suites 26.5 s. The
locked LLVM archive verification adds several minutes to a clean build.

Findings that bind design (from the `d17607f` ledger):
- Startup and Django RSS debt is dyld fixup-chain residency, roughly
  proportional to the 8 MB of Rust text. Trimming libpython's export list
  moved nothing (vmmap showed identical sections). Less code, LTO, or a
  smaller static carrier are the remaining levers, so consolidation is the
  route to that debt.
- `no_std` variants cut extension load footprint (decimal 0.66-0.78x) but
  never replicated to an acceptance. A `cdylib` plus `rlib` crate without
  `std` needs its own global allocator and panic handler, which blocked
  `_base64`. The shared layer must solve this once.
- Closed mechanisms (no repeat without new evidence): SSL process-wide
  provider `OnceLock`, TLS identity cache, and `Arc`-shared chains; HMAC
  state behind a `_hashlib_rs` capsule (load 2.77x); datetime stack
  formatters and packed `c_long` fields; ElementTree single write,
  streaming, slabs, and bytes sink; `csv_core` writer; libpython export
  trimming.
- Module rows for `_strptime`, `warnings`, `html.parser`, `uuid`,
  `decimal`, `sqlite3`, `bisect`, and ElementTree working peak flip between
  verdicts on page-quantized values. Treat them as the measurement floor.
- U3 kept the private compression modules because building `libz_rs` and
  `libbz2_rs` for upstream's `--with-zlib=zlib-rs` and
  `--with-bzip2=bzip2-rs` was outside that grant. Performance never decided
  it. The lane already ships those engines (`libz-rs-sys` 0.6.7,
  `libbz2-rs-sys`).
- `_bz2_rs` `OutBytes::spare()` hands the codec a `&mut [u8]` over the
  uninitialized storage of a new `bytes` object. S4's writer wrapper
  replaces it.

## Sequence

Milestone M1 is S1-S7. Disjoint pairs that may run in parallel: S1 with S2,
S1 with S3, and S2 with S3. S5 and S4 share the compression routes, so S5's
keep-or-delete call comes before S4 migrates them.

- **S1 - allocator contracts.** Owns the global-allocator code in
  `overlay/Modules/_bz2_rs`, `_re_rs`, `_fnmatch_rs`, and `_fractions_rs`.
  The scoped `_bz2_rs` allocator, whose `alloc_zeroed` returned unzeroed
  memory for large requests inside codec calls, is deleted. Audit the other
  three against `GlobalAlloc`: `_re_rs` arena reuse and realloc,
  `_fractions_rs` `PyMem_Malloc` against the attached-thread-state rule, and
  `_fnmatch_rs`. Delete each allocator unless it is sound and a maintained
  alternative was compared. Acceptance: each touched route's suites from
  ROUTES.md pass on a perf build (`perf.py test --name N --suite ...`). The
  memory rows are recorded after S2 lands, for diagnosis only.
- **S2 - verdict policy.** Owns `rust-cpython/perf_verdict.py`, its use in
  `rust-cpython/perf.py`, and `rust-cpython/tests/test_perf.py`. Implements
  the outcomes in **Acceptance**. Required tests, each failing before the
  change:
  - one run with ratio interval `[0.8, 1.3]` reads inconclusive, not
    neutral;
  - two runs with intervals `[1.02, 1.08]` and `[0.99, 1.03]` read
    inconclusive, not neutral;
  - two runs with intervals `[0.95, 1.6]` and `[0.9, 1.5]` and pooled
    median 1.0 read UNCLEAR as a goal, not MET;
  - bench.py memory with `(absolute_change + noise_allowance) /
    baseline.median` at most the allowance reads within budget, and above
    it reads inconclusive.

  Keep `--memory-only` working, and keep calibration semantics (identical
  interpreters must not read improved, regressed, or unstable). Replay:
  re-decide the stored batch-33 `verdict.json` files under
  `rust-cpython/results/perf-bench/` with the new functions in a scratch
  script under `.work/tmp`, and put the replayed counts in Status.
  Acceptance: `python3 -m unittest discover -s rust-cpython/tests -p
  'test_perf.py'` passes.
- **S3 - Cargo workspace for development.** Owns a new `perf.py cargo`
  subcommand and its tests. `python3 rust-cpython/perf.py cargo --name N --
  <cargo args>` syncs changed overlay files into perf build `N`'s assembled
  source, using the incremental sync and its refusals, then runs the pinned
  toolchain's cargo offline. It runs in that source with the private
  `CARGO_HOME` and `PYTHON_BUILD_DIR` set as the build sets them, so
  `cpython-sys` bindgen resolves. Acceptance: on an existing build, `perf.py
  cargo --name N -- metadata --format-version 1 --offline` exits 0, and
  `-- clippy -p _bz2_rs --offline` and `-- test -p _bz2_rs --offline` run to
  completion. The subcommand's unit tests in `test_perf.py` pass. The full
  CPython suite stays the integration gate. This command is the inner loop
  for Rust mistakes.
- **S4 - shared binding layer and bytes writer.** Prerequisites: S3, and
  PR3 decided. Owns a new overlay crate and the modules it migrates. One
  crate owns the ABI declarations and fork-only layouts (with a layout
  check), owned versus borrowed references, `Py_buffer` acquire and
  release, module state, panic policy, and one result convention: success,
  unsupported fast path, or Python exception already set. It also wraps
  CPython's `PyBytesWriter` (in the pinned fork) for output. Migrate the
  10 `STATIC_IMMORTAL_REFCNT` modules first, then the compression routes
  that survive S5, then the rest. Delete each private copy as its module
  migrates. Acceptance: migrated routes' suites pass; `rg -l 'struct
  BorrowedBuffer|const STATIC_IMMORTAL_REFCNT|#\[panic_handler\]'
  rust-cpython/overlay/Modules` lists only the shared crate; application
  rows within budget on matched optimized builds; overlay Rust lines
  (`fd -e rs . rust-cpython/overlay | xargs wc -l`; 100,510 at `d17607f`)
  fall.
- **S5 - upstream compression wrappers.** Owns `rust-cpython/build.py` and
  `perf.py` configure inputs plus the compression adapters. Build
  `libz_rs` and `libbz2_rs` from the pinned crate sources, feed them to
  configure through `ZLIB_CFLAGS/LIBS` and `BZIP2_CFLAGS/LIBS`, and compare against the
  private `_zlib_rs` and `_bz2_rs` routes on matched builds. Delete each
  private adapter whose upstream route keeps its ROUTES.md suites green
  within budget. Remaining gzip, zip, and zipimport paths keep only
  machinery that earns its cost (`_zip_rs` is now folded into `_zlib_rs`). Acceptance: the compression lines of ROUTES.md green on
  the chosen route; a recorded matched comparison.
- **S6 - deser TOML pilot.** Prerequisite: S4. Owns `_tomllib_rs`. Prove a
  sound ownership bridge from deser's event sinks to Python objects first;
  never an `unsafe impl Send`. Keep `parse_float` callback cases on the
  current path until equivalence is shown. deser-toml builds its whole
  document before emitting events, which can reorder syntax errors against
  callbacks. Acceptance: `test_tomllib` and `test_inspect` green, plus CPU
  and RSS against the current `_tomllib_rs` on a TOML workload. If the pilot
  loses, record why and stop. JSON and plist follow only on a win.
- **S7 - one measured runtime improvement.** Prerequisites: S2 and a
  profile. Start from a cost shared across many operations: borrowed bytes
  instead of copies, native Unicode widths instead of forced UTF-8,
  vectorcall instead of argument tuples, bulk work inside one kernel,
  import closures and lazy resources (the `http.client` and
  `urllib.request` lazy-ssl wins are the pattern), or an interpreter
  hotspot such as the type-cache fixture in
  `overlay/Objects/tests/typecache_content.md`. Acceptance: an application
  workload improved on matched optimized builds, every other application
  row within budget.

Backlog after M1, each needing its own reason: share the pure hash and HMAC
engine with production `backend/hashing` (crosses the production boundary,
so the owner decides); shrink the `cpython-rust-staticlib` carrier, keeping
source sharing, code sharing, and mapped-page sharing as separate claims;
the `_fnmatch_rs` cache comparison (PR4); JIT and tail-call interpreter
builds as upstream comparators; the U7 source rebase.

## Acceptance

Each metric gets one of four outcomes from paired runs. S2 implements them.
Until S2 lands, report raw intervals and call nothing neutral.
- improved: every run's 95% ratio interval lies entirely below 0.99.
- regressed: every run's interval lies entirely above 1.01.
- within budget: every run's interval upper bound is at most
  `1 + allowance`. For bench.py memory medians the upper bound is
  `1 + (absolute_change + noise_allowance) / baseline.median`.
- inconclusive: anything else, including runs that disagree.

Decisions: REJECT on any regressed metric or output mismatch; INCONCLUSIVE
when any evaluated metric is inconclusive; ACCEPT when a target improved and
every other evaluated metric is improved or within budget; WITHIN-BUDGET,
replacing NEUTRAL, when every evaluated metric is within budget and no
target improved. Goal rows against control use the same rule against the
1.0 ceiling: OVER when regressed, MET when within budget, BEYOND when every
interval lies below 0.9, UNCLEAR otherwise.

Allowances are predeclared and set by the owner, not a lane: 1% (the
existing practical floor) for application wall, CPU, and peak RSS. Module
rows are diagnostic, with the existing 64 KiB load and 256 KiB working-peak
floors. A slice that pushes a touched route's row past its floor explains
why in the commit body. Thresholds do not move to clear old debt.

A slice lands when:
- the complete suites for every touched route in ROUTES.md pass on a perf
  build, plus a subinterpreter probe when module init or state changed
  (`_interpreters.run_string()` returns `None`);
- the seven gate workloads (`GATE_WORKLOADS` in `perf.py`) and the slice's
  target workloads read improved or within budget, with matching outputs;
- for S4, S5, and S7, the result is confirmed on matched optimized builds.

A slice that deletes private code needs no speedup. It lands on these
conditions alone. Gates still need two runs. `test --all` runs after S4,
S5, and S7 and must show only the Baseline known-red.

M1 is done when S1-S7 are accepted or S6 is recorded as a loss, overlay Rust
line count is below 100,510, and one application workload is improved on
an optimized build.

## Needs environment

Linux proof (done 2026-10-31 at the M1 head; see Status and Baseline):
- D1 and D2 on native x86-64 Linux: `test --all --jobs 4` matches the
  accepted known-red set (3 failed files, 10 failures, 22 errors); the
  2026-09-28 host VSOCK failure is absent. The Ubuntu 24.04 userland is a
  container on the Alpine x86-64 host; execution is native, not emulated.
- U2 Unicode 18 and U8 `re` locale requalification: `test_re`,
  `test_locale`, and `test_str` pass; `test_unicodedata` repeats the 5
  accepted failures from the fork test file.
- Any source rebase (U7) on both hosts remains open.

Linux arm64 has no lane builder and is not claimed.

## Execution boundary and non-goals

Authorized: branches and worktrees under `.work/wt/`; perf builds on the
macOS host and coverage builds on the macOS host or the locked Linux
x86-64 userland; fetching pinned sources and pinned crates (rule
9); running CPython suites; editing `rust-cpython/overlay/`,
`rust-cpython/*.py` tooling and tests, `rust-cpython/README.md`,
`rust-cpython/ROUTES.md`, and this doc; deleting private routes under rule
8; adding a new workload in its own commit before the slice that uses it;
local commits.

Forbidden: pushing; changing production `buildsys/`, `backend/`, or the
3.15.1 product; adding platforms, production dependencies, or product
versions; OpenSSL in any route; network access during sealed builds;
editing `Lib/test/`; changing an existing workload or module kernel to move
a result (a kernel that misses its route's behavior is fixed in its own
commit first); timing claims from a busy host.

Non-goals: a redesign around the proposed 3.17+ upstream Rust API; a Linux
arm64 lane builder; a second plan, dashboard, or ledger; concurrent
`bbtree`; deser for pickle or marshal, which need CPython reconstruction
semantics.

Contract changes for the owner:
- Route deletion is allowed (OD8).
- Memory no longer gates CPU work (OD7).
- S2 changes `perf.py` verdict output: INCONCLUSIVE replaces NEUTRAL where
  evidence is weak, and WITHIN-BUDGET replaces NEUTRAL where it is bounded.
- The lane skills, agents, and hook are gone (OD11).

## Reference

- Pin: `rust-cpython/sources.lock.json` (`b812b4a`, 3.16.0a0). The pinned
  fork has `PyBytesWriter` (`Include/cpython/bytesobject.h`); overlay C
  users include `socketmodule.c`, `posixmodule.c`, `binascii.c`,
  `_io/bufferedio.c`, and `_codecsmodule.c`.
- Behavior register: `rust-cpython/ROUTES.md`. Harness: `perf.py`,
  `perf_modules.py`, `perf_verdict.py`, commands in `rust-cpython/README.md`.
- Static carrier: `overlay/Modules/cpython-rust-staticlib/Cargo.toml`
  (13 crates).
- Duplication at `d17607f`: 16 `BorrowedBuffer` definitions, 13
  handwritten `PyObject` layouts, 13 panic handlers, 10
  `STATIC_IMMORTAL_REFCNT` constants, 4 global allocators (`_bz2_rs`
  removed in S1), 11 `_PyBytes_Resize` users.
- Fast perf builds configure `--enable-experimental-jit=no
  --with-tail-call-interp=no` without PGO or LTO (`perf.py`).
- Production hashing: `backend/hashing/src/lib.rs` (RustCrypto engine) and
  `python_ffi.rs` (CPython frontend).
