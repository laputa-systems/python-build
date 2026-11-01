Yes—we should pursue a JIT for `python-build`. But I would not build another JIT from scratch, and I definitely wouldn't rewrite all of CPython first.

The interesting opportunity is to take CPython's existing execution machinery, selectively adopt JVM-style optimization techniques, and use your Rust research lane to experiment more aggressively than upstream CPython can.

I inspected [laputa-systems/python-build](https://github.com/laputa-systems/python-build?utm_source=chatgpt.com) at commit `96f4112` (November 1, 2026). The repository is already structured for this kind of experiment:

- Production: CPython 3.15.1, GIL enabled, JIT disabled, with three supported macOS/musl Linux targets.
- Research: A separate CPython 3.16.0a1 Rust overlay, with 71 qualified public-behavior targets, differential testing, and a fairly rigorous performance harness.
- Existing constraints: The JIT, PGO, tail-call interpreter, and free-threaded ABI are explicitly disabled. These are configuration decisions, not evidence that they would perform poorly.

That research lane gives us a relatively safe place to explore a different execution architecture without destabilizing your distribution.

## What happened to CPython's JIT?

The recent developments are more complicated than simply giving up.

In June 2026, Python's Steering Council froze new JIT feature and optimization development on `main` pending acceptance of a Standards Track PEP. It specifically raised maintenance, debugging, free-threading, compatibility, and performance concerns.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://discuss.python.org\&sz=32)

Discussions on Python.org



The resulting [PEP 836](https://peps.python.org/pep-0836/?utm_source=chatgpt.com) was withdrawn on October 19, 2026. Importantly, the existing CPython 3.15 JIT had already achieved approximately 4–12% geometric-mean speedups in the measurements presented by its authors.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://peps.python.org\&sz=32)

peps.python.org+1



So this is not a failed compiler with no worthwhile engineering. It's an experimental compiler with promising performance and unresolved questions about whether its maintenance and compatibility costs justify becoming part of the supported runtime.

That distinction makes its technology especially interesting for your fork.

The authors' October 19 explanation was that they lacked the bandwidth to carry the proposal through the Steering Council's approval process—not that the compiler had stopped producing useful speedups. They intend to make their reference implementation available.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://discuss.python.org\&sz=32)

Discussions on Python.org



## There is already a particularly relevant AI-assisted JIT experiment

I also found [python/cpython#146235](https://github.com/python/cpython/pull/146235?utm_source=chatgpt.com), a hybrid JIT experiment drawing on Pyston's DynASM approach.

Its author explicitly describes using Claude Opus 4.6 to implement much of the experiment, with human architectural direction. That's remarkably close to the agent-driven development approach you're proposing.

The design combines LLVM-compiled C stencils with DynASM-based runtime code generation, pursuing better hot/cold code separation and less time leaving compiled code.

Its reported improvements over the existing CPython JIT include:

| Benchmark                 | Reported speedup |
| ------------------------- | ---------------- |
| `nbody`                   | 1.80×            |
| `spectral_norm`           | 1.51×            |
| `scimark_sparse_mat_mult` | 1.41×            |
| `scimark_fft`             | 1.39×            |
| `float`                   | 1.21×            |
| `html5lib`                | 1.14×            |

These are author's measurements on a particular Intel Linux machine, not independently validated results for your targets. The experiment also reports disabling reference tracing on JIT paths, so it is not a drop-in compatibility-preserving solution.

Nevertheless, this is far more compelling starting material than asking an agent to invent a compiler from scratch.

## The architecture I'd pursue

I agree with the JVM inspiration, particularly the HotSpot and Graal approach to speculative optimization. But Python's dynamic semantics and exposed C API mean we cannot simply transplant the JVM's runtime design.

Proposed execution architecture

CPython 3.16 interpreter

Keep existing bytecode semantics, object model, and C ABI

Runtime profiles and hotness counters

Tier 2: Existing CPython JIT

uop IR · trace recording · copy-and-patch

Only exceptionally profitable code

Tier 3: Experimental optimizing JIT

Speculation · inlining · unboxing · escape analysis · optimized native code

Guards, deoptimization and invalidation

CPython interpreter fallback

Preserve observable behavior on unsupported operations

I'd treat this as a direction, not a mandate to implement three execution tiers immediately. CPython already has substantial specialization machinery that we should reuse before adding another compiler.

The JVM ideas worth borrowing first are:

1. Tiered optimization and profiling: Spend compilation effort in proportion to measured hotness and expected payoff.
2. Speculative inlining: Specialize calls based on observed types, with guards and correct invalidation.
3. Unboxed numerical operations: Avoid repeatedly constructing and destroying Python float/int objects inside hot regions.
4. Escape analysis and scalar replacement: Eliminate temporary allocations when objects don't escape and their identity is unobservable.
5. On-stack replacement and deoptimization: Move long-running loops into compiled code, and reconstruct interpreter state when assumptions fail.

I'd defer implementing a full optimizing tier until profiling identifies a workload that the existing JIT cannot efficiently handle.

### Where Rust belongs

I would not insist on writing the JIT in Rust.

Rust is sensible for isolated IR analyses, optimization passes, and well-defined compiler infrastructure. But existing CPython uops, the proven C runtime interface, and LLVM-generated stencils are considerable assets.

Use Rust where it measurably improves correctness, maintainability, or performance. Don't build a second PyObject runtime, a bespoke garbage collector, or an entirely new compiler backend merely to increase Rust coverage.

## How I'd execute this in `python-build`

| Stage         | Work                                                           | Decision gate                                    |
| ------------- | -------------------------------------------------------------- | ------------------------------------------------ |
| 1. Baseline   | Enable upstream JIT in an isolated research build              | Tests, runtime behavior, perf evidence           |
| 2. Compare    | Evaluate stock copy-and-patch versus the hybrid JIT experiment | Same source revision, flags, hardware            |
| 3. Optimize   | Improve the best-performing existing backend                   | Real end-to-end speedup                          |
| 4. JVM ideas  | Implement speculative inlining and unboxing experiments        | Proven benefit after deoptimization costs        |
| 5. Extend     | Explore escape analysis and a higher optimizing tier           | Only if profiling justifies complexity           |
| 6. Production | Consider a separately qualified JIT-enabled distribution       | No outstanding correctness or packaging blockers |

The first real task is changing `rust-cpython/perf.py` to build and benchmark both JIT-enabled and JIT-disabled interpreters from the same pinned CPython revision.

Your existing harness is particularly well suited to this because it already tracks application wall time, CPU time, peak RSS, and clean paired measurements. We should additionally capture native code size, JIT compilation overhead, warmup time, and executable-memory residency.

Two platform-specific details deserve early attention: upstream currently specifies LLVM 21 for generating JIT stencils even though your builds pin LLVM 23, and Apple Silicon imposes executable-memory restrictions that may affect packaging and hardened-runtime entitlements. Neither should be bypassed just to get the experiment running.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+1



## What I'd demand before calling it successful

Correctness and ABI compatibility

Mandatory

Full relevant CPython tests, extension compatibility, callback/refcount semantics, debugging and tracing behavior, subinterpreter tests, and sanitizer-assisted stress testing.

Performance over stock JIT

I'd use a 10% geometric-mean speedup on a predeclared, representative workload set as an ambitious research target—not a reason to change your existing 1% regression guardrails.

Cold-start and memory discipline

No unapproved regression in startup time, peak RSS, or application output. Keep the compiled-code cache bounded.

Compatibility before free threading

Establish correctness under the existing GIL first. Evaluate the free-threaded build as a separate dimension rather than simultaneously changing synchronization, compiler, and object ownership.

For perspective, an October 28 issue, [python/cpython#160852](https://github.com/python/cpython/issues/160852?utm_source=chatgpt.com), describes a JIT crash caused by executor invalidation during finalization. That's precisely the kind of lifecycle interaction that can evade ordinary Python-level correctness tests.

## About the two-week autonomous rewrite claim

I think a capable coding agent could produce an extraordinarily ambitious prototype in two weeks. I'm much less convinced by full compatibility, including the ABI, free threading, a JIT, and better performance on every metric.

The distinction is between passing tests and preserving all observable runtime contracts.

CPython compatibility includes native extensions, refcount-sensitive finalization, C callbacks, embedding, signal handling, frame introspection, debugging and profiling interfaces, and thread-state lifetimes. Existing tests do not exhaustively specify those behaviors.

The JVM is an excellent reference for optimizing dynamic execution, but the freedom afforded by its managed object model is fundamentally different from CPython's C-visible objects.

A complete rewrite would force us to solve those difficult compatibility problems while simultaneously inventing a new execution engine. That would make performance investigations much harder to isolate.

## My recommendation

Start a JIT research campaign in `rust-cpython/`, using the existing CPython JIT as the foundation. Preserve the current production configuration and make no shipping changes yet.

I'd give an agent the following tightly scoped first assignment: establish reproducible JIT/no-JIT baselines on macOS arm64 and Linux x86-64, investigate the hybrid DynASM experiment, compare against the current Rust-overlay interpreter, identify the hottest remaining execution costs, and implement one carefully selected optimization with complete differential evidence.

That should tell us whether the next step is a better code generator, deeper object optimizations, or a genuine JVM-style higher-tier compiler.

The ambitious endgame is attractive: a CPython-compatible runtime with much better native-code performance, without sacrificing the ecosystem. I think that's a stronger project than rewriting all of Python, and your existing research infrastructure makes it a credible experiment.
