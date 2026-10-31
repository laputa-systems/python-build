# Rust-for-CPython research lane

This isolated lane builds the pinned Rust-for-CPython CPython **3.16.0a0** fork.
All 71 named public-behavior coverage targets in
[`rust-for-cpython.md`](../rust-for-cpython.md) passed their complete relevant
CPython Python module suites and the combined default-resource suite on
Linux x86-64. On Linux the only failure is one VSOCK
test that also fails on the pristine fork, because the host kernel has no
VSOCK loopback. They also passed on macOS arm64, including rerun at
`9329e1c` with the shared-overlay uuid, codec (`encodings.utf_8`), socket,
and stdlib-module-list repairs included (`b0888dd`, `499308e`, `4710c23`):
50,158 run/2,703 skipped with zero failures, matching the pre-repair counts
exactly at that historical coverage checkpoint. The later parity run has
explicit accepted failures; see the live campaign in
[`rust-for-cpython.md`](../rust-for-cpython.md). [`ROUTES.md`](ROUTES.md)
lists every route's public behavior and qualifying suites.

The production CPython 3.15.1 build, packaging, and pinned Linux recipes are
outside this lane. The coverage builder runs on native macOS arm64 and Linux
x86-64; the optimized `perf.py` harness currently builds only on macOS. The
M1 head's Linux x86-64 requalification (2026-10-31) matches the accepted
known-red set; see the live campaign. Linux arm64 has no builder. Windows,
Intel macOS, and all other targets are unsupported; the builder rejects them.

The verified source is `Rust-for-CPython/cpython` commit
`b812b4a7b9efaca46b98544a8633b7d7e454166b`, pinned with its archive
and Cargo lock digests in [`sources.lock.json`](sources.lock.json). The Rust
toolchain is pinned in [`rust-toolchain.toml`](../rust-toolchain.toml). The
private Cargo home is used offline after fetching. Do not update the source,
toolchain, or Cargo lock accidentally; intentional vetted crate additions
update the overlay lockfile.

## Active commands

```sh
python3 rust-cpython/build.py doctor
python3 rust-cpython/build.py fetch
python3 rust-cpython/build.py build
python3 rust-cpython/build.py test --suite test_bz2
python3 rust-cpython/build.py test --all
python3 rust-cpython/build.py clean
```

`build` now configures CPython with `--with-pydebug`, no PGO or LTO, C/C++
`-O0 -g3`, and the fork's Cargo `dev` profile. It runs offline and installs
`stage/bin/python3.16d`. `test` accepts repeated `--suite test_NAME` options
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
directory after configure. Do not embed Rust source in
Python strings or patch hunks. An edited `Cargo.lock` is an ordinary overlay
file and `fetch` caches its approved dependencies; `build` checks it
offline. The overlay refuses changes under `Lib/test/`, keeping the CPython
suite unchanged. The generated build report records one digest for the overlay.
The coverage builder has no PGO or benchmark mode; optimized builds,
profiles, and paired measurements for the performance phase use
`perf.py` (`python3 rust-cpython/perf.py --help`).

## Porting and evidence

Use a maintained Rust library where its semantics fit. Vetted Rust crates
with compatible recorded licenses and pinned `Cargo.lock` entries are
preauthorized for this isolated lane. Keep CPython's Python and C-ABI boundaries
where they carry public object, callback, or exception behavior. A route's
line in `ROUTES.md` names the suites that must stay green when its
implementation changes or moves.
Check a public import and representative call in a CPython subinterpreter
before handing off a candidate. Single-phase private extensions can reject
loading there, so their public wrapper must use a compatible fallback or the
extension must support multi-phase loading. Assert that
`_interpreters.run_string()` returns `None`; an exception in the child is
returned as a value and can leave the parent process exit status at zero.

Develop in a committed branch and isolated worktree. Keep candidate source
in Git under `overlay/`. Put the public Rust route, crate licenses, target,
focused-suite command, and pass/skip counts in the implementation commit
message. The coordinator records the integrated full-suite result beside
the completed checklist item. Generated interpreters, compiler output, and
raw test logs remain ignored. Avoid per-attempt reports and coordinator
bookkeeping commits.

`linux-toolchain.lock.json` pins the x86-64 LLVM and Ubuntu package inputs.
Ubuntu 24.04 has no first-party libmpdec, so the Linux build compiles the
root lock's `mpdecimal` source into a private static library for `_decimal`.
The macOS lane uses the root `bootstrap.lock.json` LLVM 23.1.2 and Xcode SDK
identity. `fetch` verifies inputs; build and install use an offline boundary
with a network-denial self-test. The Linux boundary uses user and network
namespaces; macOS uses `sandbox-exec`.

## Performance harness

`python3 rust-cpython/perf.py` builds optimized interpreters (`-O2`, Cargo
`release`, no PGO, LTO, JIT, or tail-call interpreter), runs suites on them,
profiles, and measures paired comparisons. It is the only entry point while
measurements may run: it holds the host lease, and the coverage
`build.py build` and `test` commands bypass it. `perf.py build` refuses
Linux hosts; Linux coverage builds and suites use `build.py`.

| Command | Purpose |
| --- | --- |
| `build --name N [--incremental] [--jobs J]` | Clean optimized build verified per Rust extension; `--incremental` syncs changed overlay files and refuses build-system or Cargo changes |
| `build --name perf-upstream --empty-overlay` | Pristine-fork control |
| `build --name N --upstream-compression` | Link CPython's stock `zlib`, `_bz2` and `binascii` against static `zlib-rs` and `libbz2-rs` archives of the locked crates, and omit the private `_zlib_rs`, `_bz2_rs` and `Lib/zlib.py` |
| `test --name N --suite test_X ...` or `--all` | Complete CPython suites on a perf build |
| `cargo --name N -- <cargo args>` | Sync changed overlay files as `build --incremental` does, then run the pinned cargo in build `N`'s assembled source with the build's environment; exits with cargo's status |
| `profile --ref R (--module M \| --workload W) --tool sample\|cprofile\|importtime` | Native stacks, Python call costs, or import time |
| `bench --baseline R --candidate R [--module M] [--workload W] [--gate]` | Paired measurement with a coded verdict |
| `calibrate --ref R --gate` | Self-comparison; identical interpreters must not read different |
| `goals [--candidate R] [--module M ...]` | Module rows against the control |
| `setup-worktree` | Share verified caches from the primary checkout into a worktree |
| `status` | Lease holders and every perf build with commit, flags, and suite state |

`@control` is the primary checkout's `perf-upstream` and `@incumbent` its
`perf-rust`; a bare name is a build in the current worktree. Builds, suites,
and profiles share the host, while `bench` and `calibrate` hold it
exclusively (`WAIT  host lease busy` is normal). A bench refuses a stage
whose bytes changed since its build. `--gate` requires clean builds of
committed overlays, a candidate that contains the incumbent commit, the
`GATE_WORKLOADS` guards, and two runs. Runs without `--gate` steer and are
never acceptance evidence. `--memory-only` skips timing.

Builds and `test --all` outlast a 10-minute foreground limit; run them in
the background. A worktree build costs about 3 GB under
`work/perf/<name>/` and `stage-perf-<name>/`; `perf.py clean --name N`
removes one. Gates run from long worktree paths have biased startup-bound
guards (`import_django`, `python_startup`) by 1-3% against the primary-path
incumbent, so gate an integration batch in the primary checkout. When two
lanes' `overlay/Cargo.lock` edits merge textually but `cargo fetch --locked`
rejects the result, regenerate it with `cargo metadata --offline` in the
build's `work/perf/<name>/source/cpython-*/` using the primary
`rust-cpython/.cargo-home`, copy the lock back, and rebuild.
