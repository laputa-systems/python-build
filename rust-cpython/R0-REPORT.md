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

## Fix A: post-rebase overlay parity (macOS arm64)

Full-suite runs (`logs/perf-rust-all.log` vs `logs/perf-upstream-all.log`) gave
five overlay-only failing suites, all attributed to the rebase of the overlay
onto e891a911. Five root causes, all fixed in `overlay/`:

1. **`test_uuid` (309 errors, 100 failures; now passes).** Upstream's new tail
   block `from _uuid import UUID, uuid4, uuid7` rebinds the module global
   `UUID` to the C type before the pure-Python factories run. The overlay's
   `UUID._from_int(int_uuid_N, version=N)` calls then hit
   `_uuid.UUID._from_int` (`METH_CLASS|METH_O`), which rejects keywords:
   `TypeError: UUID._from_int() takes no keyword arguments` at
   `overlay/Lib/uuid.py:966` (uuid7) and siblings. Upstream already ORs the
   RFC version flags before the call, so the kwarg was redundant. `_from_int`
   is back to upstream's single-argument form with the lane's `_normalize`
   dispatch (`uuid.py:341`), and uuid6/7/8 call it positionally
   (`uuid.py:894`, `:966`, `:997`). The 100 `test_exact_same_algo_uuid4`
   failures had a second cause: the Rust fast path `_uuid_rs.uuid4()`
   bypassed `_gen_random`, so `_install_py_hooks` entropy never reached
   `_py_uuid4` while the C twin consumed the seeded C hooks. `uuid4` now uses
   the hook-aware path whenever `_random_hook` is installed (`uuid.py:840`).

2. **`test_importlib` (3 failures; now passes).** Upstream's `Lookup` now
   walks `sorted(path.children(), key=str.lower, reverse=True)` so a path
   with several metadata directories prefers the greater version. The Rust
   `_metadata_rs.scan_path` returned `read_dir` order, so
   `test_importlib.metadata.test_main.MultipleVersionsTests` failed. The Rust
   path now sorts its matches by lowercased name descending
   (`overlay/Modules/_metadata_rs/src/lib.rs:110`), matching upstream's
   reverse-lexicographic preference.

3. **`test_shutil` (1 error, 1 failure; now passes).** The Rust
   `copytree` fast path always calls `copystat` on directories, so
   `copytree(..., copy_dir_stat=False)` copied the source mode (`0o555`) and
   the test's `rmtree` then hit `PermissionError`. The fast path is now taken
   only when `copy_dir_stat` is true (`overlay/Lib/shutil.py:686`).

4. **`test_cext` (1 error; now passes).** `test_cext` builds a venv, and the
   bundled pip 26.3 installs an `_ImportBlocker` that rejects every import
   after installation starts. `overlay/Lib/csv.py` imported `_csv_rs` lazily
   on first reader use, so ensurepip failed with
   `Blocked import of '_csv_rs' after pip install started`. `_csv_rs` is now
   imported eagerly next to `_csv` (`overlay/Lib/csv.py:79`), as upstream does
   for the C extension.

5. **`test_marshal` (1 failure; now passes).** Upstream added deterministic
   ordering for set elements with equal dumps (`w_set_item_compare`,
   `w_same_set_items`, `w_tie_set_items` in `Python/marshal.c`). The overlay
   C file already carries that code; `_marshal_rs.write_set` did not: it
   sorted `(dump, element)` tuples with `PyList_Sort`, leaving equal-dump
   elements in set order, so `test_nan_in_set_order` saw NaNs take reference
   slots in address order. The Rust writer now carries upstream's tie array
   and algorithm: `TIED`/`SET_ITEM_NEW`, a `ties` array freed with the
   encoder, `track` taking the next interchangeable record on a later
   reference, `write_same_set_items` ordering already-written elements first
   and new floats/complexes as one tie group, and `tie_set_items` marking the
   group (`overlay/Modules/_marshal_rs/src/lib.rs:44`, `:558`, `:901`, `:971`,
   `:1032`). The stray tuple/list imports the old sort needed are gone.

**Acceptance (this worktree, `TMPDIR=$PWD/.work/tmp`):**

- `python3 rust-cpython/perf.py build --name perf-rust --incremental` exit 0
  ("58 release Rust extensions verified"). A concurrent R1 measurement was
  recorded by the lease (`WAIT host lease busy: measure cinderx steady
  kernels`); the build waited and completed.
- `rust-cpython/stage-perf-rust/bin/python3.16 -m test -j4 test_uuid
  test_importlib test_shutil test_marshal test_cext` exit 0:
  `Total tests: run=1,756 skipped=134`; `Total test files: run=5/5`;
  `Result: SUCCESS`. All five suites OK.
- Same command on `rust-cpython/stage-perf-upstream/bin/python3.16` exit 0:
  `Total tests: run=1,756 skipped=134`; `Total test files: run=5/5`;
  `Result: SUCCESS`. perf-rust fails no test perf-upstream passes.
- `python3 rust-cpython/tests/marshal_parity.py --baseline perf-upstream
  --candidate perf-rust` exit 0: `mismatch_count=0`, `code_items=6216`,
  `value_items=119`, versions 0-5, `skipped_only_by_candidate=[]`.
- Repeated runs: `test_marshal` 5/5 `Result: SUCCESS` (the NaN order test is
  address- and hash-seed-dependent), `test_uuid` 3/3 `Result: SUCCESS`;
  `test_csv`/`test_shutil` 2/2 OK after the eager `_csv_rs` import.

**Unverified / not touched:** no full `test --all` rerun after the fixes
(only the five suites plus `test_csv`); `perf-rust-jit` and the other builds
were not rebuilt with these overlay changes; the Rust marshal tie port is
validated by `test_marshal` and the parity run, not by the full corpus again;
TLS paths were untouched.

## Fix B: remaining overlay-only failures (macOS arm64)

Three further suites failed only on `perf-rust` (`logs/perf-rust-all.log`, taken
before Fix A). Each was the lane's Rust fast path not producing the observable
behaviour of the upstream path it replaced; all three are fixed in `overlay/`.
`ssl.py`, `_ssl_rs`, RTLS and the rest of the TLS code were not touched.

1. **`test_gzip.test_decompress_many_members` (test_gzip.py:873) and
   `test_lzma.test_decompress_many_streams` (test_lzma.py:563): `0 not less
   than 0`.** Both post-a1 tests replace the decoder factory
   (`zlib.decompressobj`, `lzma.LZMADecompressor`) with a counter and assert the
   input fed grows linearly with the number of members. `gzip.decompress` built
   each member through `_new_decompressor` (now `overlay/Lib/gzip.py:800-802`)
   and `lzma.decompress` returned `_rust_lzma.decompress_xz` directly (now
   `overlay/Lib/lzma.py:412`), so the replaced factory was never constructed and
   both `fed` counts stayed 0. `gzip.decompress` now calls `zlib.decompressobj` when it is neither the
   C builtin nor the lane's Rust wrapper (`zlib._rust_decompressobj`), keeping
   the Rust decoder otherwise; `lzma.decompress` takes the
   Rust fast path only while the module global is the C class
   (`lzma.py:32` imports `_lzma.LZMADecompressor`; `lzma.py:407-408` requires
   `LZMADecompressor is _CPythonLZMADecompressor`) and otherwise uses upstream's
   `_streams._decompress_all`. Upstream behaviour (which reads the callers can
   observe) is the oracle; the unpatched fast path is unchanged.
2. **`test_hmac.test_hmac_digest_reject_memoryview_for_key` (test_hmac.py:1550):
   `TypeError not raised`.** With `_hashlib` and `_hmac` blocked, upstream's
   `hmac.digest` reaches `_compute_digest_fallback`, whose
   `isinstance(key, (bytes, bytearray))` check raises `TypeError` for a
   `memoryview` key. The lane consulted `_hmac_rs.compute_digest` before the
   fallback, and it accepted the buffer. The Rust call is now gated on
   `isinstance(key, (bytes, bytearray))` (`overlay/Lib/hmac.py:406-407`), so a
   non-bytes key reaches the upstream fallback and raises. `hmac.HMAC` already
   had upstream's key check at the top of `__init__`.

**Acceptance (this worktree, `TMPDIR=$PWD/.work/tmp`):**

- `python3 rust-cpython/perf.py build --name perf-rust --incremental` exit 0:
  `OK incremental overlay sync: 3 changed file(s)`, `OK perf CPython 3.16.0a1+dev
  (incremental, 58 release Rust extensions verified, 95s)`.
- `rust-cpython/stage-perf-rust/bin/python3.16 -m test -j6 test_gzip test_lzma
  test_hmac test_zlib test_bz2 test_tarfile test_zipfile` exit 0:
  `All 7 tests OK.` / `Total tests: run=1,964 skipped=17` /
  `Total test files: run=7/7` / `Result: SUCCESS`.
- Same command on `stage-perf-upstream` exit 0: `All 7 tests OK.` /
  `Total tests: run=1,964 skipped=17` / `Total test files: run=7/7` /
  `Result: SUCCESS`. `perf-rust` fails no test `perf-upstream` passes.

### Classification: asyncio sendfile/sslproto, test_ssl, test_support

Each suite was run verbosely on both builds (`-m test -v <suite>`, per suite).
`perf-rust` fails all four; `perf-upstream` passes all four. The TLS-side
modules are not an overlay difference: `asyncio/sslproto.py` is byte-identical
between the builds (sha256 `5e2fd44d…9586`), and the `asyncio/base_events.py`
overlay diff is only `_asyncio_rs` ready-queue/timer dispatch, not the sendfile
or fallback path. `overlay/Lib/socket.py` differs only in `inet_pton`/`inet_ntop`
and the `_socket_rs` import gate; the non-SSL sendfile tests pass on `perf-rust`.

| Failing test | Class | Evidence |
| --- | --- | --- |
| `test_asyncio.test_sendfile`: `Kqueue/Poll/SelectEventLoopTests.test_sendfile_ssl_close_peer_in_the_middle_closes_transport` (3 failures, `ConnectionError not raised`, test_sendfile.py:580) | (a) frozen TLS stack | New after a1: absent from v3.16.0a1's `Lib/test/test_asyncio/test_sendfile.py`, which has only the two non-SSL `*_close_peer_in_the_middle_of_receiving` tests. An instrumented run (`.work/tmp/probe_sendfile.py`, scratch) shows the difference is EOF/close semantics: on `perf-upstream` the peer's close surfaces mid-transfer as `SSLError(1, '[SSL: APPLICATION_DATA_AFTER_CLOSE_NOTIFY] … (_ssl.c:3303)')` and `BrokenPipeError`, so `_SendfileFallbackProtocol.drain()` raises `ConnectionError` (`asyncio/base_events.py:229-231`); on `perf-rust` all 69 drains see `is_closing()=False` and `connection_lost(None)` is delivered only at cleanup, so sendfile returns normally. The lane's frozen RTLS stack maps the peer's close_notify to a clean EOF where upstream's OpenSSL `_ssl` reports a fatal error. |
| `test_asyncio.test_sslproto`: `SslProtoHandshakeTests.test_pause_reading_if_ssl_replies_are_not_read` (`transport.write` called once) and `test_no_pause_reading_if_app_data_is_not_read` (called twice) | (a) frozen TLS stack | Both tests are new after a1 (absent from a1's `test_sslproto.py`), added with upstream's outgoing accounting that a1's `sslproto.py` lacks (`_outgoing_app_size` at main `Lib/asyncio/sslproto.py:311`, `_outgoing_app_size +=` at `:755`, `_process_outgoing` at `:758`). The lane has that upstream `sslproto.py`, but its frozen `ssl` differs: `ctx.wrap_bio(...)` leaves a 1460-byte ClientHello pending on `perf-rust` and 0 bytes on `perf-upstream` (direct probe on both stages), so `_process_outgoing()` writes it to the mocked transport and violates `assert_not_called()`/`assert_called_once_with`. |
| `test_ssl`: file-level `AttributeError: module 'ssl' has no attribute 'HAS_ECH'` at import (`test_ssl.py:2065`, `class ECHTests`) | (b) a1 known-red + (a) frozen TLS stack | Recorded a1 known red as `test_ssl` "RTLS policy gaps" (`rust-cpython/ENGINEERING.md:129-134`). Concrete instance is ECH, added after v3.16.0a1: a1's `Lib/ssl.py` has no `HAS_ECH` and a1's `test_ssl.py` has no ECH tests, while main's `Lib/ssl.py:121` imports `HAS_ECH` and `test_ssl.py:2065` requires it. The lane's `ssl.py` is the unrebased RTLS shim (`from rtls import *`), so the import aborts the whole suite (`run=0`). |
| `test_support`: 14 errors, all `TestHashlibSupport.test_disable_hash` subTests (`allow_openssl=True, allow_builtin=False`) raising `ValueError: unsupported hash algorithm <name>` | (b) a1 known-red | Recorded a1 known red as `test_support` "hashlib shim" (`ENGINEERING.md:129-134`). The lane's `hashlib.__rust_new` probes `__get_builtin_constructor` and raises when the test blocks it (`overlay/Lib/hashlib.py:308-312`), instead of serving the algorithm through the Rust/OpenSSL route the helper allows. Fixing it would need `Lib/hashlib.py`, outside this task's writable paths. |

No (c) new non-TLS regression was found in these four suites.

**Results (individual verbose runs):**

| Suite | `perf-rust` | `perf-upstream` |
| --- | --- | --- |
| `test_asyncio.test_sendfile` | exit 2; `Ran 72 tests`; `FAILED (failures=3)`; `Total tests: run=72 failures=3`; `Total test files: run=1/1 failed=1`; `Result: FAILURE` | exit 0; `Ran 72 tests`; `OK`; `Total tests: run=72`; `Total test files: run=1/1`; `Result: SUCCESS` |
| `test_asyncio.test_sslproto` | exit 2; `Ran 52 tests`; `FAILED (failures=2, skipped=17)`; `Total tests: run=52 failures=2 skipped=17`; `run=1/1 failed=1`; `Result: FAILURE` | exit 0; `Ran 52 tests`; `OK (skipped=17)`; `Total tests: run=52 skipped=17`; `run=1/1`; `Result: SUCCESS` |
| `test_ssl` | exit 2; `Total tests: run=0`; `Total test files: run=1/1 failed=1`; `Result: FAILURE` | exit 0; `Ran 243 tests`; `OK (skipped=19)`; `Total tests: run=243 skipped=19`; `run=1/1`; `Result: SUCCESS` |
| `test_support` | exit 2; `Ran 78 tests`; `FAILED (errors=14, skipped=6)`; `Total tests: run=78 skipped=6`; `run=1/1 failed=1`; `Result: FAILURE` | exit 0; `Ran 78 tests`; `OK (skipped=6)`; `Total tests: run=78 skipped=6`; `run=1/1`; `Result: SUCCESS` |

**Unverified / not touched:** no full `test --all` rerun after Fix B (only the
seven compression suites and the four classified suites); `perf-rust-jit` and
the other builds were not rebuilt with Fix B; the a1 comparison uses the
`v3.16.0a1` tag files fetched into `.work/tmp/a1` (peeled commit `22d79d98`),
not an a1 build; TLS code was untouched.

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

## Step 4 parity after Fix B (macOS arm64)

`perf.py test --all`, after an incremental rebuild of `perf-rust` and
`perf-rust-jit` on `99cce2b`:

| Build | Failed files |
| --- | --- |
| `perf-upstream` | `test_multiprocessing_forkserver.test_misc` |
| `perf-upstream-jit` | `test_multiprocessing_forkserver.test_misc`, `test_concurrent_futures.test_process_pool` (flaky) |
| `perf-rust` | upstream-red above; known red `test_ssl`, `test_support`; frozen-TLS `test_asyncio.test_sendfile`, `test_asyncio.test_sslproto` |
| `perf-rust-jit` | same set as `perf-rust` |

A first run at load average 15-17 also failed `test_socket`, `test_tarfile`,
`test_concurrent_futures.test_shutdown` and `test_os.test_os` with
`os.getcwd()` raising `FileNotFoundError`; all four pass in isolation on the
overlay and upstream builds and did not recur in the confirming run.

Fuzzer, `perf-rust-jit`, 2,000 programs: seeds 0, 1, 2 give 0 mismatches,
0 nondeterministic (JIT coverage 0.9745, 0.9775, 0.9725).

`perf.py` has no pydebug build mode; pydebug evidence is the pristine scratch
build above.
