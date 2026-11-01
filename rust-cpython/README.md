# Rust-for-CPython research lane

This isolated lane builds upstream CPython **3.16.0a1** with a Rust overlay.
It is not the shipping 3.15.1 product. JIT experiments run only in this lane:
the product build keeps the JIT disabled, and `--jit` here never changes the
product configuration. All 71 named public-behavior coverage
targets passed their complete relevant CPython suites on native macOS arm64
and Linux x86-64; later parity runs keep the known-red set recorded in
[ENGINEERING.md](ENGINEERING.md). Linux arm64 is not implemented here and is
never described as qualified. The engineering contract, verdict policy, and
closed mechanisms are in [ENGINEERING.md](ENGINEERING.md); each route's suite
contract is in [ROUTES.md](ROUTES.md); open work is in
[../docs/open-work.md](../docs/open-work.md).

The verified source is upstream `python/cpython` tag `v3.16.0a1`, commit
`22d79d9840a275ed6ed36cd590dc74edc61765fb`, pinned with its archive digest in
[`sources.lock.json`](sources.lock.json). The overlay supplies the Cargo
workspace and the `cpython-sys`/`cpython-build-helper` bridge crates; the
overlay lockfile is the build lock, and its digest is pinned in the same
source record. The Rust toolchain is pinned in
[`rust-toolchain.toml`](../rust-toolchain.toml). The private Cargo home is used
offline after fetching. Release perf builds build std from the pinned
`rust-src` with ThinLTO; `fetch` caches the toolchain's std crates with the
workspace crates. Do not update the source, toolchain, or Cargo lock
accidentally; intentional vetted crate additions update the overlay lockfile.

The lane uses a shared `cpython-binding` layer and `cpython-deflate` engine,
a static Rust carrier for built-in modules, a shared `_ssl_rs` extension, and
ThinLTO release builds. Build, test, and benchmark commands run through
`build.py` for coverage and `perf.py` for optimized builds.

## Active commands

```sh
python3 rust-cpython/build.py doctor
python3 rust-cpython/build.py fetch
python3 rust-cpython/build.py build
python3 rust-cpython/build.py build --jit
python3 rust-cpython/build.py test --suite test_bz2
python3 rust-cpython/build.py test --all
python3 rust-cpython/build.py clean
```

`build` configures CPython with `--with-pydebug`, no PGO or LTO, C/C++
`-O0 -g3`, and the overlay's Cargo `dev` profile. It runs offline and installs
`stage/bin/python3.16d`. `build --jit` enables the experimental JIT and
generates its stencils with the locked LLVM; a build without the flag keeps
the JIT disabled. `test` accepts repeated `--suite test_NAME` options
and runs only those complete CPython Python test modules or packages.
`test --all` runs all CPython test modules under the test runner's default
resource policy on the integrated tree. Each test file has a 900-second
timeout. Neither command runs Cargo tests. Choose every suite relevant to the
public behavior before editing; a passing subset is not a coverage result.
Each agent's Git worktree has its own build and stage tree. `build --jobs N`
and `test --jobs N` let the coordinator divide CPU capacity among lanes.

For a candidate, edit real source files under `overlay/`, using paths relative
to the pinned CPython source root. The builder copies those files over a
fresh verified source extraction before Cargo fetch and build. For example,
`overlay/Modules/_bz2_rs/src/lib.rs` could hold a future Rust codec source.
Put Rust in `.rs` files, Python in `.py` files, and C integration in C or
header files. For a new extension, put its module declaration in
`overlay/Modules/Setup.local`; the builder copies it to CPython's build
directory after configure. Do not embed Rust source in Python strings or patch
hunks. An edited `Cargo.lock` is an ordinary overlay file and `fetch` caches
its approved dependencies; `build` checks it offline. The overlay refuses
changes under `Lib/test/`, keeping the CPython suite unchanged. The generated
build report records one digest for the overlay. Optimized builds, profiles,
and paired measurements use `perf.py` (`python3 rust-cpython/perf.py --help`).

## Porting and evidence

Use a maintained Rust library where its semantics fit. Vetted Rust crates
with compatible recorded licenses and pinned `Cargo.lock` entries are
preauthorized for this isolated lane. Keep CPython's Python and C-ABI
boundaries where they carry public object, callback, or exception behavior. A
route's line in `ROUTES.md` names the suites that must stay green when its
implementation changes or moves. Check a public import and representative
call in a CPython subinterpreter before handing off a candidate.

Develop in a committed branch and isolated worktree. Keep candidate source in
Git under `overlay/`. Put the public Rust route, crate licenses, target,
focused-suite command, and pass/skip counts in the implementation commit
message. Generated interpreters, compiler output, and raw test logs remain
ignored. Avoid per-attempt reports and coordinator bookkeeping commits.

`linux-toolchain.lock.json` pins the x86-64 LLVM and Ubuntu package inputs.
Ubuntu 24.04 has no first-party libmpdec, so the Linux build compiles the root
lock's `mpdecimal` source into a private static library for `_decimal`. The
macOS lane uses the root `bootstrap.lock.json` LLVM 23.1.4 and Xcode SDK
identity. `fetch` verifies inputs; build and install use an offline boundary
with a network-denial self-test. The Linux boundary uses user and network
namespaces; macOS uses `sandbox-exec`.

## Performance harness

`python3 rust-cpython/perf.py` builds optimized interpreters (`-O2`, Cargo
`release` with ThinLTO and std built from the pinned `rust-src`, no PGO or
tail-call interpreter; `--jit` opts into the experimental JIT), runs suites on
them, profiles, and measures paired comparisons. It is the only entry point while measurements may run: it holds
the host lease, and the coverage `build.py build` and `test` commands bypass
it. On Linux the optimized harness uses the same locked Ubuntu 24.04 userland
and `inputs.linux-cp316.lock.json`; the macOS harness uses the macOS lock.

| Command | Purpose |
| --- | --- |
| `build --name N [--incremental] [--jobs J] [--jit]` | Clean optimized build verified per Rust extension; `--incremental` syncs changed overlay files and refuses build-system, Cargo, or JIT-flag changes |
| `build --name perf-upstream --empty-overlay` | Pristine-upstream control |
| `build --name perf-upstream-jit --empty-overlay --jit` | Pristine-upstream JIT control |
| `build --name N --upstream-compression` | Link CPython's stock `zlib`, `_bz2` and `binascii` against static `zlib-rs` and `libbz2-rs` archives of the locked crates, and omit the private `_zlib_rs`, `_bz2_rs` and `Lib/zlib.py` |
| `test --name N --suite test_X ...` or `--all` | Complete CPython suites on a perf build |
| `cargo --name N -- <cargo args>` | Sync changed overlay files as `build --incremental` does, then run the pinned cargo in build `N`'s assembled source with the build's environment; exits with cargo's status |
| `profile --ref R (--module M \| --workload W) --tool sample\|cprofile\|importtime` | Native stacks, Python call costs, or import time |
| `bench --baseline R --candidate R [--module M] [--workload W] [--gate] [--jit-set]` | Paired measurement with a coded verdict |
| `jit-stats --ref R (--workload W \| --jit-set)` | JIT residency, warmup ratio, libpython text/data size, and `sys._jit` state; diagnostic only |
| `calibrate --ref R --gate` | Self-comparison; identical interpreters must not read different |
| `goals [--candidate R] [--module M ...]` | Module rows against the control |
| `setup-worktree` | Share verified caches from the primary checkout into a worktree |
| `status` | Lease holders and every perf build with commit, flags, and suite state |

`@control` is the primary checkout's `perf-upstream` and `@incumbent` its
`perf-rust`; `@control-jit` and `@incumbent-jit` are their JIT builds. A bare
name is a build in the current worktree. `jit-stats` reports anonymous
executable mapping count and RSS from `/proc/<pid>/smaps` on Linux, the
first-pass over median-pass CPU ratio in one process, and the installed
libpython text/data sizes. It diagnoses only and never feeds a verdict. Builds, suites,
and profiles share the host, while `bench` and `calibrate` hold it exclusively
(`WAIT  host lease busy` is normal). A bench refuses a stage whose bytes
changed since its build. `--gate` requires clean builds of committed overlays,
a candidate that contains the incumbent commit, the `GATE_WORKLOADS` guards,
and two runs. Runs without `--gate` steer and are never acceptance evidence.
`--memory-only` skips timing. Verdicts follow [ENGINEERING.md](ENGINEERING.md).

Builds and `test --all` outlast a 10-minute foreground limit; run them in the
background. A clean perf build takes about 7-13 minutes; a worktree build
costs about 3 GB under `work/perf/<name>/` and `stage-perf-<name>/`;
`perf.py clean --name N` removes one. Gates run from long worktree paths have
biased startup-bound guards (`import_django`, `python_startup`) by 1-3%
against the primary-path incumbent, so gate an integration batch in the
primary checkout. When two lanes' `overlay/Cargo.lock` edits merge textually
but `cargo fetch --locked` rejects the result, regenerate it with `cargo
metadata --offline` in the build's `work/perf/<name>/source/cpython-*/` using
the primary `rust-cpython/.cargo-home`, copy the lock back, and rebuild.
