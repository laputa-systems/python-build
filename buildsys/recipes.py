"""Dependency recipes shared by all supported targets.

Each recipe builds into its own scratch directory and installs into a shared
private prefix. Static PIC libraries keep downstream linkage simple;
the dependency closure is small enough that a plain list beats a scheduler.

The build systems invoked here are the components' own — autotools, plain
make, and OpenSSL's Configure. The selected family determines the toolchain
and dependency set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .targets import native_target

REPO = Path(__file__).resolve().parent.parent


class BuildError(Exception):
    """A dependency or interpreter build step failed."""


@dataclass
class Toolchain:
    """The compiler/linker set a recipe is built with.

    Linux tools come from the pinned native-musl bootstrap. macOS uses
    absolute paths in the verified LLVM prefix and lets clang select Apple's
    linker.
    """

    cc: str = "clang"
    cxx: str = "clang++"
    ar: str = "llvm-ar"
    ranlib: str = "llvm-ranlib"
    ld: str = "ld.lld"
    nm: str = "llvm-nm"
    strip: str = "llvm-strip"
    make: str = "make"
    lto: str = "-flto=thin"
    jobs: int = max(1, (os.cpu_count() or 4))
    pkg_config_path: str = ""
    family: str = "linux-musl"
    sdkroot: str = ""  # macOS
    deployment_target: str = ""  # macOS
    cpu_baseline: str = ""  # e.g. -march=x86-64, -mcpu=apple-m1

    @property
    def is_macos(self) -> bool:
        return self.family == "macos"

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        # Scrub influences that could contaminate the build. The
        # DYLD_* family is macOS's equivalent of LD_PRELOAD/LD_LIBRARY_PATH:
        # an inherited value would substitute a different library at link or
        # run time without any change to the recipe.
        for var in ("PYTHONPATH", "PYTHONHOME", "CPATH", "C_INCLUDE_PATH",
                    "LIBRARY_PATH", "LD_LIBRARY_PATH", "LD_PRELOAD",
                    "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
                    "DYLD_INSERT_LIBRARIES", "DYLD_FRAMEWORK_PATH"):
            env.pop(var, None)
        env.update(
            {
                "CC": self.cc,
                "CXX": self.cxx,
                "CC_FOR_BUILD": self.cc,
                "CXX_FOR_BUILD": self.cxx,
                "AR": self.ar,
                "RANLIB": self.ranlib,
                "NM": self.nm,
                "STRIP": self.strip,
            }
        )
        if self.is_macos:
            # The linker is intentionally not named: clang's driver selects
            # the platform linker and passes the libLTO matching its own
            # bitcode. Pinning LD to a separately installed LLD is how you
            # get a version-mismatched bitcode rejection.
            env.pop("LD", None)
            if self.sdkroot:
                env["SDKROOT"] = self.sdkroot
            if self.deployment_target:
                # Autotools configure scripts read this directly; clang also
                # honours it. CFLAGS/LDFLAGS carry -mmacosx-version-min too,
                # because a component that forks its own compile line must
                # not fall back to the SDK's (newer) default floor.
                env["MACOSX_DEPLOYMENT_TARGET"] = self.deployment_target
        else:
            env["LD"] = self.ld
        if self.pkg_config_path:
            # Dependency detection must not find unrelated host packages
            # The private prefix is the only search root.
            env["PKG_CONFIG_PATH"] = self.pkg_config_path
            env["PKG_CONFIG_SYSROOT_DIR"] = ""
            if self.is_macos:
                # PKG_CONFIG_LIBDIR replaces the default search path, which
                # may otherwise include unrelated host packages.
                env["PKG_CONFIG_LIBDIR"] = self.pkg_config_path
        else:
            env.pop("PKG_CONFIG_PATH", None)
            if self.is_macos:
                env.pop("PKG_CONFIG_LIBDIR", None)
        if extra:
            env.update(extra)
        return env

    def cflags(self, extra: str = "") -> str:
        parts = ["-O3", self.cpu_baseline, "-fno-omit-frame-pointer", "-fPIC"]
        if self.lto:
            parts.append(self.lto)
        if self.is_macos:
            parts.append(f"-mmacosx-version-min={self.deployment_target}")
        else:
            parts.append("-fstack-protector-strong")
            parts.append("-D_FORTIFY_SOURCE=2")
        if extra:
            parts.append(extra)
        return " ".join(p for p in parts if p)

    def ldflags(self, prefix: Path, extra: str = "") -> str:
        parts = [f"-L{prefix}/lib"]
        if self.lto:
            parts.append(self.lto)
        if self.is_macos:
            parts.append(f"-mmacosx-version-min={self.deployment_target}")
        else:
            parts += [
                "-fuse-ld=lld",
                "-Wl,-z,noexecstack",
                "-Wl,-z,relro",
                "-Wl,-z,now",
                "-Wl,--build-id=sha1",
            ]
        if extra:
            parts.append(extra)
        return " ".join(p for p in parts if p)


def run(cmd: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as handle:
        result = subprocess.run(
            cmd, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode != 0:
        raise BuildError(f"command failed ({result.returncode}): {cmd} — log {log}")


@dataclass(frozen=True)
class RecipeLayout:
    archive_root: str
    source_root: str
    build_subdirectory: str = ""
    configure_subdirectory: str = ""
    style: str = "autotools"


RECIPE_LAYOUTS = {
    "zlib": RecipeLayout("zlib-1.3.1", "zlib-1.3.1"),
    "bzip2": RecipeLayout("bzip2-1.0.8", "bzip2-1.0.8", style="make"),
    "xz": RecipeLayout("xz-5.8.1", "xz-5.8.1"),
    "zstd": RecipeLayout(
        "cpython-source-deps-zstd-1.5.7",
        "cpython-source-deps-zstd-1.5.7/lib",
        style="make",
    ),
    "expat": RecipeLayout("expat-2.8.1", "expat-2.8.1"),
    "mpdecimal": RecipeLayout("mpdecimal-4.0.0", "mpdecimal-4.0.0"),
    "libffi": RecipeLayout("libffi-3.8.0", "libffi-3.8.0"),
    "libedit": RecipeLayout("libedit-20240808-3.1", "libedit-20240808-3.1"),
    "ncurses": RecipeLayout("ncurses-6.5", "ncurses-6.5"),
    "libuuid": RecipeLayout("libuuid-1.0.3", "libuuid-1.0.3"),
    "bdb": RecipeLayout(
        "db-6.0.19", "db-6.0.19", "db-6.0.19/build_unix", "dist"
    ),
    "sqlite": RecipeLayout("sqlite-autoconf-3530100", "sqlite-autoconf-3530100"),
    "openssl": RecipeLayout(
        "openssl-3.5.7", "openssl-3.5.7", style="openssl"
    ),
}


@dataclass
class Recipe:
    name: str
    layout: RecipeLayout
    configure_args: tuple[str, ...] = ()
    make_targets: tuple[str, ...] = ()
    cflags: str = ""
    cxxflags: str = ""
    ldflags: str = ""
    log_path: Path | None = None
    configure_env: tuple[tuple[str, str], ...] = ()


def build_recipe(
    recipe: Recipe,
    blob: Path,
    work: Path,
    prefix: Path,
    *,
    toolchain: Toolchain | None = None,
) -> None:
    """Extract a verified blob and build it into the private prefix.

    `toolchain` is injected by the build driver; when omitted, the native
    Alpine toolchain is selected.
    """
    # Resolve everything up front: a relative executable path would be
    # resolved against the child process's cwd, not the parent's.
    work = Path(work).resolve()
    prefix = Path(prefix).resolve()
    blob = Path(blob).resolve()
    if recipe.log_path is not None:
        recipe.log_path = Path(recipe.log_path).resolve()
    scratch = work / recipe.name
    if scratch.exists():
        raise BuildError(f"{scratch} already exists; refusing to reuse scratch tree")
    sys.path.insert(0, str(REPO))
    from buildsys.inputs import safe_extract

    safe_extract(blob, scratch)
    archive_root = scratch / recipe.layout.archive_root
    source = scratch / recipe.layout.source_root
    if not archive_root.is_dir() or not source.is_dir():
        raise BuildError(
            f"{recipe.name}: expected archive root {archive_root} and source {source}"
        )
    pkg_config_path = (
        f"{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig"
        if (prefix / "lib/pkgconfig").exists() or (prefix / "share/pkgconfig").exists()
        else ""
    )
    if toolchain is None:
        toolchain = Toolchain(pkg_config_path=pkg_config_path)
    elif not toolchain.pkg_config_path:
        toolchain = replace(toolchain, pkg_config_path=pkg_config_path)
    if recipe.layout.style == "make":
        # Plain make packages: no configure step at all. CFLAGS/LDFLAGS are
        # passed as make command-line overrides because some Makefiles
        # (bzip2) assign these variables internally and ignore the
        # environment. Make's command line wins over any in-makefile
        # assignment. `-D_FILE_OFFSET_BITS=64` is a large-file concern for
        # 32-bit-off_t platforms and is not needed on Darwin.
        env = toolchain.env()
        makefile = source / "Makefile"
        if not makefile.is_file():
            raise BuildError(f"{recipe.name}: missing Makefile at {makefile}")
        extra_cflags = recipe.cflags
        if not toolchain.is_macos:
            extra_cflags = " ".join(filter(None, (extra_cflags, "-D_FILE_OFFSET_BITS=64")))
        make_cflags = toolchain.cflags(extra_cflags)
        make_ldflags = toolchain.ldflags(prefix, recipe.ldflags)
        command = [
            toolchain.make,
            "-j",
            str(toolchain.jobs),
            f"CFLAGS={make_cflags}",
            f"LDFLAGS={make_ldflags}",
        ]
        # bzip2's Makefile assigns `CC=gcc` and zstd's defaults to `cc`. A
        # makefile assignment beats the environment, so exporting CC in `env`
        # is not enough — the dependency would be compiled by whatever the
        # makefile names rather than by the toolchain the target declares.
        # Command-line variable assignments are the only form that wins.
        # This applies on every family: a recipe that silently uses an
        # unlisted compiler produces an artifact nobody can attribute, and on
        # macOS it also breaks LTO, since Apple clang's bitcode generation
        # does not match the LLVM the lock pins.
        command += [
            f"CC={toolchain.cc}",
            f"CXX={toolchain.cxx}",
            f"AR={toolchain.ar}",
            f"RANLIB={toolchain.ranlib}",
        ]
        command += list(recipe.make_targets)
        run(command, cwd=makefile.parent, env=env, log=recipe.log_path / "make.log")
    elif recipe.layout.style == "autotools":
        cflags = toolchain.cflags(recipe.cflags)
        cxxflags = toolchain.cflags(recipe.cxxflags)
        ldflags = toolchain.ldflags(prefix, recipe.ldflags)
        env = toolchain.env(
            {
                "CFLAGS": cflags,
                "CXXFLAGS": cxxflags,
                "LDFLAGS": ldflags,
                **dict(recipe.configure_env),
            }
        )
        configure = source / recipe.layout.configure_subdirectory / "configure"
        if not configure.is_file():
            raise BuildError(f"{recipe.name}: missing configure script at {configure}")
        build_directory = (
            scratch / recipe.layout.build_subdirectory
            if recipe.layout.build_subdirectory
            else source
        )
        build_directory.mkdir(parents=True, exist_ok=True)
        run(
            [str(configure), f"--prefix={prefix}", *recipe.configure_args],
            cwd=build_directory,
            env=env,
            log=recipe.log_path / "configure.log",
        )
        run(
            [toolchain.make, "-j", str(toolchain.jobs), *recipe.make_targets],
            cwd=build_directory,
            env=env,
            log=recipe.log_path / "make.log",
        )
        run(
            [toolchain.make, "install"],
            cwd=build_directory,
            env=env,
            log=recipe.log_path / "install.log",
        )
    elif recipe.layout.style == "openssl":
        target = native_target()
        openssl_cflags = toolchain.cflags()
        openssl_ldflags = toolchain.ldflags(prefix)
        env = toolchain.env({"CFLAGS": openssl_cflags, "LDFLAGS": openssl_ldflags})
        run(["perl", str(source / "Configure"), target.openssl_configure_target,
             f"--prefix={prefix}", "--libdir=lib", "--openssldir=/etc/ssl",
             "no-shared", "no-module", *recipe.configure_args],
            cwd=source, env=env, log=recipe.log_path / "configure.log")
        run([toolchain.make, "-j", str(toolchain.jobs), "build_sw"], cwd=source,
            env=env, log=recipe.log_path / "make.log")
        run([toolchain.make, "install_sw"], cwd=source,
            env=env, log=recipe.log_path / "install.log")
    else:
        raise BuildError(f"unknown recipe style {recipe.layout.style!r}")
