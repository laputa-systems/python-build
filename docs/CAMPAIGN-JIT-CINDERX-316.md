# CPython 3.16 Rust optimizing JIT: CinderX-informed roadmap

**Approved direction, 2026-11-09.** Build a Rust-native optimizing JIT for
**current CPython 3.16**, learning selectively from Meta CinderX rather than
translating all of its C++ JIT. Stock CPython is the **semantic oracle**;
CinderX is a **performance benchmark and optimization reference**, never
the authority for Python correctness. Keep the existing pinned LLVM and
the shipping 3.15 product unchanged.

## Starting state and immovable constraints

- Production `python-build`: locked CPython 3.15.1, JIT off, OpenSSL-free,
  relocatable macOS arm64 and Linux musl distributions. This research roadmap
  does not change its source, release gates, dependencies or platforms.
- Research `rust-cpython/`: currently pinned to upstream `v3.16.0a1`,
  SHA `22d79d9840a275ed6ed36cd590dc74edc61765fb`. That pin and its
  2026-11-02 results are **historical**: migrate the research lane first.
- **Latest 3.16 means upstream `python/cpython` `main` at the time of
  each intentional refresh**, not an old alpha tag and not a floating
  source during builds. On 2026-11-09 the observed `main` head was
  `e891a9117985888df3e5d48145cf07ddda098335` (3.16.0a1+dev).
  Recheck the tip when work starts and pin an immutable commit, verified
  archive SHA-256 and size. 3.16.0a2 is scheduled 2026-11-10; continue
  reviewed refreshes through pre-releases rather than assuming one old
  snapshot is permanent. https://peps.python.org/pep-0826/
- **LLVM pins are fixed**: macOS arm64 research currently uses LLVM
  **23.1.4** from `bootstrap.lock.json`, and Linux x86-64 research
  uses LLVM **23.1.2** from `rust-cpython/linux-toolchain.lock.json`.
  Do not adopt LLVM 21 just because the upstream JIT README names it;
  do not silently upgrade, substitute system LLVM, or introduce runtime
  LLVM, asmjit, CinderX, GCC/libgcc or another backend. Investigate a
  compatible narrow patch first; escalate a demonstrable incompatibility.
- Research host coverage is macOS arm64 and Linux x86-64; Linux arm64 has
  not been implemented there. Keep research and production distinct.
- Preserve existing acceptance gates and evidence in
  [CAMPAIGN-JIT-CLIMB.md](CAMPAIGN-JIT-CLIMB.md) and
  [../rust-cpython/ENGINEERING.md](../rust-cpython/ENGINEERING.md):
  no relaxed performance floors, memory/code budgets or correctness rules.

## Four distinct reference roles

| Role | Code | Purpose |
| --- | --- | --- |
| **Correctness oracle** | Clean CPython at the *same pinned 3.16 commit*, JIT off; paired upstream JIT-on control | Python semantics, C-API, object/refcount ownership, tracing/monitoring, exceptions, deopt, introspection, GC, threading and subinterpreters |
| **External performance reference** | Pinned upstream [CinderX](https://github.com/facebookincubator/cinderx), benchmarked on a CPython version it actually supports | Identify profitable optimization mechanisms and attainable, workload-specific JIT gains; never redefine Python semantics |
| **Implementation target** | Existing `rust-cpython/overlay/` + pinned upstream CPython 3.16 JIT integration | Implement selected mechanisms in Rust; retain CPython ABI and existing LLVM tooling |
| **Optional architectural comparator** | [PEP 836 method-based prototype](https://github.com/openai-oss-forks/cpython/tree/public/pep-836), observed SHA `04a79098cc83898249d33b787e15fd2e2cf8d61a` | Compare method-based and tracing frontends only after independent qualification |

CinderX currently advertises CPython `>=3.14.5,<3.16`, with 3.15
macOS arm64 and musllinux x86-64/aarch64 coverage. Its 3.16 source work
does **not** establish an externally supported or passing 3.16 JIT.
Do not compare raw Python 3.15 CinderX times with raw Python 3.16 Rust
times as if they share an interpreter: report *within-version* JIT/no-JIT
uplifts separately. Only claim a head-to-head on matched 3.16 source if
CinderX itself is ported and qualified on that exact revision.

## R0 — Refresh and qualify latest CPython 3.16 (must run first)

1. In an isolated worktree, resolve latest upstream `python/cpython main`,
   verify immutable source/provenance, update only the **research** source
   lock and source archive digest, and preserve a retrievable record of the
   v3.16.0a1 baseline. Never build against a floating ref.
2. Audit the changed bytecode and uops, optimizer, stencil layout, runtime
   helpers, frame structure, C-API/ABI, Python 3.16 language behavior, Rust
   bindings, and overlay conflicts. Port compatible code deliberately.
3. Verify **unchanged** LLVM 23.1.4/macOS and 23.1.2/Linux can regenerate
   stencils, assemble, link and produce ThinLTO output. Record precise
   failures, don't change a lock by stealth. If blocked, stop and escalate.
4. Rebuild clean upstream JIT-off/on and Rust-overlay JIT-off/on siblings
   from identical new CPython sources per host. Compare targeted and broad
   CPython suites, pydebug, tracing/monitoring, reference ownership,
   generators, subinterpreters, differential fuzzer, and available
   sanitizers. Preserve accurate upstream/host-known-red attribution.
5. Rerun clean A/A calibration, fixed 16 training and eight held-out
   kernels, guards, startup/import/Django and whole-application checks.
   Track kernel CPU, wall and whole-process CPU, cold/warm behavior,
   native code size, peak/private RSS, digest and JIT coverage. Re-establish
   `S0` and incumbent on the refreshed source. Historical `S0=0.7474`
   is *not* a valid before-score for a different CPython revision.

**Exit:** current 3.16 is reproducibly pinned and passes parity/baseline
gates on implemented hosts; otherwise retain previous known-good lane and
do not start optimized-port work. Requalify again after intentional
subsequent upstream 3.16 refreshes.

## R1 — Reproduce CinderX and extract only measured ideas

1. Pin and source-build CinderX on stock CPython 3.15.1 initially, since
   that is within its advertised range. Run its published tests, and
   establish matched JIT-disabled/JIT-enabled **3.15** baselines. Record
   full source/ABI/dependency and license closure, code bytes, warmup,
   compilation CPU/time and peak RSS. Treat binary wheels as feasibility
   evidence only, not proof they work in our custom OpenSSL-free runtime.
2. Benchmark shared microkernels, object-heavy programs, import/Django,
   and relevant pyperformance suites. Normalize per same-version
   interpreter baseline. Use 3.16 Rust JIT vs its 3.16 JIT-off sibling as
   a separate, explicitly cross-version-indicative comparison.
3. Inspect `cinderx/Jit/hir/`, `cinderx/Jit/lir/`, `cinderx/Jit/codegen/`,
   `cinderx/Jit/inline_cache.cpp`, guard invalidation, type feedback,
   typed IR, native code emission, inlining and deoptimization machinery.
   For each mechanism report source location, version assumptions,
   observable semantics, existing Rust coverage, benchmark ceiling,
   integration complexity and test oracle. Rank by *measured* potential.
4. Do not inherit CinderX's full runtime, Static Python language subset,
   parallel GC, lightweight frames, C++/asmjit dependency closure or
   its build system. Any actual code reuse requires explicit license,
   attribution and safety review; prefer independent Rust implementation.

**Exit:** pinned benchmark receipts and a ranked optimization matrix.
A future 3.16 CinderX port may be independently evaluated, but is not
a prerequisite for the Rust campaign.

## R2 — Incremental Rust-native optimizing JIT

Use the existing trace infrastructure and retained CPython integration
first; do not redesign the backend based on an unmeasured preference.

1. Profile refreshed hot paths. Attack systemic thread-state and frame
   overhead, redundant guards and validity/IP-setting costs **only if
   the new 3.16 profile still demonstrates the cost**.
2. Implement exact float then compact-int unboxing with overflow guards,
   then loop-carried unboxed locals. Reconstruct boxed Python objects
   accurately on every exit, callback, store, traceback and frame-inspection
   path. CPython remains the behavioral oracle.
3. Selectively adapt CinderX-style global/attribute inline caches,
   version/type specialization and deopt invalidation, honoring descriptor
   semantics, object mutation, inheritance, lazy imports and subinterpreters.
4. Add Python-to-Python inlining with lazy and **correct** frame materialization,
   then measured escape analysis/allocation removal, guard hoisting and
   trace extension. Finally revisit register allocation and stencil glue.
5. Each candidate gets a ceiling probe, clean code review where ownership,
   deopt or invalidation changes, JIT-on/off differential fuzz, matched
   CPython suites, interleaved benchmark gate, held-out/guard nonregression,
   and memory/code-size checks. Follow the unmodified hill-climb ledger
   and acceptance policy; reject or park inconclusive changes rather
   than claiming speculative speedups.

**Exit:** reproducible gains on 3.16 with no semantic or memory/latency
regression beyond the existing gates. CinderX inspires mechanisms but
does not replace correctness tests or become a mandatory dependency.

## R3 — Optional method-based JIT frontend hypothesis

Evaluate the withdrawn PEP 836 prototype in a separate pinned worktree,
after R0 and a useful R1 mechanism catalog. Verify actual compatibility
with the latest 3.16 first; its prototype has known generator/coroutine
and polymorphism gaps. Compare tracing vs method-based compilation,
warmup, whole-program gain, deopt behavior, maintenance complexity,
size and RSS. A shared Rust IR/backend is optional *only if* it yields
measurable benefit. Do not replace the tracing frontend based on a design
sketch or mix unqualified prototypes into the default lane.

## Promotion boundary (out of scope)

No production `buildsys/cpython.py`, target matrix, payload, package or
dependency changes are authorized here. JIT/AOT promotion needs a separate
owner decision and full production-specific qualification (including
OpenSSL-free, glibc-free, no-libgcc, relocation, exact target ABI and
runtime closure). Cython/mypyc/Nuitka AOT is a distinct investigation.

## Stop rules and agent handoff

- Stop if latest CPython 3.16 does not build with the existing pinned LLVM,
  upstream/overlay parity fails, reference ownership or deopt is unsafe,
  JIT-on/off semantics diverge unexpectedly, or a current size/performance
  gate fails. Report blocker and evidence instead of changing LLVM,
  jumping source versions, suppressing tests or weakening criteria.
- Source refreshes reset **comparison baselines**, not frozen historical
  results. Every benchmark report must record CPython, LLVM, Rust overlay
  and CinderX SHAs, flags, workload digests, runtime environment and
  confidence intervals. A matched A/A gate precedes optimization claims.
- **Next unattended agent task: R0 only.** Read the current source lock,
  pinned macOS/Linux toolchains, `AGENTS.md`, `rust-cpython/ENGINEERING.md`
  and `CAMPAIGN-JIT-CLIMB.md`. Discover the then-latest upstream 3.16
  `main` SHA, update/qualify the research lane in a separate worktree
  without changing LLVM or production, and report precisely what passed
  and what remains blocked. R1–R3 follow sequentially.
