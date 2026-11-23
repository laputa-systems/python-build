"""Linux host support for the isolated Rust-for-CPython lane.

The Linux lane runs in the production Alpine/musl container and shares the
production APK closure and LLVM archive:

* `linux-musl-toolchain.lock.json` pins the lane-only APK layer and references
  the production image, APK closure, and LLVM digests.
* `SealedRun` runs a command in a fresh user and network namespace
  (`unshare --user --map-root-user --net`). The namespace has only a down
  loopback device, so every connection fails, including to host loopback;
  `network_boundary_selftest` proves that against a live listener. Writes
  are not restricted, which is weaker than both the macOS profile and the
  production container boundary; reports say so.
* `fetch_source` reconstructs a codeload.github.com archive from the pinned
  commit with `git archive | gzip -n` when the archive host is unreachable.
  The result must still match the locked SHA-256 and size byte for byte.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cache_paths import local_cache_root

from buildsys.bootstrap import BootstrapError, load_linux_bootstrap
from buildsys.inputs import Cache, Input, InputError

TARGET = "x86_64-unknown-linux-musl"
_GNU_TARGET = "x86_64-unknown-linux-gnu"
UNSHARE = "/usr/bin/unshare"
_UNSHARE_ARGS = ("--user", "--map-root-user", "--net", "--")
_DOCKER_OFFLINE = "RUST_CPYTHON_DOCKER_OFFLINE"


class LinuxHostError(Exception):
    """The Linux lane toolchain or host cannot satisfy its lock."""


def supported_host() -> bool:
    return (
        platform.system() == "Linux"
        and platform.machine() == "x86_64"
        and Path("/etc/alpine-release").is_file()
    )


def _tool_output(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    return (result.stdout + result.stderr).strip()


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return values
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            values[name] = value.strip().strip('"')
    return values


def _dpkg_versions(packages: list[str]) -> dict[str, str]:
    names = [item.split("=", 1)[0] for item in packages]
    dpkg = shutil.which("dpkg-query")
    if dpkg is None:
        return {}
    result = subprocess.run(
        [dpkg, "-W", "-f=${Package}=${Version}\\n", *names],
        capture_output=True, text=True,
        check=False,
    )
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, version = line.partition("=")
        if separator:
            found[name] = version
    return found


@dataclass(frozen=True)
class LinuxToolchain:
    lock_path: Path
    llvm_prefix: Path
    llvm_resource_dir: Path
    llvm_version: str
    llvm_archive_url: str
    llvm_archive_sha256: str
    llvm_archive_size: int
    llvm_license: str
    llvm_release_tag: str
    llvm_source_commit: str
    llvm_attestation_url: str
    llvm_attestation_sha256: str
    llvm_attestation_size: int
    llvm_workflow: str
    llvm_workflow_path: str
    llvm_archive_root: str
    make: Path
    make_version: str
    pkgconf: Path
    pkgconf_version: str
    cpu_baseline: str
    host_os_id: str
    host_os_version: str
    glibc: str
    system_packages: tuple[str, ...]
    provisional_hosts: tuple[dict[str, Any], ...]
    icu_runtime: dict[str, Any]
    # Fields the macOS toolchain carries; empty on Linux by construction.
    deployment_target: str = ""
    xcode_version: str = ""
    sdkroot: Path | None = None

    @property
    def llvm_profdata(self) -> Path:
        return self.llvm_prefix / "bin" / "llvm-profdata"

    @property
    def clang(self) -> Path:
        return self.llvm_prefix / "bin" / "clang"

    @property
    def libclang_path(self) -> Path:
        """The distribution libclang directory for bindgen.

        Ubuntu 24.04 pins /usr/lib/llvm-18/lib; provisional hosts (e.g.
        22.04) carry a different llvm-N. Prefer the lock's pin, then the
        newest /usr/lib/llvm-N/lib that ships libclang.so.1.
        """
        pinned = Path("/usr/lib/llvm-18/lib")
        if (pinned / "libclang.so.1").is_file():
            return pinned
        versions = []
        for candidate in Path("/usr/lib").glob("llvm-*/lib"):
            try:
                versions.append((int(candidate.parent.name.removeprefix("llvm-")), candidate))
            except ValueError:
                continue
        for _version, candidate in sorted(versions, reverse=True):
            if (candidate / "libclang.so.1").is_file():
                return candidate
        return pinned

    @property
    def bindgen_resource_dir(self) -> Path:
        """clang resource dir matching the libclang bindgen will load.

        libclang-14 crashes parsing clang-23 built-in headers, so on hosts
        whose distro libclang ships its own resource dir
        (/usr/lib/llvm-N/lib/clang/N) that dir wins; otherwise the locked
        LLVM toolchain's resource dir is used, as upstream intends when the
        distro headers are broken (libclang-18 on Ubuntu 24.04).
        """
        clang_dirs = []
        for candidate in self.libclang_path.glob("clang/*"):
            try:
                clang_dirs.append((int(candidate.name.split(".")[0]), candidate))
            except ValueError:
                continue
        for _version, candidate in sorted(clang_dirs, reverse=True):
            if (candidate / "include").is_dir():
                return candidate
        return self.llvm_resource_dir

    def llvm_input(self) -> Input:
        return Input(
            name="llvm-linux-x86_64",
            version=self.llvm_version,
            urls=(self.llvm_archive_url,),
            sha256=self.llvm_archive_sha256,
            size=self.llvm_archive_size,
            role="build-source",
            target=_GNU_TARGET,
            license=self.llvm_license,
            purpose="official LLVM compiler, linker and profiling tools for the Linux lane",
        )

    def llvm_attestation_input(self) -> Input:
        return Input(
            name="llvm-linux-x86_64-provenance",
            version=self.llvm_version,
            urls=(self.llvm_attestation_url,),
            sha256=self.llvm_attestation_sha256,
            size=self.llvm_attestation_size,
            role="build-source",
            target=_GNU_TARGET,
            purpose="Sigstore provenance statement for the LLVM release archive",
        )

    def icu_input(self) -> Input:
        return Input(
            name="libicu70-lld-runtime",
            version=self.icu_runtime["package"].split("=", 1)[1],
            urls=(self.icu_runtime["url"],),
            sha256=self.icu_runtime["sha256"],
            size=self.icu_runtime["size"],
            role="build-source",
            target=_GNU_TARGET,
            license=self.icu_runtime["license"],
            purpose="ICU 70 runtime libraries required by the official ld.lld binary",
        )

    @property
    def icu_marker(self) -> Path:
        return self.llvm_prefix / "lib" / ".icu70.verified.json"

    def env(self) -> dict[str, str]:
        """Compiler selection plus a scrub of search-path influences."""
        env = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "CPATH", "C_INCLUDE_PATH",
                     "LIBRARY_PATH", "LD_LIBRARY_PATH", "LD_PRELOAD",
                     "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"):
            env.pop(name, None)
        binary = self.llvm_prefix / "bin"
        env.update({
            "CC": str(binary / "clang"),
            "CXX": str(binary / "clang++"),
            "AR": str(binary / "llvm-ar"),
            "RANLIB": str(binary / "llvm-ranlib"),
            "NM": str(binary / "llvm-nm"),
            "STRIP": str(binary / "llvm-strip"),
            "READELF": str(binary / "llvm-readelf"),
            "LIBCLANG_PATH": str(self.libclang_path),
        })
        # As on macOS, LD is not exported; `-fuse-ld=lld` in LDFLAGS selects
        # the locked linker through the clang driver.
        env.pop("LD", None)
        return env

    def toolchain(self) -> LinuxToolchain:
        """Match `MacOSToolchain.toolchain().env()` call sites."""
        return self

    def identity(self) -> dict[str, Any]:
        profdata = _tool_output([str(self.llvm_profdata), "--version"])
        return {
            "family": "linux-gnu",
            "target": _GNU_TARGET,
            "llvm_version": self.llvm_version,
            "llvm_prefix": str(self.llvm_prefix),
            "llvm_resource_dir": str(self.llvm_resource_dir),
            "llvm_archive": {
                "url": self.llvm_archive_url,
                "sha256": self.llvm_archive_sha256,
                "size": self.llvm_archive_size,
                "license": self.llvm_license,
                "release_tag": self.llvm_release_tag,
                "source_commit": self.llvm_source_commit,
                "attestation_url": self.llvm_attestation_url,
                "attestation_sha256": self.llvm_attestation_sha256,
                "attestation_size": self.llvm_attestation_size,
                "workflow": self.llvm_workflow,
                "workflow_path": self.llvm_workflow_path,
            },
            "linker": str(self.llvm_prefix / "bin" / "ld.lld"),
            "llvm_profdata": str(self.llvm_profdata),
            "llvm_profdata_version": profdata.splitlines()[0] if profdata else "unavailable",
            "cpu_baseline": self.cpu_baseline,
            "make": str(self.make),
            "make_version": self.make_version,
            "pkgconf": str(self.pkgconf),
            "pkgconf_version": self.pkgconf_version,
            "host": {"os_id": self.host_os_id, "os_version_id": self.host_os_version,
                     "glibc": self.glibc},
            "system_packages": list(self.system_packages),
            "lld_icu_runtime": {key: self.icu_runtime[key]
                                for key in ("package", "url", "sha256", "size")},
            "lock_path": str(self.lock_path),
        }


@dataclass(frozen=True)
class MuslToolchain:
    lock_path: Path
    target: str
    cpu_baseline: str
    alpine_release: str
    image_digest: str
    image_revision: str
    repositories: tuple[str, ...]
    package_pins: tuple[str, ...]
    lane_packages: tuple[dict[str, Any], ...]
    lane_apk_cache: str
    system_package_roots: tuple[str, ...]
    llvm_prefix: Path
    llvm_resource_dir: Path
    llvm_version: str
    llvm_archive_url: str
    llvm_archive_sha256: str
    llvm_archive_size: int
    llvm_archive_root: str
    llvm_license: str
    rust_channel: str
    rust_host: str
    make: Path
    make_version: str
    pkgconf: Path
    pkgconf_version: str

    @property
    def llvm_profdata(self) -> Path:
        return self.llvm_prefix / "bin" / "llvm-profdata"

    @property
    def clang(self) -> Path:
        return self.llvm_prefix / "bin" / "clang"

    @property
    def libclang_path(self) -> Path:
        return self.llvm_prefix / "lib"

    @property
    def bindgen_resource_dir(self) -> Path:
        return self.llvm_resource_dir

    def llvm_input(self) -> Input:
        return Input(
            name="llvm-musl-x86_64",
            version=self.llvm_version,
            urls=(self.llvm_archive_url,),
            sha256=self.llvm_archive_sha256,
            size=self.llvm_archive_size,
            role="build-source",
            target=self.target,
            license=self.llvm_license,
            purpose="production LLVM compiler and linker for the musl lane",
        )

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        for name in (
            "PYTHONPATH", "PYTHONHOME", "CPATH", "C_INCLUDE_PATH", "LIBRARY_PATH",
            "LD_LIBRARY_PATH", "LD_PRELOAD", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS",
        ):
            env.pop(name, None)
        binary = self.llvm_prefix / "bin"
        env.update({
            "CC": str(binary / "clang"),
            "CXX": str(binary / "clang++"),
            "AR": str(binary / "llvm-ar"),
            "RANLIB": str(binary / "llvm-ranlib"),
            "NM": str(binary / "llvm-nm"),
            "STRIP": str(binary / "llvm-strip"),
            "READELF": str(binary / "llvm-readelf"),
            "LIBCLANG_PATH": str(self.libclang_path),
        })
        env.pop("LD", None)
        return env

    def toolchain(self) -> MuslToolchain:
        return self

    def identity(self) -> dict[str, Any]:
        return {
            "family": "linux-musl",
            "target": self.target,
            "alpine_release": self.alpine_release,
            "image_digest": self.image_digest,
            "image_revision": self.image_revision,
            "cpu_baseline": self.cpu_baseline,
            "lock_path": str(self.lock_path),
            "llvm_version": self.llvm_version,
            "llvm_prefix": str(self.llvm_prefix),
            "llvm_archive": {
                "url": self.llvm_archive_url,
                "sha256": self.llvm_archive_sha256,
                "size": self.llvm_archive_size,
                "license": self.llvm_license,
            },
            "rust_native_static_libs": ["-lgcc_s", "-lc"],
            "linker": str(self.llvm_prefix / "bin" / "ld.lld"),
            "llvm_profdata": None,
            "llvm_profdata_version": "not included in the production musl LLVM archive",
            "make": str(self.make),
            "make_version": self.make_version,
            "pkgconf": str(self.pkgconf),
            "pkgconf_version": self.pkgconf_version,
            "system_package_roots": list(self.system_package_roots),
            "system_packages": list(self.package_pins),
        }


def load_musl_toolchain(path: Path, cache_root: Path | None = None) -> MuslToolchain:
    try:
        document = json.loads(Path(path).read_text())
        bootstrap_path = Path(path).resolve().parents[1] / "bootstrap.lock.json"
        bootstrap_document = json.loads(bootstrap_path.read_text())["linux_toolchain"]
        bootstrap = load_linux_bootstrap(bootstrap_path)
    except (OSError, json.JSONDecodeError, KeyError, BootstrapError) as error:
        raise LinuxHostError(f"cannot read musl toolchain inputs: {error}") from error
    production = document.get("production", {})
    closure = bootstrap_document["apk_closure"]["x86_64"]
    closure_digest = hashlib.sha256(
        json.dumps(closure, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    image = bootstrap.images["amd64"]
    archive = bootstrap.llvm.archives["x86_64"]
    if document.get("target") != TARGET or document.get("arch") != "x86_64":
        raise LinuxHostError(f"{path}: expected the {TARGET} target")
    if (
        production.get("image_digest") != image.digest
        or production.get("image_revision") != image.revision
        or production.get("apk_closure_sha256") != closure_digest
        or production.get("llvm_sha256") != archive.sha256
        or document.get("alpine_release") != bootstrap.release
        or tuple(document.get("repositories", ())) != bootstrap.repositories
    ):
        raise LinuxHostError(f"{path}: production image, APK, or LLVM identity differs")
    expected = dict(item.split("=", 1) for item in bootstrap.base_packages)
    expected.update({item.name: item.version for item in bootstrap.apk_closure["x86_64"]})
    lane_packages = tuple(document.get("lane_packages", ()))
    if not lane_packages or len({item.get("name") for item in lane_packages}) != len(lane_packages):
        raise LinuxHostError(f"{path}: lane package set is empty or has duplicate names")
    for item in lane_packages:
        if (
            item.get("arch") != "x86_64"
            or item.get("repository") not in {"main", "community"}
            or item.get("repository") not in {
                repository.rsplit("/", 1)[-1] for repository in bootstrap.repositories
            }
            or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
            or not isinstance(item.get("size"), int)
            or not item.get("license")
        ):
            raise LinuxHostError(f"{path}: malformed lane APK entry {item.get('name')}")
        supersedes = item.get("supersedes_production_pin")
        if supersedes:
            name, separator, version = supersedes.partition("=")
            if not separator or name != item.get("name") or expected.get(name) != version:
                raise LinuxHostError(f"{path}: invalid production supersession for {name}")
        expected[item["name"]] = item["version"]
    runtime_libgcc = next((item for item in lane_packages if item.get("name") == "libgcc"), None)
    if (
        runtime_libgcc is None
        or runtime_libgcc.get("version") != "15.2.0-r5"
        or runtime_libgcc.get("license") != "GPL-3.0-or-later WITH GCC-exception-3.1"
        or runtime_libgcc.get("role") != "lane runtime library libgcc_s.so.1, not a compiler"
    ):
        raise LinuxHostError(f"{path}: Alpine libgcc_s runtime package pin is missing")
    roots = tuple(document.get("system_package_roots", ()))
    if not roots or not set(roots).issubset({item["name"] for item in lane_packages}):
        raise LinuxHostError(f"{path}: system package roots are not in the lane APK set")
    tools = document.get("production_tools", {})
    if tools.get("make") != f"make={expected.get('make')}":
        raise LinuxHostError(f"{path}: make pin differs from the production closure")
    if tools.get("pkgconf") != f"pkgconf={expected.get('pkgconf')}":
        raise LinuxHostError(f"{path}: pkgconf pin differs from the production closure")
    llvm = document.get("llvm", {})
    native_static_libs = llvm.get("rust_native_static_libs", {})
    if (
        llvm.get("sha256") != archive.sha256
        or llvm.get("version") != bootstrap.llvm.version
        or native_static_libs.get("reported") != ["-lgcc_s", "-lc"]
        or native_static_libs.get("musl_link_args") != ["-lgcc_s", "-lc"]
        or native_static_libs.get("runtime_package") != "libgcc=15.2.0-r5, providing libgcc_s.so.1"
    ):
        raise LinuxHostError(f"{path}: LLVM or Rust native-library identity differs")
    rust = document.get("rust", {})
    if rust.get("host") != TARGET or rust.get("channel") != "nightly-2026-09-15":
        raise LinuxHostError(f"{path}: Rust toolchain pin is not the musl lane toolchain")
    version = llvm["version"]
    local_cache = local_cache_root(Path(path).resolve().parents[1], cache_root)
    prefix = (
        local_cache / "llvm" / "toolchains"
        / f"{version}-{archive.sha256}-musl"
    ).resolve()
    package_pins = tuple(f"{name}={value}" for name, value in sorted(expected.items()))
    return MuslToolchain(
        lock_path=Path(path),
        target=TARGET,
        cpu_baseline=document["cpu_baseline"],
        alpine_release=bootstrap.release,
        image_digest=image.digest,
        image_revision=image.revision,
        repositories=bootstrap.repositories,
        package_pins=package_pins,
        lane_packages=lane_packages,
        lane_apk_cache=document["lane_apk_cache"],
        system_package_roots=roots,
        llvm_prefix=prefix,
        llvm_resource_dir=prefix / "lib" / "clang" / "23",
        llvm_version=version,
        llvm_archive_url=archive.url,
        llvm_archive_sha256=archive.sha256,
        llvm_archive_size=archive.size,
        llvm_archive_root=archive.archive_root,
        llvm_license=llvm["license"],
        rust_channel=rust["channel"],
        rust_host=rust["host"],
        make=Path("/usr/bin/make"),
        make_version=expected["make"],
        pkgconf=Path("/usr/bin/pkgconf"),
        pkgconf_version=expected["pkgconf"],
    )


def _require(document: dict, key: str, where: str) -> Any:
    if key not in document:
        raise LinuxHostError(f"{where}: missing {key!r}")
    return document[key]


def load_linux_toolchain(path: Path, cache_root: Path) -> LinuxToolchain:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise LinuxHostError(f"cannot read Linux toolchain lock {path}: {error}") from error
    if document.get("target") != _GNU_TARGET:
        raise LinuxHostError(f"{path}: target is not {_GNU_TARGET}")
    llvm = _require(document, "llvm", "lock")
    archive = _require(llvm, "archive", "llvm")
    provenance = _require(archive, "provenance", "llvm.archive")
    if provenance.get("attestation_subject_sha256") != archive.get("sha256"):
        raise LinuxHostError("LLVM attestation subject disagrees with the archive pin")
    make = _require(document, "make", "lock")
    pkgconf = _require(document, "pkgconf", "lock")
    host = _require(document, "host", "lock")
    prefix = (
        Path(cache_root) / "llvm" / "toolchains"
        / f"{llvm['version']}-{archive['sha256']}"
    )
    return LinuxToolchain(
        lock_path=Path(path),
        llvm_prefix=prefix,
        llvm_resource_dir=prefix / _require(llvm, "resource_dir", "llvm"),
        llvm_version=_require(llvm, "version", "llvm"),
        llvm_archive_url=_require(archive, "url", "llvm.archive"),
        llvm_archive_sha256=_require(archive, "sha256", "llvm.archive"),
        llvm_archive_size=_require(archive, "size", "llvm.archive"),
        llvm_license=_require(archive, "license", "llvm.archive"),
        llvm_release_tag=_require(provenance, "release_tag", "provenance"),
        llvm_source_commit=_require(provenance, "source_commit", "provenance"),
        llvm_attestation_url=_require(provenance, "attestation_url", "provenance"),
        llvm_attestation_sha256=_require(provenance, "attestation_sha256", "provenance"),
        llvm_attestation_size=_require(provenance, "attestation_size", "provenance"),
        llvm_workflow=_require(provenance, "workflow", "provenance"),
        llvm_workflow_path=_require(provenance, "workflow_path", "provenance"),
        llvm_archive_root=_require(archive, "archive_root", "llvm.archive"),
        make=Path(_require(make, "path", "make")),
        make_version=_require(make, "version", "make"),
        pkgconf=Path(_require(pkgconf, "path", "pkgconf")),
        pkgconf_version=_require(pkgconf, "version", "pkgconf"),
        cpu_baseline=_require(document, "cpu_baseline", "lock"),
        host_os_id=_require(host, "os_id", "host"),
        host_os_version=_require(host, "os_version_id", "host"),
        glibc=_require(host, "glibc", "host"),
        system_packages=tuple(_require(document, "system_packages", "lock")),
        provisional_hosts=tuple(document.get("provisional_hosts", ())),
        icu_runtime=_require(document, "lld_icu_runtime", "lock"),
    )


def host_spec(toolchain: LinuxToolchain | MuslToolchain) -> tuple[str, dict[str, Any]]:
    """The host spec this host satisfies: ('lock', spec) or ('provisional', spec).

    Provisional specs come from the lock's `provisional_hosts` entries and
    carry their own make/pkgconf versions and package pins. When nothing
    matches, the primary spec is returned so problems() reports against it.
    """
    if isinstance(toolchain, MuslToolchain):
        return "pinned-alpine", {
            "release": toolchain.alpine_release,
            "image_digest": toolchain.image_digest,
            "system_packages": list(toolchain.package_pins),
        }
    release = _os_release()
    libc = platform.libc_ver()
    primary = {
        "os_version_id": toolchain.host_os_version,
        "glibc": toolchain.glibc,
        "make_version": toolchain.make_version,
        "pkgconf_version": toolchain.pkgconf_version,
        "system_packages": list(toolchain.system_packages),
    }
    if (
        release.get("ID") == toolchain.host_os_id
        and release.get("VERSION_ID") == primary["os_version_id"]
        and libc == ("glibc", primary["glibc"])
    ):
        return "lock", primary
    for entry in toolchain.provisional_hosts:
        if (
            release.get("ID") == toolchain.host_os_id
            and release.get("VERSION_ID") == entry.get("os_version_id")
            and libc == ("glibc", entry.get("glibc"))
        ):
            return "provisional", {
                "os_version_id": entry["os_version_id"],
                "glibc": entry["glibc"],
                "reason": entry.get("reason", ""),
                "make_version": entry.get("make_version", toolchain.make_version),
                "pkgconf_version": entry.get("pkgconf_version", toolchain.pkgconf_version),
                "system_packages": entry.get(
                    "system_packages", list(toolchain.system_packages)
                ),
            }
    return "lock", primary


def _apk_installed_versions() -> dict[str, str]:
    try:
        records = Path("/lib/apk/db/installed").read_text().split("\n\n")
    except OSError:
        return {}
    installed: dict[str, str] = {}
    for record in records:
        fields = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
        if fields.get("P"):
            installed[fields["P"]] = fields.get("V", "")
    return installed


def _musl_problems(toolchain: MuslToolchain) -> list[str]:
    found: list[str] = []
    if not supported_host():
        found.append(
            f"the musl lane requires x86_64 Alpine in Docker "
            f"(found {platform.system()} {platform.machine()})"
        )
    if not Path("/.dockerenv").is_file():
        found.append("the musl lane must run inside the pinned Docker container")
    release = Path("/etc/alpine-release")
    try:
        installed_release = release.read_text().strip()
    except OSError:
        installed_release = "unavailable"
    if installed_release != toolchain.alpine_release:
        found.append(
            f"Alpine release is {installed_release}; lock requires {toolchain.alpine_release}"
        )
    image_digest = os.environ.get("MUSL_LANE_IMAGE_DIGEST")
    if image_digest != toolchain.image_digest:
        found.append(
            f"container image digest is {image_digest or 'unreported'}; "
            f"lock requires {toolchain.image_digest}"
        )
    installed = _apk_installed_versions()
    expected = dict(item.split("=", 1) for item in toolchain.package_pins)
    for name, version in expected.items():
        if installed.get(name) != version:
            found.append(
                f"APK package {name} is {installed.get(name) or 'absent'}; lock requires {version}"
            )
    extras = sorted(set(installed) - set(expected))
    if extras:
        found.append(f"unexpected APK packages are installed: {', '.join(extras)}")
    marker = toolchain.llvm_prefix / ".verified.json"
    try:
        marker_data = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        marker_data = None
    if marker_data != {
        "version": toolchain.llvm_version,
        "sha256": toolchain.llvm_archive_sha256,
    }:
        found.append(f"production LLVM archive is not provisioned at {toolchain.llvm_prefix}; run fetch")
    else:
        clang_version = _tool_output([str(toolchain.clang), "--version"])
        if f"clang version {toolchain.llvm_version}" not in clang_version:
            found.append(f"{toolchain.clang} does not report LLVM {toolchain.llvm_version}")
        triple = _tool_output([str(toolchain.clang), "-print-target-triple"])
        if triple != toolchain.target:
            found.append(f"clang target is {triple!r}; expected {toolchain.target}")
        resource = _tool_output([str(toolchain.clang), "-print-resource-dir"])
        if Path(resource) != toolchain.llvm_resource_dir:
            found.append(f"clang resource directory is {resource!r}")
        for name in ("ld.lld", "llvm-ar", "llvm-nm", "llvm-readelf"):
            reported = _tool_output([str(toolchain.llvm_prefix / "bin" / name), "--version"])
            if toolchain.llvm_version not in reported:
                found.append(f"{name} does not report LLVM {toolchain.llvm_version}")
    libclang = toolchain.libclang_path / "libclang.so"
    if not libclang.is_file():
        found.append(f"{libclang} is missing")
    make_version = toolchain.make_version.split("-r", 1)[0]
    if f"GNU Make {make_version}" not in _tool_output([str(toolchain.make), "--version"]):
        found.append(f"{toolchain.make} does not report GNU Make {toolchain.make_version}")
    pkgconf_version = toolchain.pkgconf_version.split("-r", 1)[0]
    if _tool_output([str(toolchain.pkgconf), "--version"]) != pkgconf_version:
        found.append(f"{toolchain.pkgconf} does not report {toolchain.pkgconf_version}")
    return found


def problems(toolchain: LinuxToolchain | MuslToolchain) -> list[str]:
    """Every way the host disagrees with the Linux lane lock."""
    if isinstance(toolchain, MuslToolchain):
        return _musl_problems(toolchain)
    found: list[str] = []
    if not supported_host():
        found.append(
            f"the Linux lane requires native x86_64 Linux (found {platform.system()} "
            f"{platform.machine()})"
        )
    spec_kind, spec = host_spec(toolchain)
    release = _os_release()
    if release.get("ID") != toolchain.host_os_id or (
        release.get("VERSION_ID") != spec["os_version_id"]
    ):
        found.append(
            f"host distribution is {release.get('ID')} {release.get('VERSION_ID')}; "
            f"lock requires {toolchain.host_os_id} {spec['os_version_id']}"
        )
    libc = platform.libc_ver()
    if libc != ("glibc", spec["glibc"]):
        found.append(f"host C library is {libc}; lock requires glibc {spec['glibc']}")
    marker = toolchain.llvm_prefix / ".verified.json"
    try:
        marker_data = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        marker_data = None
    if marker_data != {"version": toolchain.llvm_version,
                       "sha256": toolchain.llvm_archive_sha256}:
        found.append(
            f"official LLVM archive is not provisioned at {toolchain.llvm_prefix}; "
            "run `python3 rust-cpython/build.py fetch`"
        )
    else:
        reported = _tool_output([str(toolchain.clang), "--version"])
        if f"clang version {toolchain.llvm_version}" not in reported:
            found.append(f"{toolchain.clang} does not report LLVM {toolchain.llvm_version}")
        resource = _tool_output([str(toolchain.clang), "-print-resource-dir"])
        if Path(resource) != toolchain.llvm_resource_dir:
            found.append(f"clang resource directory is {resource!r}")
        for tool in ("ld.lld", "llvm-profdata", "llvm-ar", "llvm-nm", "llvm-readelf"):
            reported = _tool_output([str(toolchain.llvm_prefix / "bin" / tool), "--version"])
            if toolchain.llvm_version not in reported:
                found.append(f"{tool} does not report LLVM {toolchain.llvm_version}")
    try:
        icu_marker = json.loads(toolchain.icu_marker.read_text())
    except (OSError, json.JSONDecodeError):
        icu_marker = None
    if icu_marker != {"sha256": toolchain.icu_runtime["sha256"]}:
        found.append("ICU 70 runtime for ld.lld is not provisioned; run fetch")
    if not (toolchain.libclang_path / "libclang.so.1").is_file():
        found.append(f"{toolchain.libclang_path / 'libclang.so.1'} is missing")
    if f"GNU Make {spec['make_version']}" not in _tool_output([str(toolchain.make), "--version"]):
        found.append(f"{toolchain.make} does not report GNU Make {spec['make_version']}")
    if _tool_output([str(toolchain.pkgconf), "--version"]) != spec["pkgconf_version"]:
        found.append(f"{toolchain.pkgconf} does not report {spec['pkgconf_version']}")
    installed = _dpkg_versions(list(spec["system_packages"]))
    for pinned in spec["system_packages"]:
        name, _separator, version = pinned.partition("=")
        if installed.get(name) != version:
            found.append(
                f"host package {name} is {installed.get(name) or 'absent'}; lock requires {version}"
            )
    if not Path(UNSHARE).is_file():
        found.append(f"{UNSHARE} is unavailable; the offline build cannot be sealed")
    return found


def provision_icu_runtime(toolchain: LinuxToolchain, deb: Path) -> Path:
    """Copy the locked ICU 70 libraries beside the verified LLVM tools.

    `deb` must already be the digest-verified cache object. Only the members
    named in the lock are taken, plus the SONAME links the loader resolves.
    """
    library = toolchain.llvm_prefix / "lib"
    marker = toolchain.icu_marker
    expected = {"sha256": toolchain.icu_runtime["sha256"]}
    try:
        if json.loads(marker.read_text()) == expected:
            return library
    except (OSError, json.JSONDecodeError):
        pass
    members = list(toolchain.icu_runtime["members"])
    with tempfile.TemporaryDirectory(prefix=".icu-extract-", dir=library) as temporary:
        staged = Path(temporary)
        fsys = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(deb)], stdout=subprocess.PIPE)
        extract = subprocess.run(
            ["tar", "-x", "-C", str(staged), "--no-same-owner",
             *[f"./{member}" for member in members]],
            stdin=fsys.stdout, capture_output=True, text=True,
            check=False,
        )
        assert fsys.stdout is not None
        fsys.stdout.close()
        if fsys.wait() != 0 or extract.returncode != 0:
            raise LinuxHostError(f"cannot extract ICU runtime: {extract.stderr.strip()}")
        for member in members:
            source = staged / member
            if source.is_symlink() or not source.is_file():
                raise LinuxHostError(f"ICU package member is not a regular file: {member}")
            name = Path(member).name
            if member.startswith("usr/share/doc/"):
                destination = library / "icu70-copyright"
            else:
                destination = library / name
            shutil.copyfile(source, destination)
            if name.endswith(".so.70.1"):
                soname = library / name[: -len(".1")]
                if soname.is_symlink() or soname.exists():
                    soname.unlink()
                soname.symlink_to(name)
    marker.write_text(json.dumps(expected, sort_keys=True) + "\n")
    return library


def setup_local_with_shared_ssl_rs(contents: str) -> str:
    lines = contents.splitlines()
    try:
        shared = lines.index("*shared*")
        static = lines.index("*static*")
        disabled = lines.index("*disabled*")
    except ValueError as error:
        raise LinuxHostError("Setup.local lacks a required module section") from error
    if not shared < static < disabled:
        raise LinuxHostError("Setup.local module sections are out of order")

    def ssl_entries(start: int, stop: int) -> list[int]:
        return [index for index in range(start, stop)
                if lines[index].split()[:1] == ["_ssl_rs"]]

    shared_entries = ssl_entries(shared + 1, static)
    static_entries = ssl_entries(static + 1, disabled)
    if len(shared_entries) + len(static_entries) != 1:
        raise LinuxHostError("Setup.local must declare exactly one _ssl_rs module")
    if shared_entries:
        return "\n".join(lines) + "\n"
    lines.pop(static_entries[0])
    ssl_entry = "_ssl_rs _ssl_rs/Cargo.toml _ssl_rs/src/lib.rs"
    insertion = static
    while insertion > shared + 1 and not lines[insertion - 1].strip():
        insertion -= 1
    lines.insert(insertion, ssl_entry)
    return "\n".join(lines) + "\n"


def package_report(toolchain: LinuxToolchain | MuslToolchain) -> dict[str, str]:
    if isinstance(toolchain, MuslToolchain):
        installed = _apk_installed_versions()
        names = {item.split("=", 1)[0] for item in toolchain.package_pins}
        return {name: installed[name] for name in sorted(names) if name in installed}
    _kind, spec = host_spec(toolchain)
    return _dpkg_versions(list(spec["system_packages"]))


def configure_llvm(toolchain: MuslToolchain) -> None:
    binary = toolchain.llvm_prefix / "bin"
    patcher = Path(__file__).resolve().parents[1] / "build" / "patch_llvm_stack.py"
    result = subprocess.run(
        [os.environ.get("PYTHON", "python3"), str(patcher), str(binary)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise LinuxHostError(f"cannot patch the LLVM GNU_STACK headers: {result.stderr.strip()}")
    for name in ("clang", "lld"):
        report = _tool_output([str(binary / "llvm-readelf"), "-lW", str(binary / name)])
        if "GNU_STACK" not in report or "0x800000" not in report:
            raise LinuxHostError(f"LLVM {name} does not have the locked 8 MiB stack header")
    target_config = binary / f"{toolchain.target}.cfg"
    target_config.write_text(
        "\n".join((
            "--rtlib=compiler-rt",
            "--unwindlib=none",
            f"-L{toolchain.llvm_prefix / 'lib'}",
            "-isystem /usr/include/fortify",
            "",
        ))
    )
    cxx_config = binary / "clang++.cfg"
    existing = cxx_config.read_text() if cxx_config.is_file() else ""
    suffix = "".join(flag + "\n" for flag in ("-lc++abi", "-lunwind") if flag not in existing)
    if suffix:
        with cxx_config.open("a") as stream:
            stream.write(suffix)
    strip = binary / "strip"
    if strip.is_symlink() or strip.exists():
        strip.unlink()
    strip.symlink_to("llvm-strip")


def docker_offline() -> bool:
    return os.environ.get(_DOCKER_OFFLINE) == "1"


def _docker_network_proof() -> dict[str, Any]:
    if not Path("/.dockerenv").is_file():
        raise LinuxHostError(f"{_DOCKER_OFFLINE} requires a Docker container")
    interfaces = sorted(name for _, name in socket.if_nameindex())
    if interfaces != ["lo"]:
        raise LinuxHostError(f"Docker offline build requires loopback only, found {interfaces}")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # UDP connect checks routing without sending an application packet.
            probe.connect(("192.0.2.1", 9))
        except OSError as error:
            if error.errno != errno.ENETUNREACH:
                raise
        else:
            raise LinuxHostError("Docker offline build has an external IPv4 route")
    return {
        "ok": True,
        "mechanism": "docker --network none",
        "interfaces": interfaces,
        "external_ipv4_route": False,
    }


@dataclass
class SealedRun:
    """A command in the selected offline Linux build boundary."""

    write_paths: list[Path] = field(default_factory=list)
    home: Path | None = None

    @property
    def tmpdir(self) -> Path:
        return (self.write_paths[0] / "tmp") if self.write_paths else Path("/tmp")

    def prepare(self) -> None:
        for path in self.write_paths:
            Path(path).mkdir(parents=True, exist_ok=True)
        if self.write_paths:
            self.tmpdir.mkdir(parents=True, exist_ok=True)
        if self.home is not None:
            Path(self.home).mkdir(parents=True, exist_ok=True)

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.home) if self.home else "/nonexistent",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "SHELL": "/bin/sh",
            "TMPDIR": str(self.tmpdir),
            "SOURCE_DATE_EPOCH": "1704067200",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        if base:
            env.update(base)
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy",
                     "ALL_PROXY", "NO_PROXY", "no_proxy"):
            env.pop(name, None)
        return env

    def run(
        self, command: list[str], *, cwd: Path, env: dict[str, str],
        log: Path | None = None,
    ) -> subprocess.CompletedProcess:
        self.prepare()
        if docker_offline():
            _docker_network_proof()
            full = command
        else:
            full = [UNSHARE, *_UNSHARE_ARGS, *command]
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("w") as out:
                return subprocess.run(full, cwd=cwd, env=env, stdout=out,
                                      stderr=subprocess.STDOUT, check=False)
        return subprocess.run(full, cwd=cwd, env=env, capture_output=True, text=True, check=False)


_PROBE = """
import socket, sys
sock = socket.socket()
sock.settimeout(5)
sock.connect(("127.0.0.1", int(sys.argv[1])))
sock.sendall(b"ping")
sock.close()
print("CONNECTED")
"""


def network_boundary_selftest(python: Path, workdir: Path) -> dict[str, Any]:
    """Run one loopback probe outside and inside the namespace."""
    if docker_offline():
        return _docker_network_proof()
    if not Path(UNSHARE).is_file():
        raise LinuxHostError(f"{UNSHARE} is not present")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    probe = workdir / "network_probe.py"
    probe.write_text(_PROBE)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]

    def accept_loop() -> None:
        listener.settimeout(20)
        for _ in range(2):
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            connection.close()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    outside = subprocess.run([str(python), str(probe), str(port)],
                             capture_output=True, text=True, check=False)
    sealed = SealedRun(write_paths=[workdir], home=workdir / "home")
    inside = sealed.run([str(python), str(probe), str(port)], cwd=workdir,
                        env=sealed.environment())
    listener.close()
    thread.join(timeout=5)
    return {
        "probe": "tcp connect to a loopback listener owned by the validating process",
        "mechanism": "unshare --user --map-root-user --net",
        "unsandboxed_connected": "CONNECTED" in outside.stdout,
        "sandboxed_connected": "CONNECTED" in inside.stdout,
        "sandboxed_output": (inside.stdout + inside.stderr).strip()[:300],
        "ok": "CONNECTED" in outside.stdout and "CONNECTED" not in inside.stdout,
    }


def describe() -> dict[str, str]:
    if docker_offline():
        return {
            "mechanism": "docker --network none",
            "isolation": "Docker network namespace shared by commands in the build container",
            "network": "container loopback only and no external IPv4 route, checked before commands",
            "filesystem": "Docker container filesystem with a writable checkout mount",
        }
    return {
        "mechanism": "unshare --user --map-root-user --net",
        "isolation": "fresh user and network namespaces sharing the host kernel and "
                     "filesystem; weaker than the production container boundary",
        "network": "no interfaces up in the namespace, including loopback; verified by self-test",
        "filesystem": "writes are not restricted beyond ordinary permissions",
    }


# ---------------------------------------------------------------------------
# ELF inspection with the locked LLVM tools.


def _readelf(toolchain: LinuxToolchain, *arguments: str) -> str:
    readelf = toolchain.llvm_prefix / "bin" / "llvm-readelf"
    result = subprocess.run([str(readelf), *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LinuxHostError(f"llvm-readelf {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout


def elf_machine(toolchain: LinuxToolchain, path: Path) -> str:
    text = _readelf(toolchain, "-h", str(path))
    match = re.search(r"Machine:\s+(.+)", text)
    return match.group(1).strip() if match else ""


def elf_dynamic(toolchain: LinuxToolchain, path: Path) -> dict[str, list[str]]:
    text = _readelf(toolchain, "-d", str(path))
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", text)
    rpaths = re.findall(r"\((?:RPATH|RUNPATH)\)\s+Library (?:rpath|runpath): \[([^\]]+)\]", text)
    return {"needed": needed, "rpaths": rpaths}


def elf_defined_symbols(toolchain: LinuxToolchain, path: Path) -> list[str]:
    nm = toolchain.llvm_prefix / "bin" / "llvm-nm"
    result = subprocess.run([str(nm), "-g", "--defined-only", str(path)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LinuxHostError(f"llvm-nm failed on {path}: {result.stderr.strip()}")
    return [line.split()[-1] for line in result.stdout.splitlines() if line.split()]


# ---------------------------------------------------------------------------
# Source reconstruction when codeload.github.com is unreachable.

_CODELOAD = re.compile(
    r"^https://codeload\.github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/tar\.gz/(?P<commit>[0-9a-f]{40})$"
)


def _git_archive(input_: Input, workdir: Path) -> Path:
    match = _CODELOAD.match(input_.url)
    if match is None:
        raise InputError(f"{input_.name}: no git reconstruction for {input_.url}")
    commit = match["commit"]
    repository = workdir / "repository"
    remote = f"https://github.com/{match['owner']}/{match['repo']}"
    git_env = dict(os.environ)
    git_env["GIT_CEILING_DIRECTORIES"] = str(workdir)
    for argv in (
        ["git", "init", "-q", str(repository)],
        ["git", "-C", str(repository), "fetch", "-q", "--depth", "1", remote, commit],
    ):
        result = subprocess.run(argv, capture_output=True, text=True, env=git_env, timeout=1800, check=False)
        if result.returncode != 0:
            raise InputError(f"{input_.name}: {' '.join(argv[:4])} failed: {result.stderr.strip()}")
    output = workdir / "archive.tar.gz"
    with output.open("wb") as handle:
        archive = subprocess.Popen(
            ["git", "-C", str(repository), "archive", "--format=tar",
             f"--prefix={match['repo']}-{commit}/", commit],
            stdout=subprocess.PIPE, env=git_env,
        )
        gzip = subprocess.run(["gzip", "-n", "-c"], stdin=archive.stdout, stdout=handle, check=False)
        assert archive.stdout is not None
        archive.stdout.close()
        if archive.wait() != 0 or gzip.returncode != 0:
            raise InputError(f"{input_.name}: git archive reconstruction failed")
    return output


def fetch_source(cache: Cache, input_: Input, *, timeout: float = 600.0) -> tuple[Path, str]:
    """Fetch a pinned archive, reconstructing codeload tarballs through git.

    Returns the verified cache path and how it was obtained. The digest and
    size checks in `Cache.store` are the same for both routes.
    """
    try:
        return cache.require(input_), "cache"
    except InputError:
        pass
    try:
        return cache.fetch(input_, timeout=timeout), "download"
    except (urllib.error.URLError, OSError, InputError) as error:
        if _CODELOAD.match(input_.url) is None:
            raise InputError(f"{input_.name}: download failed: {error}") from error
        download_error = error
    cache.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="git-archive-", dir=cache.root) as temporary:
        try:
            archive = _git_archive(input_, Path(temporary))
            return cache.store(archive, input_), "git-archive-reconstruction"
        except InputError as error:
            raise InputError(
                f"{input_.name}: download failed ({download_error}) and git "
                f"reconstruction failed ({error})"
            ) from error
