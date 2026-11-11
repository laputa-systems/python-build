# Reproduce the hashing tests

Run the experiment on native Apple Silicon macOS with Rust 1.88 or newer,
`kache`, Cargo, and a CPython 3.15.1 candidate that has `_ssl` and the native
`_hashlib` extension disabled. Keep the candidate prefix under the repository's
`.work/` directory. A Python `_ssl.py` marker shim is allowed, but compiled
`_ssl` extensions are not. The script modifies only that disposable prefix.

Copy a prepared candidate prefix before installing the facade:

```sh
mkdir -p .work/tmp/hashlib-candidate
cp -cR /path/to/candidate-prefix/. .work/tmp/hashlib-candidate/
```

Build the extension, install both modules, and run the complete suites:

```sh
backend/hashing/run-tests.sh \
    .work/tmp/hashlib-candidate \
    .work/tmp/rtls-no-ssl/cpython-source/Python-3.15.1
```

The script downloads locked Cargo inputs, builds with `--offline --locked`,
and installs `_hashlib.py` in `lib/python3.15/`. It installs
`_hashlib_graviola.abi3.so` in `lib/python3.15/lib-dynload/`. It rejects native
`_hashlib` and `_ssl` extensions and checks that `_ssl` resolves only to a
Python source file containing the Rustls marker or is unavailable. If the
candidate lacks `lib/python3.15/test`, it copies CPython's test package into
that disposable prefix. The hashlib, hmac, and support tests run with `-I -B`
and do not add the source `Lib` directory to `PYTHONPATH`. The interpreter
driver runs with `-E -s -B` so its worker helper can be imported from the
driver directory while ignoring Python environment overrides.

Compare names, aliases, errors, HMAC, PBKDF2, buffer updates, the scrypt
maxmem boundary, unsigned parameter limits, and `__index__` conversion against
a pinned reference prefix:

```sh
python3 backend/hashing/compare-reference.py \
    .work/tmp/rtls-no-ssl/python-prefix/bin/python3.15 \
    .work/tmp/hashlib-candidate/bin/python3.15
```

The verified run used a private copy of the integrated v7d candidate on
CPython 3.15.1 arm64 macOS. Its Python `_ssl.py` marker shim remained installed,
and the original native `_hashlib` extension was absent. `test_hashlib` and
`test_hmac` completed with 240 tests run, 18 skipped, and no failures.
`TestHashlibSupport` completed with 5 tests run and 4 upstream skips.
The allocation-failure driver exercised native hash and HMAC constructor
cleanup at CPython allocation failpoints, then verified recovery outputs.
The public-attribute driver checks read-only hash/HMAC metadata, independent
copies, constructor signatures and forwarded keywords, and one-time
`usedforsecurity` coercion including unsupported-name precedence against CPython.

The subinterpreter driver verifies fixed reference outputs for hash, HMAC,
PBKDF2, and scrypt. It begins before either `_hashlib` module is imported in
the main interpreter, then holds two executor workers at a loopback rendezvous
while they import and use the backend concurrently. It checks copied-state
independence and repeats interpreter creation and destruction three times.
The suite skips include the private `_hashlib.HASH` instantiation check; the
facade has no shared native digest type. Resource-gated tests are skipped by
the default test runner settings. This verifies the Python-level hash
contracts on this macOS candidate; it does not qualify native `_hashlib`
consumers, TLS behavior, other platforms, performance, or a production
artifact.
