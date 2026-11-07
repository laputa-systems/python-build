# Product targets

`buildsys/product.py` derives product paths, ABI, archive names, and metadata
from the CPython source-lock entry. `buildsys/targets.py` owns production
target descriptions and target branching.

| Triple | Platform/compiler | Baseline and deployment |
| --- | --- | --- |
| `aarch64-apple-darwin` | Apple Silicon macOS 27 host; official LLVM 23.1.4 and Xcode 27 SDK | `-mcpu=apple-m1`; artifact minimum macOS 27.0 |
| `x86_64-unknown-linux-musl` | Alpine base; laputa `llvm-prebuilt-musl` LLVM 23.1.0-rc2 | `-march=broadwell` (AES, PCLMULQDQ, AVX/AVX2, BMI1/BMI2, FMA, MOVBE); loader `/lib/ld-musl-x86_64.so.1` |
| `aarch64-unknown-linux-musl` | Alpine base; laputa `llvm-prebuilt-musl` LLVM 23.1.0-rc2 | `-march=armv8-a+crypto` (AES, PMULL, SHA2, ASIMD); loader `/lib/ld-musl-aarch64.so.1` |

macOS 27 is the only supported macOS release: the host floor and the artifact
minimum are both 27.0, and macOS 26 and older are unsupported. Linux qualification
uses the locked Alpine image and complete APK closure in `bootstrap.lock.json`;
package acquisition is verified before the network-disabled build.

The product keeps the GIL, shared libpython, the bundled mimalloc allocator
(compiled in statically via explicit `--with-mimalloc`), private native
dependencies,
ThinLTO, `-O3`, and useful headers, sysconfig, pkg-config metadata,
and `python3.15-config`. PGO is deferred on every target and out of scope until
the owner reopens it; `Target.pgo` and the `--pgo-jobs` options keep the
machinery. JIT, BOLT, free-threaded ABI,
and the tail-call interpreter are disabled. Preserve the platform CPU
baselines and the declared dependency closure.

The product is fully OpenSSL-free: its candidate excludes OpenSSL source
and linkage; the pinned OpenSSL input serves the separate reference build.
Compiled `_ssl` and `_hashlib` are disabled in `Modules/Setup.local`,
and TLS plus hashing run on the RTLS/rustls-graviola backend built with the
pinned nightly Rust toolchain. Rust extensions use the musl dynamic CRT
(`-crt-static` is mandatory: rustc cannot emit cdylib/proc-macro artifacts
with a static CRT); no glibc symbols may appear in any payload. Do not claim a
build-time gain without a clean before-and-after measurement.

Linux artifacts need only musl at run time: the loader and
`libc.musl-<arch>.so.1`. They do not need Alpine's `libgcc` package, and no
GCC-compiled object is linked into them. The Linux build image installs no
GCC toolchain package: no `gcc`, `g++`, `libgcc-static`, `libstdc++-dev`,
`binutils`, or Alpine LLVM. Alpine's `libgcc` and `libstdc++` stay installed
only as run-time libraries of the build host's `python3`, `patchelf`, and the
Rust host tools.

- The compiler is the pinned laputa `llvm-prebuilt-musl` LLVM. A target clang
  configuration file in the image selects compiler-rt builtins, no unwinder
  library, the LLVM directory holding the static `libc++`, and the
  `fortify-headers` include path for every clang invocation. The `clang++`
  configuration adds the release's static `libc++abi` and `libunwind`, which
  the C++ driver does not request itself. LLVM's `strip` is exposed as `strip`
  for install rules that hardcode the command.
  The release leaves `PT_GNU_STACK` at zero, so musl gives every extra thread
  128 KiB and multithreaded `ld.lld` overflows on large ThinLTO links; the
  image sets that segment to 8 MiB in every shipped tool before any build.
- musl's `crt1.o` and `Scrt1.o` are rebuilt with that clang from the pinned
  musl source, because Alpine compiles them with GCC. The `crtbegin`/`crtend`
  objects are empty: musl needs neither.
- Rust extensions rebuild `std` with `-Zbuild-std` and the `llvm-libunwind`
  feature, linking the LLVM prebuilt's static `libunwind.a`. The prebuilt
  `std` would request `-lgcc_s` and carries GCC-compiled compiler-rt and
  libunwind objects.
- `ctypes` loads shared objects through the musl loader.
  `ctypes.util.find_library` returns `None` because it probes the host `gcc`,
  `ld`, and `ldconfig`, none of which exist on a minimal musl runtime; callers
  must name the soname.

`backend/` keeps the vendored RTLS source, the RTLS provider patch, the
hashing backend, and the replay procedures behind the official build. Keep its
Rust dependencies out of the production locks and bootstrap. Keep its vendored
source pin, per-architecture bootstrap lockfiles, and license record together
when refreshing the prototype.

Linux `_dbm` is the only consumer of Berkeley DB 6.0.19, whose Sleepycat
license differs from later AGPL releases; `_dbm` stays. `buildsys/notices.py`
installs the bundled notices plus `SOURCES.md` in each artifact, and the
qualifier's `license_notices` check verifies them in the extracted archive.

Ships: CPython 3.15.1, shared libpython, useful standard library, extension
build metadata, and private static native dependencies.

Deliberately absent: pip, ensurepip and its wheel, venv, Tcl/Tk/X11,
`_tkinter`, tkinter, idlelib, turtle, turtldemo, `__phello__` fixtures,
`_gdbm`, release-tree regression tests, native test C-API extensions,
sampling-profiler static web assets (vendored D3/flamegraph/heatmap
JavaScript, CSS, and images; the profiling collection code ships),
and generated bytecode caches. SQLite keeps FTS3/4/5, enhanced FTS3 query
syntax, geopoly, rtree, dbstat, and loadable extensions. Applications must
explicitly enable SQLite extension loading. `compression.zstd` uses a
multithread-capable static zstd library.

Default TLS trust tries the platform-configured sources first. The locked
Mozilla bundle is installed from `certifi-ca` at package time and is used only
when no explicit override or available default source exists. Set
`PYTHON_BUILD_NO_DEFAULT_CA_BUNDLE=1` to disable fallback. Trust data does not
provide online revocation checking; refresh its lock entry for each release
and relevant trust changes.
