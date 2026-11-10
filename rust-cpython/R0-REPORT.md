# R0 report: refresh to CPython 3.16 `main` (macOS arm64)

## Source

- `python/cpython` `main` = `e891a9117985888df3e5d48145cf07ddda098335` (3.16.0a1+dev,
  resolved 2026-11-09 with `git ls-remote ... refs/heads/main`).
- Archive `https://codeload.github.com/python/cpython/tar.gz/e891a9117985888df3e5d48145cf07ddda098335`
  sha256 `af50f4ca750e1a28e6dc197a7f23bc1ae6ce5fe25cee6caf52eaa275065877fe`, size `36219192`.
- `rust-cpython/sources.lock.json`: branch/commit/version/url/sha256/size updated.
  `overlay/Cargo.lock` did not change; its sha256 still matches `cargo_lock_sha256`
  (`52922e3d…50c21e8`).
- `python3 rust-cpython/build.py fetch` exit 0 (archive verified, LLVM 23.1.4
  extracted, cargo deps cached).

`perf.py setup-worktree` could not run: the primary checkout has no `.cache/objects`
(`FAIL primary checkout lacks …/.cache/objects`). The worktree was fetched directly.

## Overlay port

Upstream a1→main diff was read for every overlay-replaced path; files were rebased
with `git merge-file` (base=a1, ours=overlay, theirs=main) and conflicts resolved by
lane intent.

- `Lib/*.py` (34 files: `_strptime`, `argparse`, `asyncio/base_events`, `collections`,
  `concurrent/futures/_base`, `configparser`, `functools`, `gzip`, `hmac`, `http/client`,
  `importlib/_bootstrap_external`, `importlib/metadata`, `inspect`, `ipaddress`, `json`,
  `logging`, `lzma`, `multiprocessing/connection`, `pickle`, `shutil`, `socket`,
  `statistics`, `subprocess`, `tarfile`, `threading`, `tokenize`, `typing`,
  `urllib/parse`, `urllib/request`, `uuid`, `zipfile`, `zipimport`): rebased onto
  e891a911 with the lane's Rust dispatch/lazy-import edits kept.
  Notable resolutions: `configparser` keeps lazy `re`/`_LazyRegex` plus upstream
  annotations and sorted `__all__`; `gzip` keeps the lane decompressor/`_crc32` with
  upstream's chunked member scan; `lzma` keeps the Rust fast path before upstream
  `_streams._decompress_all`; `uuid` keeps `_uuid_rs` and adopts `_gen_random`;
  `logging`/`json`/`tarfile`/`tokenize` keep lane imports and adopt upstream lazy
  `warnings`/`__getattr__`.
- `Lib/_plistlib_py.py`: the moved pure-Python writer received upstream's three
  `struct.pack` format changes. `Lib/plistlib.py` shim kept unchanged (wholesale rewrite).
- Build glue: `Makefile.pre.in`, `configure`, `configure.ac`, `Misc/platform_triplet.c`,
  `Modules/makesetup`. Upstream's bootstrap module split (`BOOTSTRAP_LIBRARY_OBJS`,
  `BOOTSTRAP_MODLIBS`, `Modules/Setup.bootstrap.in`, `makesetup -b`), `PLATFORM_MAIN_OBJS`,
  `RUNSHARED_FOR_BUILD`, and `LLVM_PROF_SHOW` were merged with the lane's `BLDSHARED_EXE`/
  `BLDSHARED_ARGS`, `RUST_TARGET`/`LLVM_TARGET`, cargo plumbing, and
  `cpython-rust-staticlib` link dependencies. `makesetup` was reconstructed from new
  upstream structure plus the lane's Rust-source branch.
- C modules: `_datetimemodule.c`, `_decimal/_decimal.c`, `_io/textio.c`,
  `_sqlite/cursor.c`, `_zstd/compressor.c`, `_zstd/decompressor.c`, `binascii.c`,
  `itertoolsmodule.c`, `posixmodule.c`, `socketmodule.c`, `unicodedata.c`. Resolutions:
  `itertools` keeps Rust step dispatch inside upstream's `@critical_section`
  `chain_next_impl` (`overlay/Modules/itertoolsmodule.c:2186`); zstd adopts upstream
  "no `tp_clear`" (`compressor.c:925`, `decompressor.c:818`) while keeping Rust
  members in traverse/dealloc; sqlite keeps the Rust column/step route and adopts
  upstream `pysqlite_get_ref` (`cursor.c:469`).
- `Python/marshal.c`: merged (upstream deterministic set ordering included).
  `Python/jit.c` and `Python/optimizer.c` are byte-identical upstream between a1 and
  main, so no rebase was needed. The lane `effective_trace_length` fix stays: upstream
  still uses `is_terminator()` (`overlay/Python/optimizer.c:1593`).
- `Lib/ssl.py` and `Lib/rtls/*`: deliberately not rebased; TLS stays out per
  `docs/CAMPAIGN-RUST-CPYTHON-JIT.md` Boundary. Upstream ssl.py changes are unported.

## LLVM and stencils

JIT used the locked LLVM 23.1.4 via `build.py:1017` (`LLVM_VERSION`, 
`LLVM_TOOLS_INSTALL_DIR`) and `build.py:751` (archive-extracted object tools).
No `Tools/jit` patch was needed. Both JIT builds generated
`jit_stencils-aarch64-apple-darwin.h` (2,075,941 and 2,075,937 bytes); build logs name
only `…/.cache/llvm/toolchains/23.1.4-5d3179…/bin/clang`.

## Builds (all from worktree root, `TMPDIR=$PWD/.work/tmp`)

| Command | Exit | Result |
| --- | ---: | --- |
| `python3 rust-cpython/perf.py build --name perf-upstream --empty-overlay` | 0 | OK, 207 s |
| `python3 rust-cpython/perf.py build --name perf-upstream-jit --empty-overlay --jit` | 0 | OK, 457 s |
| `python3 rust-cpython/perf.py build --name perf-rust` | 0 | OK, 58 release Rust extensions, 603 s |
| `python3 rust-cpython/perf.py build --name perf-rust-jit --jit` | 0 | OK, 58 release Rust extensions, 619 s |

## Targeted suites

Both JIT builds ran the prescribed command:
`python3 rust-cpython/perf.py test --name <build> --suite test_capi.test_opt --suite test_float --suite test_long --suite test_sys_settrace --suite test_monitoring --suite test_generators --suite test_frame --suite test_gc --suite test_weakref --suite test_jit`

- `perf-upstream-jit`: exit 1. 9 tests OK; `test_jit` failed (uncaught exception).
  Log: `rust-cpython/logs/perf-upstream-jit-test_capi.test_opt-…-test_jit.log`.
- `perf-rust-jit`: exit 1. 9 tests OK; `test_jit` failed identically.
  Log: `rust-cpython/logs/perf-rust-jit-test_capi.test_opt-…-test_jit.log`.
- Both: `ModuleNotFoundError: No module named 'test.test_jit'`; this SHA has no
  `Lib/test/test_jit.py` (only `Lib/test/test_gdb/test_jit.py`). The report status
  `suite-failed` on both JIT builds is this invalid suite name, not a code failure.
  Each build ran 1,311 tests, skipped 18. Parity holds: `perf-rust-jit` fails no test
  that `perf-upstream-jit` passes.
- `perf.py` accepted dotted suite names after the one-line validation fix at
  `rust-cpython/perf.py:1306`.

## Differential fuzzer

`python3 rust-cpython/tests/jit_fuzz.py --python rust-cpython/stage-perf-rust-jit/bin/python3.16 --programs 2000 --seed 0`
exit 0:
`{"jit_coverage": 0.9745, "mismatches": 0, "nondeterministic": 0, "programs": 2000, "seed": 0}`.

## 3.16.0a1 upstream findings (pristine pydebug JIT built in scratch at the same SHA)

Release perf builds reject `-R` ("requires a debug build"), so a pristine pydebug JIT
was configured from the same archive with the locked clang/LLVM and `work/jit-tools`
on PATH. Stencil generation used release-style output (scratch Makefile `--debug`
removed) because the debug unwind-info path was not needed for these tests.

- (a) settrace one-block leak: **not reproduced**. Probes over `def f(): return 1`
  with 8 calls under `sys.settrace`, after a function-entry `sys.monitoring`
  `PY_START` phase, on `perf-upstream`, `perf-upstream-jit`, `perf-rust-jit`, and the
  pydebug JIT build: `[14,2,2,2,2]` and `[3,1,1,1,1]` per 8-call batch; JIT and
  no-JIT identical, no per-call growth.
- (b) `test_capi.test_opt` + `test_monitoring` from repeated `-R`: **still present,
  JIT-specific**. On the pydebug JIT build: `./python.exe -m test -R 3:3
  test_capi.test_opt test_monitoring` exit 2 (`test_capi.test_opt`: 21 errors,
  238 failures; `test_monitoring`: 1 failure, 103 events not > 250). Controls pass:
  same build without `-R` (exit 0), and `PYTHON_JIT=0` with and without `-R` (exit 0).
- (c) `test_optimizer` leak: **still present**. `-R 3:3 test_optimizer` reports
  `leaked [6, 6, 6] references, sum=18` and `[5, 5, 5] memory blocks, sum=15`, both
  with JIT on and with `PYTHON_JIT=0`.

## Left / unverified

- Only the prescribed targeted suites were run, plus the three finding probes; no
  `test --all`, no broad pydebug suite, and no Linux run.
- `overlay/Modules/_marshal_rs` was not touched (build did not require it). Upstream
  marshal.c now orders interchangeable set elements deterministically; `_marshal_rs`
  byte-parity for sets was not rechecked.
- `perf-upstream` and `perf-rust` were built but not tested in this continuation.
- The pydebug JIT oracle is a scratch build under `.work/tmp/r0/dbgbuild`, not a named
  lane build; its stencil headers carry no debug unwind info.
