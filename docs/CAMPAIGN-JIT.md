# JIT research campaign

## Status

- **Linux and macOS arm64 research closed 2026-11-01 on `campaign/jit`.**
  J0-J9 and the macOS handoff are done. `main` is untouched at `96f4112`.
  Do not push unless the owner asks. Other follow-ups are in `docs/open-work.md`.
- **Lane host** command: `.work/tmp/lane.sh <cmd>` wraps the `docker run` in
  **Lane host** (recreate it from that section). Builds kept: `perf-upstream`, `perf-rust`, `perf-upstream-jit`, `perf-rust-jit`,
  `perf-rust-jit-stats`.
- **D1 JIT builds with the pinned LLVM:** met (J1; stencils from the locked
  LLVM 23.1.2 clang, activation checked).
- **D2 kernel set:** met (J2; 16 kernels, wheel digest checked). Review found
  they were also selected by the `realworld` and `full` product suites; fixed.
- **D3 metrics:** Linux J3 and the macOS live mapping probe are complete.
  The macOS results and limits are in **macOS arm64 results**.
- **D4 Linux baseline matrix:** failed-file sets equal their siblings' (the
  RDS `testPeek` hang also hangs the no-JIT build), all 16 kernel digests equal
  across five builds. The four `--jit-set --gate` verdicts ran on 2026-11-01 on
  a quiet host (`perf-gate-ready` 0 before and after each; two runs each; wall
  only, the verdicts carry no CPU metric). Geometric mean of the 16 kernels' wall
  ratio, candidate/baseline:
  - `@control` vs `perf-upstream-jit`: REJECT, 0.806. Wall improved on 15 of 16
    (nbody 0.55, scimark_lu 0.55, spectral_norm 0.61; raytrace inconclusive).
    The reject is memory: peak RSS +2-9% and private +2-12% from the JIT's code
    regions and extra `libpython` text.
  - `@incumbent` vs `perf-rust-jit`: INCONCLUSIVE, 0.821. Wall improved on 14
    kernels, none regressed; memory within noise.
  - `perf-upstream-jit` vs `perf-rust-jit`: REJECT, 0.833 only because Rust zlib
    and gzip cut `zlib_decode_1m`/`gzip_extract_1m` wall to 0.11-0.15. Kernels
    are equal within noise; the rejects are `django_template_realistic` wall
    1.078, `import_django` wall 1.070 and +12-16% private, which `@control` vs
    `@incumbent` shows without the JIT.
  - `@control` vs `@incumbent` (no JIT): REJECT; the same `django_template_realistic`
    (1.077) and `import_django` (1.054) wall and +21% private. This is lane
    debt, not a JIT effect.
  - J8 (reverted, see D7): `perf-rust-jit` vs `perf-jit-opt`: INCONCLUSIVE, kernel wall geometric
    mean 0.9993; every kernel's interval sits inside 0.98-1.03 and memory is
    within budget. The allocator's 8.0% fewer spill uops is not measurable at
    the 1% floor, as the static estimate (about 0.6% of executed uops) predicted.
- **D5 hardening:** pydebug JIT build passes all 31 listed suites, including
  the candidate. `-R 3:3` is red on upstream causes that reproduce without the
  Rust overlay, and the candidate matches the baseline exactly. ASan builds
  together with the JIT on the first attempt (locked clang plus the archive's
  ASan runtime, `--with-address-sanitizer --enable-experimental-jit=yes`): 15 of
  16 files pass, and `test_capi` fails only `test_144068_daemon_thread_jit_cleanup`
  on a leaked `os.stat` float that also leaks with `PYTHON_JIT=0`, so it is not
  a JIT leak. python/cpython#160852 needs the tail-call interpreter, which the
  lane keeps disabled.
- **D6 hybrid verdict:** closed by the J6 rule; do not port (PD6). The first
  "blocked by OD1" closure was wrong: it read the lane's pruned prefix, not the
  archive. With the archive's members, the stock merge-base and the PR head both
  build with LLVM 23.1.2 and a scratch LuaJIT. The release archive omits
  `llvm/Plugins/PassPlugin.h` and ships no shared `libLLVM`; the header was
  fetched from llvm-project at `llvmorg-23.1.2` into scratch and the plugin built
  without LLVM libraries, so the fold pass was active. On a quiet host
  (`perf-gate-ready` 0 before and after, 5 interleaved runs, CPU time medians)
  the JIT-set geometric mean is 0.904 of stock, above the 0.90 bar (without the
  fold pass: 0.89 on a loaded host, so the pass added nothing measurable). Range
  0.59 `nbody` to 1.007 `deltablue`; all 16 digests equal; `test_capi.test_opt`
  and `test_jit_peephole` pass. The PR head also differs from the merge-base by
  new uops and optimizer changes, so this is not a pure backend comparison.
  Porting would also conflict with the lane contract (handwritten assembly,
  `Lib/test` changes, aarch64 relocations).
- **D7 one optimization:** tried and reverted. A whole-trace register-cache
  allocator (dynamic programming over cached depths, `5035638`..`c305179`) cut
  static spill and reload uops 8.0% and kept parity, but its gate against
  `perf-rust-jit` was INCONCLUSIVE (kernel wall geometric mean 0.9993), under
  the 1% floor as predicted. It also had to forbid spills from cached depth 2 or
  3 to 0, which crashed pathlib, tarfile, and profiling for a cause never
  isolated. The overlay keeps only the `effective_trace_length` fix that the
  pystats build needs.
- **D8 cost ranking:** met (J7). Type guards 10.5% of executed uops, `_SET_IP`
  9.6%, `_CHECK_VALIDITY` 8.1%, `_SPILL_OR_RELOAD` 7.5%; side exits 0.7%.
  Native sampling failed on Linux, so shares are uop shares, not CPU.
- **Review (`code-review high`, `main..campaign/jit`):** fixed the red
  `tests/test_benchmarks.py`, the kernels in product suites, and the no-JIT
  build failing on the regen interpreter. Left as is: the pasted `campaign-jit.md` goal at the
  repo root, and the earlier non-conventional commits `87e9e07` and `24d4943`.

## Charter

- Decide and keep going. Record a decision in **Decisions** only when a later
  slice depends on it. Rewrite **Status** in place after each accepted slice;
  never append logs or checkpoints.
- Within **Execution boundary**, proceed without asking. Park any item that
  needs authority outside it, with the cause and a retry condition. Pushing is
  forbidden.
- Hard stops park the item and do not end the campaign: an action outside
  the repo that the boundary does not list, a forbidden boundary, or a loop
  that cannot check anything.
- Work serially by default. Parallel lanes are allowed only for the pairs that
  **Sequence** names. Each lane gets a worktree at `.work/wt/<item>` following
  `orchestrate`'s worktree lifecycle, never `/tmp`. Point `TMPDIR` at
  `.work/tmp`.
- Integrate only green work. A slice ends on its acceptance command and the
  narrowest affected suite, run on the integrated tree. Run the steering
  checkpoint from `orchestrate`'s campaign reference after each accepted
  slice.
- A failing item gets its cause before any retry (`root-cause`). The limit is
  three attempts per item unless the item says otherwise. Then park it on
  `lane/<item>` with the cause and a retry condition.
- Fix egregious problems you meet on the way, in their own commit, when the
  cause is local. Examples: a harness command that is 2x slower than the
  timings in **Baseline**, a crash, or wrong output. Record anything else in
  `docs/open-work.md` and leave it.
- Parity before optimization. J8 is the only optimization item.
- Commits are Conventional Commits scoped to the workstream (`perf`,
  `bench`, `jit`, `docs`). The body names the item ID and the exact commands
  with their results. Code comments never cite this doc.
- Never relax a suite or verdict threshold. A stopped, blocked, or
  host-busy run is incomplete. It is never a met goal.

### Lane host (this machine)

The host is Alpine x86-64, where `rust-cpython/build.py doctor` fails because
the host is not Ubuntu 24.04. Run every lane command in the existing local
image `rust-cpython-linux:20260401` (id `sha256:701c4157…a87a25`). It is
Ubuntu 24.04 with the exact `linux-toolchain.lock.json` packages, glibc 2.39,
and rustup `nightly-2026-09-15`:

```sh
docker run --rm --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  -v "$PWD:$PWD" -w "$PWD" -e TMPDIR="$PWD/.work/tmp" \
  rust-cpython-linux:20260401 <command>
```

`unshare --user --map-root-user --net` works inside it (checked
2026-11-01). Run builds and `test --all` as background jobs; both outlast
10 minutes. If the image is missing, park every Linux item on **Needs
environment** and continue only with host-independent code slices.

### Timing evidence

`perf.py bench --gate` evidence counts only if `perf-gate-ready` exits 0
immediately before the run and `perf-gate-ready -1` exits 0 immediately
after it. Discard a run that overlapped load. The host is shared with other
projects' containers: load was 25-37 on 32 cores during the survey. If the
gate still refuses after three checks at least 20 minutes apart, mark the
measurement "perf gate deferred: host busy". Keep the item open, move to
the next item, and retry at the next checkpoint. Runs without `--gate` steer
and are never acceptance evidence.

## Debugging methods

Learned in J5-J8. Use them before inventing new ones.

1. **Separate the JIT from the baseline first.** `PYTHON_JIT=0` on a JIT build
   skips every `requires_jit_enabled` test, so a green run proves nothing for
   tier-2 tests. Compare against the JIT-off sibling build, or against the
   greedy behavior of the code you changed.
2. **Shrink to pure Python before reading C.** Probe with
   `sys.getallocatedblocks()` deltas over fresh `exec`'d functions, and
   `_opcode.get_executor(code, i)` to see whether a trace exists. Leaks and
   missing executors reproduced this way need no test runner. Remember the GC
   invalidates cold executors and backoff counters then block re-tracing.
3. **A release SIGSEGV becomes an assertion in the pydebug stage.**
   `build.py build --jit`, then run the same test on
   `rust-cpython/stage/bin/python3.16d`. `PYTHON_LLTRACE=2` there prints every
   optimized trace as it is created; the last one before the abort is the
   suspect. The abort's `executor_cases.c.h:<line>` is not reliable evidence
   of the faulting uop.
4. **Bisect a code-generation change with a runtime mask in a scratch build.**
   Read an env var once inside the changed function (for J8, a bit per
   `previous*4+input` spill transition), build it as a throwaway name such as
   `perf-jit-probe`, and run the failing tests once per bit in a single
   container. Deterministic crashes name the unsafe transition in minutes
   instead of one rebuild per guess. Delete the build and the mask before
   committing.
5. **Model an allocator offline.** Dump the optimized trace of each kernel with
   `_opcode.get_executor` from `perf-rust-jit`, parse `_PyUop_Caching` from
   `Include/internal/pycore_uop_metadata.h`, and replay greedy and candidate
   choices in Python. Keep `-1` default entries in the table parse or every
   later input depth shifts. Measure the saving before building.
6. **Uop share is not CPU.** Check the stencil body size in
   `jit_stencils-<triple>.h` (`code_body[N]`, empty for `_POP_TOP_NOP` in
   r10, r21, r32) before ranking a cost.
7. **pystats recipe.** Build `--jit --pystats`. Per kernel:
   `mkdir /tmp/py_stats`, run `PYTHONSTATS=1 build/python -c` with
   `sys._stats_clear()`, the kernel passes, `sys._stats_off()`, and
   `sys._stats_dump()`. Without the directory, the dump goes to stderr.
   Summarize with the build's `Tools/scripts/summarize_stats.py`. The stats
   dir is container-local; copy it out in the same `docker run`.
8. **Look in the archive, not the prefix.** The verified LLVM prefix holds about
   13 tools; the locked archive is the full release (`tar -tJf
   .cache/llvm/objects/<sha>.blob`, BusyBox `tar` has no `--wildcards`: build
   an explicit `-T` list, and list files, not directories). ASan: configure the
   assembled source out of tree with the locked clang, `-resource-dir` pointing
   at a scratch dir whose `lib` is the archive's `lib/clang/23/lib` and whose
   `include` is the prefix's, and `LLVM_VERSION=23 LLVM_TOOLS_INSTALL_DIR`.
   Symbolize with the archive's `llvm-symbolizer` via `ASAN_SYMBOLIZER_PATH`.
   Hybrid spike: `llvm-config-23` must exist or its pass-plugin build raises
   instead of skipping; a missing `PassPlugin.h` makes it fall back silently
   to an unfolded build, so check for `jit_fold_pass.so`.
9. **Harness traps.**
   - Two containers with one working directory collide on
     `test_python_<pid>` (container pids repeat), giving spurious
     `FileExistsError` and `IsADirectoryError`. Run suites one at a time per
     directory or give each its own `cd` scratch inside the container.
   - A crashed test leaves mode-000 directories (`dirE`) under
     `work/perf/<name>/build/build/test_python_*`; the next clean build then
     fails with `Permission denied`. `chmod -R u+rwx` and remove them.
   - A crash drops `core` in the repo root; delete it.
   - Never run bare `make` in a perf build directory: it creates a dev-profile
     cargo tree and `--incremental` refuses it. Use `perf.py build
     --incremental`, which also resyncs changed overlay files.
   - `rc=2` right after a crashed run is usually leftover scratch, not a
     product failure. Re-run once from a clean scratch before concluding.

## Decisions

### Owner

- **OD1, 2026-11-01: keep the pinned LLVM.** JIT stencils use the locked
  LLVM: 23.1.2 on Linux (`rust-cpython/linux-toolchain.lock.json`) and
  23.1.4 on macOS (`bootstrap.lock.json`). They are selected through
  configure's existing `LLVM_VERSION` and `LLVM_TOOLS_INSTALL_DIR` variables.
  Upstream's warning "Building with an LLVM version other than 21 is not
  supported" is accepted. No LLVM 21, Homebrew LLVM, or host clang is
  fetched, locked, or used, including in the hybrid spike. If LLVM 23 breaks
  a target's stencils, fix `Tools/jit` through the overlay or park that
  target. **Amended 2026-11-01 (owner: "widen the pin"):** the pin stays and no
  lock file changes. The lane's verified prefix keeps about 13 tools, but the
  locked archive is the full official release; spikes may extract further
  members of that same archive into `.work/` scratch (`opt`, `llvm-config`,
  `llvm-symbolizer`, headers, `libLLVM*.a`, the ASan runtime).
- **OD2, goal (`campaign-jit.md`): scope is the first research
  assignment.** Build reproducible JIT and no-JIT baselines on macOS arm64
  and Linux x86-64, investigate the hybrid experiment, compare against the
  Rust overlay, find the hottest remaining costs, and land one optimization.
  Production keeps JIT, PGO, BOLT, tail-call, and free-threading disabled.
  The goal's stages 4-6 are non-goals here: a speculative or optimizing tier,
  escape analysis, and a JIT-enabled distribution.
- **OD3, 2026-11-01: perf gates are waived until feature completion.** The
  shared host is noisy, so J4's four `--gate` verdicts and J8's ACCEPT gate do
  not block acceptance while the campaign lands features. Record each waived
  run with its exact command and `waived: owner OD3`; keep the digest, suite,
  parity, and differential evidence. This waives only the timing verdict, not
  suite parity, digests, or the J8 differential test. Reopen the gates at J9
  if the owner asks.

### Prescribed (the owner may overrule)

- **PD1: execution host.** Linux x86-64 runs here, in the container above.
  macOS arm64 proof runs locally on a Mac and is otherwise parked.
- **PD2: JIT mode.** `--enable-experimental-jit=yes` (on by default). Pairs
  differ by build, not by `PYTHON_JIT`: a JIT build and a no-JIT build from
  the same overlay commit. `PYTHON_JIT=0` runs are diagnostics only.
  `-I`/`-E` ignore `PYTHON_JIT`.
- **PD3: verdict policy unchanged.** `perf_verdict.py` and
  `rust-cpython/ENGINEERING.md` "Verdicts" apply as written: a 1% floor,
  95% intervals, two runs, and clean committed builds. The JIT set
  (`JIT_SET`, J2) contains the targets. `GATE_WORKLOADS` stays the guard
  set.
- **PD4: research target is reported, not gated.** The JIT-set geometric
  mean of CPU time for J8's candidate against `@incumbent-jit` is reported
  against an aspirational 0.90. Acceptance is the existing ACCEPT decision.
- **PD5: kernel source.** JIT kernels import the pyperformance 1.14.0
  benchmark functions from the vendored wheel
  `benchmarks/vendor/pyperformance-1.14.0-py3-none-any.whl`, with pyperf
  2.10.0 from the same directory. Both are already digest-pinned in
  `benchmarks/vendor/SHA256SUMS` and `pins.json`. No source is copied and no
  input lock changes.
- **PD6: the hybrid is a spike.** python/cpython#146235 (head `72ed4456`,
  open, conflicting with `main`, last updated 2026-05-08) builds and runs
  only in `.work/` on Linux x86-64. It never lands. It deletes aarch64
  relocation support, needs a LuaJIT submodule and a `luajit` binary at
  build time, optionally builds an LLVM pass plugin through `llvm-config`,
  and edits `Lib/test/`. Each of those conflicts with the lane contract (no
  handwritten assembly, ENGINEERING rule 9; no `Lib/test` changes; macOS
  arm64 is a target). Adopting it is an owner decision after J6.
- **PD7: GIL build only.** Free-threading is a separate dimension and out of
  scope.
- **PD8: the parity reference for a JIT build is its JIT-off sibling.** The
  sibling has the same overlay commit and the same flags except the JIT. A
  JIT build must fail exactly the sibling's failed-test set and produce the
  sibling's workload digests.
- **PD9: lane documentation follows the code.** The AGENTS.md invariant and
  the lane README now say the JIT is disabled in production and opt-in for
  lane builds.

### Delegated (bound; log only what a later slice reads)

- **Kernel iterations (J2):** one pass takes 0.2-2 s of CPU on
  `perf-upstream`. The digest is identical across two runs and across JIT
  and no-JIT builds. Drop a kernel whose output cannot be made deterministic,
  and say why in the commit body.
- **Warmup metric (J3):** first-pass CPU divided by the median CPU of passes
  3 and later, in one process. Derive it from samples the harness already
  takes where possible.
- **macOS code residency (J3):** any method that reads JIT executable
  mappings of a live process (`vmmap`, `mach_vm_region`). If none works
  unprivileged, report "unavailable" on macOS. Never estimate.
- **Native profiling on Linux (J7):** one attempt with host `perf` against a
  containerized process (for example, `--pid=host` and a shared perf-map
  directory). If it cannot attach or name JIT frames, use pystats only on
  Linux.
- **Optimization mechanism (J8):** the selection rule in J8.

## Baseline

Measured 2026-11-01 at `96f4112` on this host: Alpine x86-64, 32 cores,
60 GB RAM, 243 GB free disk, shared host load 25-37.

**Lane state after J0.** `perf-upstream` (439 s) and `perf-rust` (670 s,
58 release Rust extensions) are built and verified; `perf-upstream-jit`
(J1, 474 s) adds the locked LLVM 23.1.2 JIT. `@control`, `@incumbent`,
`@control-jit`, and `@incumbent-jit` resolve.

**J0 failed-file sets** (container `rust-cpython-linux:20260401`, `-j 4`):

- `perf-upstream`: `test.test_multiprocessing_fork.test_misc`,
  `test.test_multiprocessing_forkserver.test_misc`,
  `test.test_multiprocessing_spawn.test_misc`. The failing case is
  `test_forkserver_requires_writeable_tempdir`; all three files pass when
  run focused.
- `perf-rust`: the same three plus `test_socket`, `test_ssl`, `test_support`
  (the lane's known red set).
- `perf-upstream-jit` (J4): the `perf-upstream` set plus `test_socket`
  (RDS `testPeek` hang, also on the no-JIT build). `perf-rust-jit` (J4):
  the `perf-rust` set.

**Known red.**

- Lane full suite (ENGINEERING.md "Known red"): `test_ssl` and
  `test_support`. On Linux hosts, also `test_socket` RDS and multiprocessing
  `TMPDIR` cases, matching the J0 sets above.
- `python3 -m unittest discover -s benchmarks/tests -t .` (host
  Python 3.15.1): 4 pre-existing failures.
  `test_baseline.BaselineSnapshotTests.test_pbs_preset_uses_native_macos_reference_and_product_artifact`,
  `test_bench_inputs.SelectedInputTests.test_macos_uses_django_inputs_only_for_django_workloads`,
  `test_tooling.CompileallWorkloadTests.test_compileall_emits_every_source_to_a_fresh_prefix`,
  `test_tooling.ToolingFixtureTests.test_python_corpus_is_checked_in_and_package_sized`.
  They are not this campaign's. Run new benchmark tests by module.
- `python3 -m unittest discover -s rust-cpython/tests -p 'test_perf.py'`:
  green, 140 tests.

**Loop timings.**

| Command | Time |
| --- | --- |
| `test_perf.py` unit tests | 2.8 s |
| `benchmarks/tests` | 13 s |
| Lane clean perf build | 7-13 min (README; ThinLTO about 12.6 min on macOS) |
| Linux container `build.py test --all --jobs 4` | about 20 min (2026-10-31) |
| Focused suites | 20-90 s |
| Probe: plain 3.16.0a1 `-O2` clean build at `-j14` | 140 s |
| Probe: JIT stencil generation and link on top | 62 s |

**Foundation proof (OD1).** This was a probe, not the lane harness:
upstream 3.16.0a1 from the locked archive (sha256 `7dcd8a1f…5c60` verified),
Alpine clang 23.1.3, `-O2`, static. Scratch lives in
`.work/tmp/jit-probe/`.

- `LLVM_VERSION=23 LLVM_TOOLS_INSTALL_DIR=<llvm23>` generated
  `jit_stencils-x86_64-unknown-linux-gnu.h` (2.2 MB) with only the upstream
  warning. `sys._jit.is_available()`, `is_enabled()`, and `is_active()` are
  all true after about 4,000 hot-loop iterations. `PYTHON_JIT=0` disables
  it.
- Alpine's own triple `x86_64-pc-linux-musl` has no stencil target in
  configure, so the probe forced the gnu target. The lane's glibc target
  does not need that.
- 16 JIT-sensitive test files pass on both the JIT and no-JIT builds:
  `test_capi test_optimizer test_sys_settrace test_monitoring
  test_perf_profiler test_frame test_sys test_gc test_generators
  test_coroutines test_threading test_interpreters test_finalization
  test_weakref test_opcache test_dis`. Each build ran 3,377 tests; JIT
  skipped 102, no-JIT skipped 401.
- Steering only (the JIT spread was 21-102%). Medians of 5 interleaved runs
  of the pyperformance kernels, CPU ratio JIT/no-JIT:

  | Kernel | Ratio |
  | --- | --- |
  | `nbody` | 0.452 |
  | `spectral_norm` | 1.017 |
  | `float` | 0.811 |
  | `richards` | 0.685 |
  | `raytrace` | 1.013 |
  | `fannkuch` | 0.952 |
  | `deltablue` | 0.940 |
  | `chaos` | 0.926 |
  | `go` | 0.956 |
  | `nqueens` | 1.003 |
  | **Geometric mean** | **0.854** |

- Startup CPU, 80 interleaved runs, median in ms: JIT 29.75, no-JIT 28.98,
  JIT-off 29.44. The IQRs overlap. Startup max RSS is 11,368 KiB for both.
  Static `python` text grows by 740,789 B (+15.0%) and data by 162,864 B.
- Code residency is visible on Linux without dev mode as anonymous `r-x`
  mappings: 2 mappings and 12 KiB RSS after a hot loop; 0 for no-JIT and for
  `PYTHON_JIT=0`. The `[anon:cpython:jit]` name exists only under `-X dev`
  or `Py_DEBUG`, and only with `PR_SET_VMA_ANON_NAME`.
- **Unproven:** `aarch64-apple-darwin` stencils with LLVM 23. Cross-generating
  them here needs SDK headers (`TargetConditionals.h`, `mach-o/loader.h`).
  J1M on macOS proves or parks it.
- The overlay's `configure.ac` JIT section and its JIT lines in `configure`
  and `Makefile.pre.in` match upstream. The only difference is
  `clean-rust`.

## Sequence

Phases: P0 ground (J0); parity and completeness (J1-J4); hardening (J5);
investigation (J6, J7); optimization (J8); close (J9). Milestone M1, full
suites on every JIT build, is J4. M2, the full suite on the candidate, is
inside J8.

Parallel pairs: J1 with J2 (disjoint files); J5 with J6 (J5 owns no code, J6
owns only `.work/`). Builds share the host lease automatically. Everything
else is serial.

### J0: ground (S)

- **Owns:** no files; **Status** only.
- **Steps:**
  1. In the container: `python3 rust-cpython/build.py fetch`, then
     `python3 rust-cpython/perf.py fetch`. The first downloads the 2 GB
     locked LLVM archive.
  2. `perf.py build --name perf-upstream --empty-overlay` and
     `perf.py build --name perf-rust`.
  3. `perf.py test --name perf-upstream --all` and
     `perf.py test --name perf-rust --all`, in the background.
  4. Record each failed-file set in **Baseline**.
- **Accept:** `build.py doctor` exits 0 in the container; `perf.py status`
  shows both builds completed; both failed-file sets are recorded.

### J1: JIT flavor in both lane builders (M)

- **Owns:** `rust-cpython/perf.py`, `rust-cpython/build.py`,
  `rust-cpython/tests/test_perf.py`, `rust-cpython/tests/test_build.py`,
  `rust-cpython/README.md`.
- **Prereq:** J0.
- **Change:**
  - Add `--jit` to `perf.py build` and to `build.py build`.
  - One helper in `build.py` returns the JIT configure argument
    (`--enable-experimental-jit=yes` or `no`, replacing the literals at
    `rust-cpython/build.py:970` and `rust-cpython/perf.py:1087`). It also returns the environment:
    `LLVM_VERSION` is the major version taken from `toolchain.llvm_version`,
    never a literal, and `LLVM_TOOLS_INSTALL_DIR` is `toolchain.llvm_prefix`.
  - The build report gains `"jit": bool` and nothing else: the stencil digest,
    LLVM version, and regen version were dropped as unread bookkeeping.
  - Fail the build when any of these hold:
    - the stencil header is missing;
    - the build log shows a stencil clang other than
      `<llvm_prefix>/bin/clang`;
    - `PYTHON_FOR_REGEN` is older than 3.11;
    - a JIT stage fails `sys._jit.is_available() and sys._jit.is_enabled()`
      or does not become `is_active()` in a 200,000-iteration hot loop (the
      probe's check);
    - a no-JIT stage reports `is_available()`.
  - `--incremental` refuses a JIT-flag change.
  - `_label` (`perf.py:1327`) appends `jit`.
  - `ALIASES` (`perf.py:64`) gains `@control-jit` → `perf-upstream-jit` and
    `@incumbent-jit` → `perf-rust-jit`.
  - README command tables and the "no PGO, JIT" sentence (`rust-cpython/README.md:93`)
    are updated.
- **Tests:** configure arguments and environment for both flavors, the
  derived LLVM major, the report fields, the incremental refusal, the alias
  resolution, and the activation check's failure path.
- **Accept:**
  - `python3 -m unittest discover -s rust-cpython/tests -p 'test_perf.py'`
    and `-p 'test_build.py'` pass.
  - In the container, `perf.py build --name perf-upstream-jit --empty-overlay --jit`
    completes and passes its activation check.

### J2: JIT kernel workloads (M)

- **Owns:** `benchmarks/workloads/jit_kernels.py` (new),
  `benchmarks/workloads/registry.py`,
  `benchmarks/tests/test_jit_kernels.py` (new).
- **Prereq:** none. Runs in parallel with J1.
- **Change:**
  - Register these kernels as `Workload`s with category `interpreter` and
    module `jit_kernels`: `nbody`, `spectral_norm`, `float`, `richards`,
    `raytrace`, `fannkuch`, `deltablue`, `chaos`, `go`, `nqueens`, `hexiom`,
    `scimark_sor`, `scimark_sparse_mat_mult`, `scimark_fft`, `scimark_lu`,
    `scimark_monte_carlo`.
  - Add `JIT_SET: tuple[str, ...]` to `registry.py` and freeze it before any
    J4 measurement.
  - Load the functions from the vendored wheel (PD5) after checking its
    digest against `benchmarks/vendor/SHA256SUMS`, and fail loudly on a
    mismatch.
  - Print the registry's JSON result: an operation count and a digest of
    each kernel's deterministic output.
- **Tests:**
  - Each kernel's digest is stable across two runs under the host Python.
  - A deliberately perturbed kernel output changes the digest.
  - A wheel digest mismatch raises.
- **Accept:** `python3 -m unittest benchmarks.tests.test_jit_kernels` and
  `python3 -m benchmarks.workloads.jit_kernels nbody --iterations 1` both
  succeed.

### J3: JIT selection and metrics in perf.py (M)

- **Owns:** `rust-cpython/perf.py`, `rust-cpython/tests/test_perf.py`,
  `rust-cpython/README.md`.
- **Prereq:** J1 and J2.
- **Change:**
  - `bench` gains `--jit-set`, which adds `registry.JIT_SET` the
    way `--gate` adds `GATE_WORKLOADS` (`perf.py:1370`).
  - A new `jit-stats --ref R (--workload W | --jit-set)` subcommand reports,
    per workload:
    - JIT code residency: count and RSS of anonymous `r-x` mappings at the
      end of the run on Linux (`/proc/<pid>/smaps`); macOS per the delegated
      method;
    - warmup ratio (delegated definition);
    - the `libpython3.16` text and data size of the build;
    - `sys._jit` state.
  - `jit-stats` diagnoses; it never feeds a verdict.
- **Accept:**
  - The `test_perf.py` unit tests pass.
  - In the container, `perf.py jit-stats --ref perf-upstream-jit --workload nbody`
    reports residency > 0 KiB.
  - The same command on `--ref @control` reports 0 KiB.

### J4: Linux baseline matrix, milestone M1 (M)

- **Owns:** no code. **Status**, **Baseline**, and a "JIT" paragraph under
  ENGINEERING.md "Findings".
- **Prereq:** J3.
- **Steps:**
  1. Build `perf-rust-jit` (`perf.py build --name perf-rust-jit --jit`).
  2. Run `perf.py test --all` on `perf-upstream-jit` and `perf-rust-jit`.
     Each failed-file set must equal its sibling's from J0 (PD8).
  3. Run the probe's 16-file JIT-sensitive set on both JIT builds with
     `perf.py test --name N --suite ...`.
  4. Run the gates below, each with `--jit-set --gate`:
     - `--baseline @control --candidate perf-upstream-jit`
     - `--baseline @incumbent --candidate perf-rust-jit`
     - `--baseline perf-upstream-jit --candidate perf-rust-jit`
     - `--baseline @control --candidate @incumbent`
  5. Run `perf.py jit-stats --jit-set` on both JIT builds.
- **Accept:**
  - Both failed-file sets equal their siblings'.
  - All four verdicts are recorded with their decision and per-kernel
    ratios, or are recorded as "perf gate deferred: host busy".
  - Any digest mismatch is a red item: open `J4-<kernel>`, find the cause,
    and fix it in the overlay, or record it in `docs/open-work.md` as known
    red with its cause. It never becomes a waiver.

### J5: JIT correctness hardening (M)

- **Owns:** no code; fixes go in their own items.
- **Prereq:** J4.
- **Steps:**
  1. In the container, run `build.py build --jit` (pydebug).
  2. Run the 16-file JIT-sensitive set plus `test_capi`, `test_pdb`,
     `test_bdb`, `test_profile`, `test_cprofile`, `test_inspect`,
     `test_traceback`, `test__interpreters`, and every `ROUTES.md` suite of
     the five most-exercised Rust extensions in the JIT set (by `jit-stats`
     import list). Use `build.py test --suite ...`.
  3. Run refleak hunting directly:
     `stage/bin/python3.16d -m test -R 3:3 test_capi.test_opt test_optimizer test_monitoring test_sys_settrace test_generators`.
  4. ASan: in `.work/tmp/asan`, configure J1's `perf-upstream-jit`
     assembled source with `--with-address-sanitizer --enable-experimental-jit=yes`
     and the locked clang, then run the 16-file set. Limit: 2 attempts. If
     upstream ASan and the JIT do not build together, record the cause and
     close the step.
  5. Check whether python/cpython#160852 (executor invalidation during
     finalization) reproduces on 3.16.0a1 using its reproducer. If it
     reproduces, record it as known red with the issue link.
- **Accept:** every listed suite passes, or is known red with a recorded
  cause; `-R 3:3` reports no leak.

### J6: hybrid DynASM spike (M)

- **Owns:** `.work/tmp/hybrid/` only. The coordinator writes the verdict into
  **Status**.
- **Prereq:** J0 (locked LLVM in `.cache`) and J2 (kernels).
- **Steps:**
  1. In the container, `git clone` python/cpython at PR head `72ed4456` and
     at its merge-base with `main`.
  2. Build stock JIT at the merge-base and the hybrid at the head, both
     `-O2`, `--enable-shared`, with the locked LLVM 23.1.2 (OD1). Build
     LuaJIT in scratch from the PR's submodule commit.
  3. Run `jit_kernels` on both, 5 interleaved runs, CPU medians, plus the
     PR's `test_capi.test_opt` and `test_jit_peephole`.
- **Limit:** 3 build attempts. If the fold-pass plugin or any step requires
  LLVM 21, or `llvm-config` is absent from the official archive, record that
  and close the spike as "blocked by OD1".
- **Verdict:** "worth porting" only when all of these hold:
  - the geometric-mean CPU ratio of hybrid to stock is ≤ 0.90 on the JIT set;
  - every digest is equal;
  - both suites pass.

  Otherwise "closed".
- **Accept:** the verdict, numbers, and blockers are in **Status**. Porting
  is never part of this campaign (PD6). "Worth porting" goes to the owner.

### J7: cost profile (M)

- **Owns:** `rust-cpython/perf.py` (`--pystats` build flag adding
  `--enable-pystats`, only together with `--jit`),
  `rust-cpython/tests/test_perf.py`.
- **Prereq:** J4.
- **Steps:**
  1. Build `perf-rust-jit-stats`.
  2. Run each JIT-set kernel with stats collection on.
  3. Summarize with the pinned source's `Tools/scripts/summarize_stats.py`.
     Attempt native samples per the delegated rule.
- **Output:** a ranked table in **Status**, at most 10 rows. Each row gives
  the cost (trace aborts by reason, side exits and deopts by uop, executor
  invalidations, unsupported uops, tier-1 residency share), the kernels it
  touches, and its share of each kernel's CPU or of its exits.
- **Accept:** the unit tests pass; the table exists; every row cites the
  stats command that produced it.

### J8: one optimization, milestone M2 (L)

- **Owns:** the overlay files the mechanism needs, under
  `rust-cpython/overlay/` (for example `Python/optimizer_bytecodes.c`,
  `Python/bytecodes.c`, `Python/specialize.c`, `Tools/jit/*`). Include
  regenerated `*.c.h` outputs, because the build does not regenerate them.
  Also owns one new targeted differential test in `rust-cpython/tests/`;
  the overlay refuses changes under `Lib/test/`.
- **Prereq:** J5 and J7.
- **Selection rule:** take the J7 row with the largest product of share and
  number of touched JIT-set kernels. It must be implementable inside the
  existing tier-2 pipeline: abstract interpretation, specialization, uop
  definitions, or stencil templates. It must not add a tier, change the
  bytecode or pyc format, change observable semantics, or use handwritten
  assembly. JVM-style candidates rank first on ties: unboxed float or int
  in traces, speculative call inlining with guards, and allocation removal
  for non-escaping temporaries. A guard failure must deoptimize before any
  observable side effect (ENGINEERING rule 5).
- **Limit:** 3 mechanisms, 2 attempts each. Rank and try them in order.
- **Accept, all on the candidate build `perf-jit-opt`
  (`perf.py build --name perf-jit-opt --jit` from a committed overlay):**
  - `perf.py bench --baseline @incumbent-jit --candidate perf-jit-opt --jit-set --gate`
    decides ACCEPT;
  - `perf.py test --name perf-jit-opt --all` (M2) fails exactly
    `perf-rust-jit`'s set;
  - the J5 suite set and `-R 3:3` on a pydebug JIT build of the candidate
    are green;
  - the targeted differential test compares candidate and `perf-rust`
    output on adversarial inputs for the touched path (type changes mid-loop,
    `sys.settrace` and `sys.monitoring` toggled mid-trace, and the
    finalization ordering from J5) and passes;
  - the PD4 geometric mean is reported.
- **Failure:** if no mechanism reaches ACCEPT, park each on `lane/J8-<n>`
  with its verdict and cause. D7 is then unmet. The campaign still closes.

### J9: close (S)

- **Owns:** `AGENTS.md` (the invariant line per PD9), `docs/open-work.md`,
  `rust-cpython/ENGINEERING.md`, this doc.
- **Prereq:** J8 accepted or parked.
- **Steps:**
  1. Delete what the campaign made dead: no-longer-used probe helpers and
     its `.work/tmp/*` scratch. Prove a helper is dead by `rg` over the repo
     before deleting it.
  2. Move remaining follow-ups (hybrid porting, stages 4-6, macOS items not
     run) to `docs/open-work.md`.
  3. Run `ultrareview` on `main..campaign/jit` and fix the P1 and P2
     findings.
  4. Rewrite **Status** as the final report.
- **Accept:** the `test_perf.py`, `test_build.py`, and `test_jit_kernels`
  unit tests pass; `ultrareview` shows no open P1 or P2 findings.

## Acceptance

| Deliverable | Proof | Success | Failure |
| --- | --- | --- | --- |
| D1. JIT builds with the pinned LLVM | J1 accept; `perf.py status` shows `jit` on `perf-upstream-jit` and `perf-rust-jit` | Stencils come from `<llvm_prefix>/bin/clang` at the locked version, and the activation check passes | Any other clang, LLVM 21, a missing header, or an inactive JIT |
| D2. Predeclared JIT kernel set | J2 accept | 16 kernels (fewer only with a recorded reason), deterministic digests, wheel digest checked | Copied source, unchecked wheel, or nondeterministic digest |
| D3. JIT metrics | J3 accept | Linux residency > 0 on JIT and 0 on no-JIT; text size and warmup reported | Estimated or fabricated residency |
| D4. Linux baseline matrix | J4 accept | Four gate verdicts (or host-busy deferrals) and identical failed-file sets per sibling | A digest mismatch accepted, or a non-gate run used as evidence |
| D5. JIT correctness hardening | J5 accept | Suites green or known red with cause; no refleaks | A waived failure without cause |
| D6. Hybrid verdict | J6 accept | "worth porting" or "closed" with numbers or the blocker | Any hybrid code in the overlay; any non-locked LLVM |
| D7. One accepted optimization | J8 accept | Gate ACCEPT vs `@incumbent-jit`, M2 parity, differential test green | REJECT, INCONCLUSIVE, or a parity change; parked with cause |
| D8. Cost ranking | J7 accept | Ranked table with commands | Claims without a stats command |

## macOS arm64 results

On 2026-11-01, the four clean lane builds passed on an arm64 Mac with Xcode
27.0 and locked LLVM 23.1.4. The first pristine JIT build failed because the
lane omitted `llvm-readobj`, which Mach-O stencil generation requires. The
lane now extracts it from the same locked archive. Both JIT builds passed the
activation check and the overlay builds verified 58 Rust extensions.

`perf.py test --name perf-rust-jit --all` and `--name perf-rust --all`
failed the same two files: `test_ssl` and `test_support`, the known lane
failures. All 16 kernel digests matched across the four builds and both gate
runs.

Both `perf.py bench --jit-set --gate` comparisons had `perf-gate-ready` exit
0 immediately before and `perf-gate-ready -1` exit 0 immediately after.
Each used two independent runs. The table gives the geometric mean of the 16
kernels' pooled median candidate-to-baseline ratios and the range of their
pooled median peak RSS ratios. Both coded verdicts rejected peak RSS. These
are research measurements, not a production speed claim: two runs do not
establish five-run reproducibility, and the profile does not identify every
JIT frame.

| Baseline → candidate | Wall | CPU | Peak RSS range | Verdict |
| --- | ---: | ---: | ---: | --- |
| `@control` → `perf-upstream-jit` | 0.749 | 0.870 | 1.008–1.049 | REJECT |
| `@incumbent` → `perf-rust-jit` | 0.747 | 0.883 | 1.014–1.052 | REJECT |

`perf.py profile --ref perf-rust-jit --workload nbody --tool sample
--iterations 200 --seconds 10` captured float allocation and thread-local
access, plus unnamed executable addresses. It cannot attribute those
addresses to individual uops. The default one-iteration sample mostly caught
startup. The installed overlay `libpython` `__text` section measured
2,971,352 bytes without the JIT and 3,288,216 bytes with it using `size -m`.

An unprivileged `vmmap -w` of a live process after a hot integer loop showed
three private executable untagged mappings, each 16 KiB resident, with the
JIT active. The same loop in the no-JIT sibling showed none of that form.
The mappings are consistent with JIT code but are untagged; this probe does
not measure per-kernel residency. `jit-stats` still reports macOS mappings
and section sizes as unavailable because its parsers are Linux-specific.

A hardened-runtime or packaged JIT build needs an executable-memory entitlement.
Packaging is outside this lane and production keeps the JIT disabled.

## Execution boundary and non-goals

**Authorized:**

- Edits in the files each item owns, and commits on `campaign/jit`. The
  executor does not merge to `main`.
- `docker run` of the local `rust-cpython-linux:20260401` with the flags
  above. No image build, pull, prune, or delete.
- Network reads: lane `fetch` and `perf.py fetch` of locked inputs; `git
  clone` and `gh pr diff` of python/cpython for J6; the LuaJIT submodule
  for J6.
- Creating and removing the campaign's own perf builds (`perf.py clean
  --name <own>`) and its own `.work/` scratch.

**Forbidden:**

- Pushing; PR, issue, or comment activity on any remote.
- Edits to `sources.lock.json`, `rust-cpython/sources.lock.json`,
  `rust-cpython/linux-toolchain.lock.json`, `bootstrap.lock.json`,
  `rust-toolchain.toml`, `benchmarks/inputs*.lock.json`, and the vendored
  wheels.
- Any LLVM other than the locked one.
- Root `build.py`, `buildsys/`, `backend/`, `patches/`, CI, and
  `release-budgets.json`.
- `perf_verdict.py` thresholds; anything under `Lib/test/`.
- Cleaning `perf-upstream` or `perf-rust` in the primary checkout.
- Deleting `.work/` content the campaign did not create.

**Non-goals:**

- The goal's stages 4-6: a new optimizing tier, OSR beyond upstream's,
  escape analysis as a subsystem, and a JIT distribution.
- Free-threading; PGO, BOLT, and the tail-call interpreter.
- Porting the hybrid JIT (owner decision after J6).
- Linux arm64 (not implemented in the lane).
- Writing the JIT in Rust. The goal says Rust only where it measurably helps;
  J8's mechanism may be Rust only if it meets that bar inside the
  tier-2 pipeline.

**Contract changes for the owner:**

- `perf.py`: `build --jit`, `build --pystats`, `bench --jit-set`,
  the `jit-stats` subcommand, the `@control-jit` and `@incumbent-jit`
  aliases, `jit` and `pystats` in the build report, and `jit` in labels.
- `build.py build --jit`.
- The benchmark registry gains 16 workloads and `JIT_SET`.
- The AGENTS.md invariant and lane README wording (PD9).
- J8 changes interpreter-core overlay files (tier-2 optimizer or JIT
  templates).

## Reference

| Path | Role | Plan |
| --- | --- | --- |
| `rust-cpython/perf.py:62-64` | `CONTROL`, `INCUMBENT`, `ALIASES` | change (J1) |
| `rust-cpython/perf.py:67` | `GATE_WORKLOADS` guards | reuse |
| `rust-cpython/perf.py:945`, `:997`, `:1086-1088` | perf `build`, `_build_locked`, configure literal `--enable-experimental-jit=no` | change (J1) |
| `rust-cpython/perf.py:902` | `_build_environment` | change (J1) |
| `rust-cpython/perf.py:1297`, `:1327`, `:1341` | `resolve`, `_label`, `_gate_checks` | change and reuse (J1) |
| `rust-cpython/perf.py:1370` | `selection` | change (J3) |
| `rust-cpython/perf.py:1702` | `bench` | reuse; flag (J3) |
| `rust-cpython/perf.py:1760`, `:1829` | `profile`; macOS-only `/usr/bin/sample` | reuse (macOS) |
| `rust-cpython/perf.py:1923` | `perf.py fetch` (Django closure) | reuse |
| `rust-cpython/build.py:965-977` | coverage `_configuration` with JIT literal | change (J1) |
| `rust-cpython/build.py:683` | `_llvm_ready`; `toolchain.llvm_version` and `llvm_prefix` | reuse |
| `rust-cpython/lane_linux.py:1-19` | Linux toolchain and `unshare` boundary | reuse |
| `benchmarks/workloads/registry.py:14-63` | `Workload`, `WORKLOADS`, `BY_NAME` | change (J2) |
| `benchmarks/vendor/` | pyperformance 1.14.0 and pyperf 2.10.0 wheels, `SHA256SUMS` | reuse |
| Upstream `configure.ac:2960-2991` | `--enable-experimental-jit`; `REGEN_JIT_COMMAND` passes `LLVM_VERSION` and `LLVM_TOOLS_INSTALL_DIR` | reuse via env |
| Upstream `Tools/jit/_llvm.py:13` | `_LLVM_VERSION = "21"`; `_targets.py:353` prints the unsupported warning | accepted (OD1) |
| Upstream `Python/jit.c:130-181` | anonymous mmap RW then `mprotect` RX | measured (J3) |
| Upstream `Include/internal/pycore_mmap.h:19-35` | `cpython:jit` VMA name only under dev mode or `Py_DEBUG` | informs J3 |
