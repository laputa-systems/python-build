# Lane engineering contract

The lane is an isolated CPython 3.16.0a1 research interpreter with a Rust
overlay. It is not the shipping 3.15.1 product, and Linux arm64 is not
implemented. Build and benchmark commands are in [README.md](README.md);
route suites are in [ROUTES.md](ROUTES.md); remaining work is in
[../docs/open-work.md](../docs/open-work.md).

## Rules

1. Never bend a Rust `GlobalAlloc`, aliasing, initialized-memory, allocator,
   or CPython C-API contract (reference ownership, attached thread state,
   interpreter lifetime) to move a metric. A violation is fixed before
   further optimization of that route.
2. Python object, type, and module layouts, refcount constants, `Py_buffer`
   wrappers, and panic handlers exist only in the shared binding crate.
3. A new custom allocator, synchronization primitive, parser, or cache needs a
   comparison with maintained alternatives and the semantic reason in the
   commit body.
4. A consolidation deletes implementations or removes a boundary. Keeping
   every old copy is unfinished.
5. A fast path falls back before any observable side effect, or carries a
   proven continuation. User callbacks never run twice.
6. A performance claim names the exercised path and the application workload
   it moves.
7. A new or changed unsafe kernel gets targeted differential or fuzz tests,
   plus Miri or a sanitizer where either can run. CPython suites stay required
   but do not prove Rust soundness.
8. A route has no tenure. A Rust adapter may be merged, bypassed, or deleted
   when a shared, upstream, or maintained implementation keeps its ROUTES.md
   line green within budget.
9. Crates pin exact versions in the overlay `Cargo.lock` and record their
   license in the route's `THIRD_PARTY_LICENSES.md`. No OpenSSL, no production
   dependency, no handwritten assembly or SIMD.

## Verdicts

Each metric is improved (95% interval below 0.99), regressed (above 1.01),
within budget (upper bound at most `1 + allowance`), or inconclusive.
Decisions are REJECT on any regressed metric or output mismatch,
INCONCLUSIVE when any metric is inconclusive, ACCEPT when a target improved
and every other metric is improved or within budget, and WITHIN-BUDGET when
all metrics are within budget. Goal rows use OVER, MET, BEYOND, and UNCLEAR.

Application wall, CPU, and peak RSS use a predeclared 1% floor. Module rows
diagnose: 64 KiB load and 256 KiB working-peak floors. Gate evidence needs
two runs on clean committed optimized builds with a matched control. Do not
move thresholds to clear debt. Identical interpreters must not read
differently under calibration.

## Findings

- Startup and Django RSS debt is dyld fixup residency proportional to Rust
  text in `libpython`. Moving `_ssl_rs` to a shared extension cut `libpython`
  from 14.1 MB to 8.5 MB; ThinLTO with std built from source cut it to
  7.25 MB and roughly halved small extensions.
- Removing `std` entirely is not worth its global allocator and panic work
  after ThinLTO; revisit only if a target needs the residual megabytes. If
  tried, measure against the ThinLTO baseline.
- The overlay import path adds a `marshal._loads_importlib_bytecode` call per
  pyc load and extra imports; this is visible on import-heavy Django rows.
- JIT, Linux x86-64 (locked LLVM 23.1.2, GIL build). Both JIT builds fail
  exactly their no-JIT siblings' full-suite sets, plus `test_socket`
  `RDSTest.testPeek` hanging to the 15-minute timeout on `perf-upstream-jit`;
  it hangs identically on the no-JIT `perf-upstream`, so the cause is the host
  RDS socket, not the JIT. All 16 JIT-set kernel digests are identical across
  the four builds. The JIT adds about 0.4 MB of `libpython` text on the
  upstream build; every kernel maps 2-6 anonymous `r-x` regions after a hot
  loop. Timing verdicts are deferred.
- JIT cost profile: of executed tier-2 uops, type guards are 10.5%, `_SET_IP`
  9.6%, `_CHECK_VALIDITY` 8.1%, and `_SPILL_OR_RELOAD` 7.5%; side exits are
  0.7%. The register-cache allocator now minimizes spills over the whole trace
  (8.0% fewer on the JIT set); `perf-jit-opt` keeps `perf-rust-jit`'s
  failed-file set and kernel digests.
- `_bz2_rs` once gave a codec a `&mut [u8]` over uninitialized `bytes`
  storage; the shared writer wrapper replaced it.

## Closed mechanisms

Do not repeat without new evidence: SSL process-wide provider `OnceLock`,
TLS identity cache and `Arc`-shared chains; HMAC state behind a
`_hashlib_rs` capsule; datetime stack formatters and packed `c_long` fields;
ElementTree single-write, streaming, slab, and bytes-sink designs; the
`csv_core` writer; libpython export trimming; a concurrent `bbtree`. Module
rows for `_strptime`, `warnings`, `html.parser`, `uuid`, `decimal`,
`sqlite3`, `bisect`, and ElementTree flip on page-quantized values and are
measurement-floor rows.

## Decisions

ICU4X 2.4.0 with Unicode 18 data stays. Do not redesign around the proposed
3.17 Rust API. A concurrent `bbtree` stays closed. Route tenure follows rule 8.
The OpenSSL-free rule holds. Release builds use ThinLTO with std built from
source; removing `std` entirely is dropped unless a measured target needs it.

## Known red

macOS arm64 and Linux x86-64 full suites fail only `test_ssl` (RTLS policy
gaps) and `test_support` (hashlib shim); Linux hosts add host-specific
`test_socket` RDS and multiprocessing `TMPDIR` failures. The lane keeps
`test_unicodedata` green because the a1 test file reads the loaded
`unidata_version`.
