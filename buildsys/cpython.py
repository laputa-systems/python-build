"""CPython product configuration policy.

LTO is explicit ThinLTO; PGO is deferred for every target and runs CPython's
locked `-m test --pgo` workload only when `Target.pgo` is set; BOLT/JIT/tail-call
stay off; ensurepip stays disabled; and third-party dependencies resolve
through the private prefix.

The two families differ in more than flag spelling, and the differences are
consequences of the platform rather than preferences:

  linux-musl  Every dependency is bundled, dbm is Berkeley DB, readline is a
              private libedit, and musl's small default thread stack needs an
              explicit raise.
  macos       zlib, libedit, and ncurses/panel are the platform's own, while
              Expat is linked from source like the rest — a split measured
              from the pinned reference's load commands, not assumed. dbm is
              ndbm and `_uuid` uses platform facilities, so neither libuuid
              nor Berkeley DB is an input. No ELF loader-hardening or
              build-id flags exist, and the musl thread-stack policy does not
              apply.
"""

from __future__ import annotations

import os
from pathlib import Path

from .targets import Target, native_target

# Keep the profile workload as a named policy input. The tests are part of the
# locked CPython source, so the source pin selects their implementation.
CPYTHON_PGO_PROFILE_TASK = "-m test --pgo"
CPYTHON_PGO_HASH_SEED = "1704067200"


def resolve_pgo_jobs(jobs: int | None = None) -> int:
    """Choose PGO workers, defaulting to the CPython compile parallelism."""
    resolved = jobs if jobs is not None else resolve_build_jobs()
    if resolved < 1:
        raise ValueError("PGO profile jobs must be at least one")
    return resolved


def resolve_build_jobs() -> int:
    """Use one fewer worker than the host has CPUs."""
    return max(1, (os.cpu_count() or 4) - 1)


def pgo_profile_task(jobs: int) -> str:
    """Return CPython's profile task with an explicit worker count."""
    if jobs < 1:
        raise ValueError("PGO profile jobs must be at least one")
    return f"{CPYTHON_PGO_PROFILE_TASK} -j {jobs}"


def configuration(
    prefix: Path,
    target: Target | None = None,
    *,
    pgo_jobs: int | None = None,
) -> tuple[list[str], dict[str, str]]:
    prefix = Path(prefix)
    target = target or native_target()
    if pgo_jobs is not None and not target.pgo:
        raise ValueError(f"{target.triple}: PGO is not enabled for this target")
    if target.is_macos:
        return _macos_configuration(
            prefix, target, resolve_pgo_jobs(pgo_jobs) if target.pgo else None
        )
    return _linux_musl_configuration(prefix, target)


def _base_env(prefix: Path) -> dict[str, str]:
    """Dependency-selection variables shared by both families.

    These cover the libraries this project builds itself. macOS gets zlib
    and libedit from the SDK; Expat uses the private static build.
    """
    return {
        "BZIP2_CFLAGS": f"-I{prefix}/include",
        "BZIP2_LIBS": f"-L{prefix}/lib {prefix}/lib/libbz2.a",
        "LIBLZMA_CFLAGS": f"-I{prefix}/include",
        "LIBLZMA_LIBS": f"-L{prefix}/lib {prefix}/lib/liblzma.a",
        "LIBZSTD_CFLAGS": f"-I{prefix}/include",
        "LIBZSTD_LIBS": f"-L{prefix}/lib {prefix}/lib/libzstd.a",
        "LIBFFI_CFLAGS": f"-I{prefix}/include",
        "LIBFFI_LIBS": f"-L{prefix}/lib {prefix}/lib/libffi.a",
        "LIBMPDEC_CFLAGS": f"-I{prefix}/include",
        "LIBMPDEC_LIBS": f"-L{prefix}/lib {prefix}/lib/libmpdec.a -lm",
        "LIBSQLITE3_CFLAGS": f"-I{prefix}/include",
        "CPPFLAGS": f"-I{prefix}/include",
        "OPENSSL_INCLUDES": f"-I{prefix}/include",
    }


# Configure arguments that encode the product policy and are identical on
# every platform.
COMMON_POLICY_ARGS = (
    "--with-lto=thin",
    "--enable-shared",
    "--enable-loadable-sqlite-extensions",
    "--enable-experimental-jit=no",
    "--with-tail-call-interp=no",
    "--without-static-libpython",
    # The bundled mimalloc is compiled into libpython; the explicit flag
    # turns a missing C11 stdatomic.h into a configure failure instead of a
    # silent fallback to the system allocator.
    "--with-mimalloc",
    "--with-system-libmpdec",
    "--with-system-expat",
    "--with-ensurepip=no",
)


def _interpreter_flags(prefix: Path, target: Target) -> tuple[str, str]:
    cflags = ["-O3", target.cpu_baseline_cflag, "-fPIC"]
    ldflags = [f"-L{prefix}/lib"]
    if target.is_macos:
        cflags.append(f"-mmacosx-version-min={target.deployment_target}")
        ldflags.extend((
            f"-mmacosx-version-min={target.deployment_target}",
            "-Wl,-headerpad_max_install_names",
        ))
    else:
        cflags.extend(("-fstack-protector-strong", "-D_FORTIFY_SOURCE=2"))
        ldflags.extend((
            "-fuse-ld=lld",
            "-Wl,-z,noexecstack",
            "-Wl,-z,relro",
            "-Wl,-z,now",
            "-Wl,--build-id=sha1",
        ))
    return " ".join(cflags), " ".join(ldflags)


def _linux_musl_configuration(prefix: Path, target: Target) -> tuple[list[str], dict[str, str]]:
    cflags, ldflags = _interpreter_flags(prefix, target)
    env = {
        **_base_env(prefix),
        "PKG_CONFIG_LIBDIR": f"{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig",
        "LIBUUID_CFLAGS": f"-I{prefix}/include/uuid",
        "LIBUUID_LIBS": f"-L{prefix}/lib {prefix}/lib/libuuid.a",
        "LIBZSTD_LIBS": f"-L{prefix}/lib {prefix}/lib/libzstd.a -pthread",
        # Linux bundles ncurses/panel; macOS links the platform's 5.4 instead
        # (measured from the reference's load commands), so these must not be
        # set on that family — doing so silently links whatever static library
        # happens to be sitting in the prefix.
        "CURSES_CFLAGS": f"-I{prefix}/include -I{prefix}/include/ncursesw",
        "CURSES_LIBS": f"-L{prefix}/lib {prefix}/lib/libncursesw.a",
        "PANEL_CFLAGS": f"-I{prefix}/include/ncursesw",
        "PANEL_LIBS": f"-L{prefix}/lib {prefix}/lib/libpanelw.a {prefix}/lib/libncursesw.a",
        "LIBSQLITE3_LIBS": f"-L{prefix}/lib {prefix}/lib/libsqlite3.a -ldl -lpthread",
        "LIBEDIT_CFLAGS": f"-I{prefix}/include -I{prefix}/include/editline",
        "LIBEDIT_LIBS": f"-L{prefix}/lib {prefix}/lib/libedit.a {prefix}/lib/libncursesw.a",
        "DBM_CFLAGS": f"-I{prefix}/include",
        "DBM_LIBS": f"-L{prefix}/lib {prefix}/lib/libdb.a",
        "ZLIB_CFLAGS": f"-I{prefix}/include",
        "ZLIB_LIBS": f"{prefix}/lib/libz.a",
        # No -rpath here: a $ORIGIN token written through configure's
        # Makefile substitution is expanded twice (once by make, once by
        # the recipe's /bin/sh), which cannot produce a literal single-$
        # token in both the build's own link commands and the installed
        # sysconfigdata LDFLAGS that pip reads directly (no shell involved)
        # at the same time. Relative RPATHs are set as a structured
        # post-install ELF edit instead, so the installed search path is
        # relative to the interpreter rather than the build prefix.
        "LDFLAGS": ldflags,
        "CFLAGS": cflags,
        "LINKFORSHARED": "-pie -Xlinker -export-dynamic",
    }
    args = [
        *COMMON_POLICY_ARGS,
        "--with-openssl=" + str(prefix),
        "--with-openssl-rpath=no",
        "--with-dbmliborder=bdb:ndbm:gdbm",
        # Keep libedit as the readline backend: it must link against the
        # private libedit build rather than probe for a system GNU readline
        # that is not part of this product.
        "--with-readline=editline",
        # Musl default thread stack (128 KiB) is too small for CPython
        # recursion depth (Alpine carries the same 2 MiB policy).
        "CFLAGS_NODIST=-DTHREAD_STACK_SIZE=0x200000",
    ]
    return args, env


def _macos_configuration(
    prefix: Path, target: Target, pgo_jobs: int | None
) -> tuple[list[str], dict[str, str]]:
    cflags, ldflags = _interpreter_flags(prefix, target)
    env = {
        **_base_env(prefix),
        # Lock dependency detection to the private prefix. Without this,
        # pkg-config's default search path includes Homebrew's, which carries
        # .pc files for openssl/sqlite/libffi at different versions and would
        # let configure satisfy a probe from the builder's prefix. The Linux
        # branch keeps its original PKG_CONFIG_PATH because its container has
        # no competing prefix.
        "PKG_CONFIG_LIBDIR": f"{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig",
        "PKG_CONFIG_PATH": f"{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig",
        # Platform libraries, matching the reference's load commands: it links
        # /usr/lib/libedit.3.dylib, /usr/lib/libz.1.dylib,
        # /usr/lib/libncurses.5.4.dylib and /usr/lib/libpanel.5.4.dylib. No
        # private copy shadows any of them, so the probe finds the SDK's.
        "LIBEDIT_CFLAGS": "",
        "LIBEDIT_LIBS": "-ledit",
        # Expat is the exception: the reference carries no libexpat load
        # command, so it statically links its own. CPPFLAGS/LDFLAGS put the
        # private prefix first, which makes configure's -lexpat probe resolve
        # to the bundled libexpat.a rather than the SDK's dylib.
        "LIBSQLITE3_LIBS": f"-L{prefix}/lib {prefix}/lib/libsqlite3.a",
        "LDFLAGS": ldflags,
        "CFLAGS": cflags,
    }
    if pgo_jobs is not None:
        env.update({
            # `--enable-optimizations` runs this task against the instrumented
            # interpreter, then consumes its profile for the optimized build.
            # Keep the worker count explicit so the recipe can be reproduced.
            "PROFILE_TASK": pgo_profile_task(pgo_jobs),
            # SOURCE_DATE_EPOCH fixes libregrtest's seed; also fix the
            # interpreter hash seed so parallel profile shards use stable
            # hash ordering.
            "PYTHONHASHSEED": CPYTHON_PGO_HASH_SEED,
        })
    args = [
        *COMMON_POLICY_ARGS,
        *(["--enable-optimizations"] if pgo_jobs is not None else []),
        "--with-openssl=" + str(prefix),
        "--with-openssl-rpath=no",
        # Darwin's _uuid uses platform facilities and dbm uses ndbm; neither
        # libuuid nor Berkeley DB is built or shipped.
        "--with-dbmliborder=ndbm",
        "--with-readline=editline",
    ]
    return args, env
