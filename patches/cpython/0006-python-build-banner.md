# python-build banner: product identity line, no build dates

- **Upstream source/version**: `Modules/getbuildinfo.c` and
  `Python/getversion.c` in the verified CPython 3.15.1 source tree
  (`sources.lock.json` entry `cpython`, `Python-3.15.1.tar.xz`, SHA-256
  `673ae7232d84c894729882c8257d300ef2d26523911bfd3917ec8ce7f526f99b`).
- **Origin / license**: original to this project; not adapted from another
  project's patch. CPython itself is Python-2.0 licensed; this diff carries
  the same license as the files it modifies.
- **Explanation**: the stock banner prints a build clock date
  (`Jan  1 2024, 00:00:00`, pinned here via `SOURCE_DATE_EPOCH`) that
  carries no information in reproducible builds, and it says nothing
  about what this distribution is. This patch drops `DATE`/`TIME` from
  the build-info format and appends build identity on the same line, naming the
  product, target triple (from target macros, so the text needs no
  build-system substitution), key configuration (OpenSSL-free
  RTLS/rustls-graviola TLS plus hashing backend, static mimalloc,
  ThinLTO), the 12-character source revision (supplied by
  the harness as `-DPYB_SRC_REV`, recorded in `artifact.json`), and the
  canonical repository (`https://github.com/laputa-systems/python-build`). The
  banner keeps its leading version token, so `platform`'s `sys.version` parser, `sys.version.split()[0]`
  consumers, and `pydoc` output keep working; the date-less `buildno`
  slot still parses (`platform.python_build()` returns an empty
  builddate). `SOURCE_DATE_EPOCH` itself is untouched: it still pins the
  libregrtest seed and hash ordering.
- **Scope**: only `Py_GetBuildInfo()` and `Py_GetVersion()` output changes.
  Build identity stays on one line because upstream `test_repl` relies on
  fixed banner line counts when checking syntax-error output.
- **Applicability check**: apply with `patch -p1 --fuzz=0 --dry-run` to
  the verified CPython 3.15.1 source.
- **Regression test**: run upstream `test_repl`, `test_cmd_line`,
  `test_platform`, `test_pydoc`, and `test_tools` against the patched build.
