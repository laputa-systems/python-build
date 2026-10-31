# Rust route register

Each line names a public stdlib behavior that the Rust-for-CPython research
interpreter routes to Rust, the complete CPython suites that qualified it,
and the coverage commit. The suites are the behavior contract: a change that
consolidates, replaces, or deletes a route keeps every suite on its line
green on a perf build, with baseline skips only. Counts are from the
2026-10 coverage qualification on macOS arm64 (debug); later parity
deviations are listed under Baseline in
[`rust-for-cpython.md`](../rust-for-cpython.md).

A route may move to a shared, upstream, or maintained implementation (for
example CPython's `zlibmodule.c` over the zlib-rs engine) under rule 8 of
the campaign contract. Update the line in the same commit.

## Common application paths

- [x] `urllib.parse` — quote, unquote, and query parsing reach Rust
  (`5ec48ce`). Full `test_urlparse`, `test_urllib`, `test_http_cookies`,
  `test_httpservers`, `test_logging`, `test_pathlib`, `test_pydoc`, and
  `test_sqlite3`: 2,628 run/430 skipped; integrated full suite passed.
- [x] `json` — public default encode and decode of complete documents reach
  Rust (`7c37dd0`). Full `test_json` and `test_interpreters`: 402 run/11
  skipped; integrated full suite passed.
- [x] `pickle` — public `dumps`/`loads` and `BytesIO` `dump`/`load` of
  supported graphs reach Rust (`771cf8f`, with source-inspection cleanup in
  `ced130b`). Full `test_pickle`, `test_picklebuffer`, `test_pickletools`,
  `test_shelve`, `test_email`, and `test_ctypes`: 4,079 run/121 skipped;
  `test_pyclbr` also passed after the cleanup. Integrated full suite passed.
- [x] `csv` — default Excel records reach Rust through public reader and
  writer (`e49c5e1`). Full `test_csv`: 134 run/0 skipped; integrated full
  suite passed.
- [x] `tomllib` — complete TOML documents reach Rust (`76fbdb4`). Full
  `test_tomllib` and `test_inspect`: 400 run/0 skipped; integrated full
  suite passed.
- [x] `email` — public message and header parsing reaches Rust for supported
  input, with serialization and other cases preserved by Python (`aa0c120`).
  Full `test_email`, `test_mailbox`, `test_http_cookiejar`, and `test_urllib2`:
  2,362 run/7 skipped; integrated full suite passed.
- [x] `xml.etree.ElementTree` — complete-document parsing and XML writing
  reach Rust (`bab729d`). Full `test_xml_etree` and `test_xml_etree_c`:
  480 run/12 skipped; integrated full suite passed.
- [x] `re` — public compilation and search of supported ASCII patterns reach
  Rust (`68ff4d1`). Full `test_re`: 169 run/3 skipped; integrated full suite
  passed.
- [x] `base64` — public encode and decode functions reach Rust (`5024bbf`).
  Full `test_base64`, `test_binascii`, and `test_email`: 2,123 run/19
  skipped; integrated full suite passed.
- [x] `binascii` — public hex conversion and checksums reach Rust
  (`481f5dd`). Full `test_binascii`, `test_base64`, `test_email`, and
  `test_zipfile`: 2,712 run/22 skipped; integrated full suite passed.
- [x] `zlib` — public one-shot and streaming compression and decompression
  reach Rust on macOS arm64 (`be46e24`). Full `test_zlib`, `test_codecs`,
  `test_gzip`, `test_tarfile`, `test_zipfile`, `test_zipimport`,
  `test_zipimport_support`, `test_zipapp`, `test_shutil`, `test_logging`,
  `test_sqlite3`, and `test_ensurepip`: 3,060 run/121 skipped;
  `test_zipfile64` was resource-denied at baseline. Integrated full suite
  passed.
- [x] `gzip` — public file, stream, and one-shot compression/decompression
  reach Rust (`8c2b4a1`). Full `test_gzip`, `test_tarfile`, `test_xmlrpc`,
  and `test_zlib`: 1,027 run/15 skipped; integrated full suite passed.
- [x] `zipfile` — public ZIP CRC and deflate read/write paths reach Rust
  (`26746c1`). Full `test_zipfile`, `test_zipimport`,
  `test_zipimport_support`, and `test_shutil`: 956 run/91 skipped;
  `test_zipfile64` was resource-denied at baseline. Integrated full suite
  passed.
- [x] `tarfile` — public USTAR header read and write reach Rust
  (`a4aef06`). Full `test_tarfile`, `test_shutil`, and `test_zipfile`:
  1,587 run/84 skipped; integrated full suite passed.
- [x] `pathlib` — public path parsing and `exists`, `is_dir`, and `is_file`
  reach Rust (`d74abd7`). Full `test_pathlib`, `test_shutil`, and `test_glob`:
  1,644 run/485 skipped; integrated full suite passed.
- [x] `os.path` — public normalization, joining, splitting, and root splitting
  reach Rust on macOS arm64 (`23dbe42`). Full `test_posixpath`,
  `test_genericpath`, `test_pathlib`, `test_os`, and `test_faulthandler`:
  2,113 run/519 skipped; integrated full suite passed.
- [x] `shutil` — public `copyfileobj` and eligible default `copytree` reach
  Rust; ZIP archive handling uses the qualified Rust-backed `zipfile` route
  (`9062434`). Full `test_shutil`, `test_zipfile`, and `test_tarfile`:
  1,587 run/84 skipped; integrated full suite passed.
- [x] `importlib.metadata` — public metadata parsing and distribution
  discovery reach Rust (`4af83b5`). Full `test_importlib` and `test_zoneinfo`:
  1,486 run/39 skipped; integrated full suite passed.
- [x] `hashlib` — public digest updates and finalization reach Rust for
  supported algorithms (`afd1d2c`). Full `test_hashlib`, `test_hmac`, and
  `test_uuid`: 354 run/33 skipped; integrated full suite passed.
- [x] `hmac` — public keyed digest operations reach Rust (`dd10097`, with
  hash-availability guard in `ada4895`). Full `test_hmac` and `test_hashlib`:
  234 run/17 skipped; `test_imaplib` and `test_support` regression suites
  also passed; integrated full suite passed.
- [x] `uuid` — parse, format, and common generation reach Rust (`6517691`).
  Full `test_uuid` and `test_os`: 667 run/120 skipped; integrated full suite
  passed.
- [x] `datetime` — public ISO parsing and formatting plus date and datetime
  timedelta arithmetic reach Rust (`da6281d`). Full `test_datetime`,
  `test_strptime`, `test_time`, `test_calendar`, `test_locale`,
  `test_zoneinfo`, and `test_pickle`: 2,776 run/235 skipped; integrated full
  suite passed.
- [x] `decimal` — eligible public `Decimal` addition and multiplication of
  exact nonnegative integers reach Rust (`7d23831`). Full `test_decimal`,
  `test_fractions`, `test_statistics`, and `test_numeric_tower`: 1,194 run/14
  skipped; integrated full suite passed.
- [x] `sqlite3` — public cursor statement stepping and ordinary column
  conversion reach Rust (`3df4e0a`). Full `test_sqlite3`,
  `test_dbm_sqlite3`, `test_dbm`, and `test_shelve`: 954 run/14 skipped;
  integrated full suite passed.
- [x] `io` — public buffered byte transfers and line scans, plus text newline
  normalization and write-policy scans, reach Rust (`1e06fcf`). Full `test_io`:
  1,031 run/27 skipped; 14 neighboring suites: 5,052 run/792 skipped;
  integrated full suite passed.
- [x] `logging` — public record creation, formatting, and handler dispatch
  reach Rust (`a0dd573`). Full `test_logging` and `test__interpreters`:
  354 run/7 skipped; integrated full suite passed.
- [x] `asyncio` — public non-debug ready-handle dispatch and due-timer
  promotion reach Rust (`41892d3`). Full `test_asyncio` and seven
  neighboring suites: 4,497 run/122 skipped, with two Windows-only files;
  integrated full suite passed.
- [x] `http.client` — public response status parsing and request-line sending
  reach Rust (`d2aca96`). Full `test_httplib`, `test_httpservers`,
  `test_urllib`, `test_urllib2`, `test_urllib2_localnet`,
  `test_http_cookiejar`, and `test_urllib_response`: 538 run/13 skipped;
  integrated full suite passed.
- [x] `ipaddress` — IPv4/IPv6 string parsing and network bounds reach Rust on
  macOS arm64 (`249d0cb`). Full `test_ipaddress`, `test_socket`, and
  `test_concurrent_futures`: 1,363 run/284 skipped. Integrated default-resource
  suite: 50,158 run/2,703 skipped, zero failures (496/505 files; nine
  resource-denied).
- [x] `socket` — public IPv4/IPv6 `inet_pton`/`inet_ntop` and eligible blocking
  socket `send`/`recv` reach Rust (`609b503`). Full `test_socket` and six
  neighboring suites: 4,033 run/396 skipped; `test_socketserver` was
  resource-denied at baseline. Integrated full suite passed.
- [x] `ssl` — public TLS context setup and MemoryBIO handshake/read/write
  steps reach Rust (`8ab7252`). Full `test_ssl` and 18 neighboring suites:
  5,532 run/403 skipped, including two Windows-only asyncio files;
  integrated full suite passed.
- [x] `subprocess` — eligible POSIX launches and ready-pipe communication
  reach Rust (`0032df8`). Full `test_subprocess` and 12 neighboring suites:
  5,370 run/412 skipped, including baseline Windows and macOS fork skips;
  integrated full suite passed.
- [x] `multiprocessing` — public queue and pipe framing and Pool task
  batching reach Rust (`99fce8e`). Full fork, spawn, forkserver, main
  handling, futures, and logging suites: 1,630 run/163 skipped, including
  four baseline macOS fork files; integrated full suite passed.
- [x] `concurrent.futures` — public `Future` scheduling and publication
  transitions reach Rust (`5126933`). Full `test_concurrent_futures` and
  `test_interpreters`: 570 run/30 skipped; integrated full suite passed.

## Broad supporting surface

- [x] `configparser` — default simple INI read and write reach Rust
  (`94e1f1c`). Full `test_configparser` and `test_logging`: 642 run/9
  skipped; integrated full suite passed.
- [x] `plistlib` — XML and binary property-list read and write reach Rust
  (`3c5d5c3`). Full `test_plistlib`: 71 run/0 skipped; integrated full
  suite passed.
- [x] `struct` — public pack and unpack of binary records reach Rust
  (`9844a6a`). Full `test_struct`, `test_array`, `test_buffer`, `test_call`,
  `test_float`, `test_pickle`, and `test_socket`: 3,235 run/311 skipped;
  integrated full suite passed.
- [x] `marshal` — public version-6 serialization and loading of supported
  code/data records, including list and dict cycles, reach Rust (`112a4d2`).
  Immutable cycles are rejected as upstream does. Cached
  bytecode imports stay extension-free (`1e399a2`); their valid marshal
  bytes match public Rust output but differ from pristine CPython's
  selective-reference output. Full `test_faulthandler`, `test_marshal`,
  `test_importlib`, `test_py_compile`, `test_compile`, and `test_audit`:
  1,602 run/29 skipped; integrated full suite passed.
- [x] `html.parser` — public HTML token scanning reaches Rust (`d64846f`).
  Full `test_htmlparser` and `test_html`: 70 run/2 skipped; integrated full
  suite passed.
- [x] `difflib` — public sequence matching and diff generation use Rust
  longest-match discovery for supported built-in sequences (`2ed801f`).
  Eight full focused suites: 4,119 run/8 skipped; `test_peg_generator`
  was resource-denied at baseline. Integrated full suite passed.
- [x] `codecs` — public standard UTF-8 one-shot and incremental conversion
  reach Rust (`54d5d7e`). Full `test_codecs`, `test_multibytecodec`,
  `test_charmapcodec`, `test_capi`, and `test_io`: 2,864 run/352 skipped,
  matching the clean-stage focused run; integrated full suite passed.
- [x] `unicodedata` — public category, combining, normalization, and
  normalized-state queries reach ICU4X-backed Rust (`ecf5a04`). Full
  `test_unicodedata`, `test__locale`, `test_re`, `test_str`,
  `test_unicode_file`, and `test_unicode_file_functions`: 420 run/99 skipped;
  integrated full suite passed.
- [x] `bz2` — public one-shot and incremental compression/decompression
  reach Rust (`540b0dd`). Full `test_bz2`, `test_tarfile`, `test_fileinput`,
  and `test_codecs`: 1,235 run/18 skipped; the GIL-enabled
  `test_free_threading` file was skipped as at baseline. Integrated full
  suite passed.
- [x] `lzma` — public one-shot and incremental XZ compression/decompression
  reach Rust (`c53e9cf`). Full `test_lzma`: 123 run/0 skipped;
  free-threading tests retain the baseline GIL skip. Integrated full suite
  passed.
- [x] `compression.zstd` — default dictionary-free public Zstandard streams
  and one-shot calls reach Rust (`503d673`). Full `test_zstd`, `test_zipfile`,
  `test_tarfile`, `test_shutil`, `test_zipimport`, and `test_profiling`:
  2,274 run/178 skipped; integrated full suite passed.
- [x] `zipimport` — public ZIP discovery, candidate matching, and stored or
  DEFLATE payload extraction reach Rust in the main interpreter (`1196638`);
  own-GIL children retain the Python route. Seven full suites including
  `test_zipimport` and `test_importlib`: 3,146 run/347 skipped; integrated
  full suite passed.
- [x] `glob` — public nonrecursive text pathname expansion reaches Rust
  (`cd43075`). Full `test_glob`: 22 run/2 macOS skips; integrated full suite
  passed.
- [x] `fnmatch` — public filename pattern matching reaches Rust (`924b4c5`).
  Full `test_fnmatch`, `test_glob`, and `test_shutil`: 275 run/78 skipped;
  integrated full suite passed.
- [x] `importlib.resources` — public resource traversal, lookup, and reading
  reach Rust (`1fbf607`). Full `test_importlib`, `test_zipimport`,
  `test_pathlib`, and `test_interpreters`: 2,917 run/438 skipped;
  integrated full suite passed.
- [x] `tempfile` — public temporary file and directory creation retry loops
  reach Rust (`2ff348e`). Full `test_tempfile`, `test_threadedtempfile`,
  `test_shutil`, `test_pathlib`, and `test_interpreters`: 1,915 run/496
  skipped; integrated full suite passed.
- [x] `fractions` — `Fraction` parsing and arithmetic reach Rust (`0dc86e5`).
  Full `test_fractions`, `test_statistics`, `test_numeric_tower`,
  `test_math`, `test_operator`, and `test_interpreters`: 727 run/11
  skipped; integrated full suite passed.
- [x] `statistics` — common integer-sequence mean, median, and variance
  operations reach Rust (`5d7899e`). Full `test_statistics`,
  `test_fractions`, `test_math`, and `test_random`: 654 run/10 skipped;
  integrated full suite passed.
- [x] `random` — public choice and sampling operations reach Rust
  (`f02e1f2`). Full `test_random`, `test_statistics`, and `test_uuid`:
  635 run/23 skipped; integrated full suite passed.
- [x] `collections` — public `Counter.subtract` iterable counting reaches
  Rust (`07c457a`). Full `test_collections`, `test_defaultdict`,
  `test_deque`, `test_ordered_dict`, `test_userdict`, `test_userlist`, and
  `test_userstring`: 666 run/3 skipped; integrated full suite passed.
- [x] `heapq` — public min-heap and max-heap operations reach Rust
  (`78afd13`). Full `test_heapq`, `test_queue`, and `test_sched`:
  243 run/6 memory-gated skips; integrated full suite passed.
- [x] `bisect` — ordered insertion and search reach Rust (`52a58f7`). Full
  `test_bisect`: 46 run/0 skipped; full `test_statistics`, `test_datetime`,
  and `test_free_threading`: 1,560 run/38 skipped; integrated full suite
  passed.
- [x] `itertools` — public `chain`, `compress`, `dropwhile`, `filterfalse`,
  `islice`, `starmap`, and `takewhile` steps reach Rust (`3fc5b6d`). Nine
  full relevant suites: 6,027 run/84 skipped, including two Windows-only
  files; integrated full suite passed.
- [x] `functools` — public `cmp_to_key` ordering comparisons reach Rust
  (`953946d`). Full `test_functools`, `test_sort`, `test_list`, and
  `test_userlist`: 479 run; integrated full suite passed.
- [x] `contextlib` — public `ExitStack` and `AsyncExitStack` composition
  reaches Rust (`9f55f22`). Full `test_contextlib` and `test_asyncio`:
  2,880 run/65 skipped; integrated full suite passed.
- [x] `dataclasses` — public class and field processing reaches Rust
  (`aab81e7`). Full `test_dataclasses`, `test_inspect`, and `test_typing`:
  1,405 run/0 skipped; integrated full suite passed.
- [x] `inspect` — public exact `Signature.bind`/`bind_partial` and member
  lookup reach Rust in the main interpreter (`d54262d`); own-GIL children
  retain a Python route. Full `test_inspect`, `test_enum`, `test_pydoc`, and
  `test_unittest`: 2,688 run/11 skipped; integrated full suite passed.
- [x] `ast` — public parse-tree walking and transformation helpers reach Rust
  (`daf807b`). Full `test_ast` and `test_compile`: 407 run/3 skipped;
  integrated full suite passed.
- [x] `argparse` — public argument parsing uses Rust option scans
  (`21d506a`). Full `test_argparse`, `test_optparse`, and `test_pydoc`:
  2,233 run; integrated full suite passed.
- [x] `tokenize` — public token generation for simple ASCII source reaches
  Rust (`e30aac18`). Full `test_tokenize` and `test_inspect`: 518 run;
  integrated full suite passed.
- [x] `_strptime` — directive parsing used by public date/time calls reaches
  Rust (`1b9336e`). Full `test_strptime`, `test_datetime`, `test_time`,
  `test_calendar`, and `test_locale`: 1,439 run/160 skipped; integrated full
  suite passed.
- [x] `shlex` — POSIX and non-POSIX token splitting reach Rust (`5b3ef58`).
  Full `test_shlex`, `test_mimetypes`, and `test_webbrowser`: 128 run/6
  skipped; integrated full suite passed.
- [x] `textwrap` — common ASCII wrap, fill, and shorten reach the Rust textwrap
  crate on macOS arm64 (`a23a25b`). Full `test_textwrap`, `test_argparse`,
  `test_optparse`, and `test_pydoc`: 2,301 run/0 skipped; full
  `test_textwrap`, `test_concurrent_futures`, and `test_interpreters` after the
  subinterpreter repair: 638 run/30 skipped. Integrated default-resource
  suite: 50,158 run/2,703 skipped, zero failures (496/505 files; nine
  resource-denied).
- [x] `threading` — public `Barrier` state transitions reach Rust
  (`5fe006a`). Full threading, interpreter, and futures suites: 906
  run/38 skipped; integrated full suite passed.
- [x] `typing` — public `get_origin` and `get_args` generic inspection reach
  Rust (`848ea2d`). Full `test_typing`, `test_annotationlib`,
  `test_dataclasses`, and `test_inspect`: 1,522 run/0 skipped; integrated full
  suite passed.
- [x] `warnings` — public filter insertion and warning display formatting
  reach Rust (`8e08c96`). Full `test_warnings`, `test_logging`,
  `test_unittest`, `test_context`, and `test_interpreters`: 1,803 run/21
  skipped; integrated full suite passed.
- [x] `urllib.request` — public `urlopen` request preprocessing, handler
  dispatch, and response processor dispatch reach Rust (`c8ef0e3`). Twelve
  full default-resource suites: 1,113 run/34 skipped; `test_urllib2net` and
  `test_urllibnet` were resource-denied. Integrated full suite passed.
