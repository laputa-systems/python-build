"""x86_64 glibc Linux host support for the isolated Rust-for-CPython lane.

The macOS lane takes its toolchain from bootstrap.lock.json, its headers from
the Xcode SDK, and its sealed boundary from sandbox-exec. This module supplies
the Linux equivalents without touching the production 3.14.6 musl recipes:

* `linux-toolchain.lock.json` pins the official LLVM 23.1.2 x86_64 archive
  (same tag and source commit as the macOS archive) plus the exact host
  distribution packages that provide glibc and the development libraries.
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

from buildsys.inputs import Cache, Input, InputError

TARGET = "x86_64-unknown-linux-gnu"
UNSHARE = "/usr/bin/unshare"
_UNSHARE_ARGS = ("--user", "--map-root-user", "--net", "--")


class LinuxHostError(Exception):
    """The Linux lane toolchain or host cannot satisfy its lock."""


def supported_host() -> bool:
    return platform.system() == "Linux" and platform.machine() == "x86_64"


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
        return Path("/usr/lib/llvm-18/lib")

    def llvm_input(self) -> Input:
        return Input(
            name="llvm-linux-x86_64",
            version=self.llvm_version,
            url=self.llvm_archive_url,
            sha256=self.llvm_archive_sha256,
            size=self.llvm_archive_size,
            role="build-source",
            target=TARGET,
            license=self.llvm_license,
            purpose="official LLVM compiler, linker and profiling tools for the Linux lane",
        )

    def llvm_attestation_input(self) -> Input:
        return Input(
            name="llvm-linux-x86_64-provenance",
            version=self.llvm_version,
            url=self.llvm_attestation_url,
            sha256=self.llvm_attestation_sha256,
            size=self.llvm_attestation_size,
            role="build-source",
            target=TARGET,
            purpose="Sigstore provenance statement for the LLVM release archive",
        )

    def icu_input(self) -> Input:
        return Input(
            name="libicu70-lld-runtime",
            version=self.icu_runtime["package"].split("=", 1)[1],
            url=self.icu_runtime["url"],
            sha256=self.icu_runtime["sha256"],
            size=self.icu_runtime["size"],
            role="build-source",
            target=TARGET,
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
            "target": TARGET,
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


def _require(document: dict, key: str, where: str) -> Any:
    if key not in document:
        raise LinuxHostError(f"{where}: missing {key!r}")
    return document[key]


def load_linux_toolchain(path: Path, cache_root: Path) -> LinuxToolchain:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise LinuxHostError(f"cannot read Linux toolchain lock {path}: {error}") from error
    if document.get("target") != TARGET:
        raise LinuxHostError(f"{path}: target is not {TARGET}")
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
        icu_runtime=_require(document, "lld_icu_runtime", "lock"),
    )


def problems(toolchain: LinuxToolchain) -> list[str]:
    """Every way the host disagrees with the Linux lane lock."""
    found: list[str] = []
    if not supported_host():
        found.append(
            f"the Linux lane requires native x86_64 Linux (found {platform.system()} "
            f"{platform.machine()})"
        )
    release = _os_release()
    if (release.get("ID"), release.get("VERSION_ID")) != (
        toolchain.host_os_id, toolchain.host_os_version
    ):
        found.append(
            f"host distribution is {release.get('ID')} {release.get('VERSION_ID')}; "
            f"lock requires {toolchain.host_os_id} {toolchain.host_os_version}"
        )
    libc = platform.libc_ver()
    if libc != ("glibc", toolchain.glibc):
        found.append(f"host C library is {libc}; lock requires glibc {toolchain.glibc}")
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
    if f"GNU Make {toolchain.make_version}" not in _tool_output([str(toolchain.make), "--version"]):
        found.append(f"{toolchain.make} does not report GNU Make {toolchain.make_version}")
    if _tool_output([str(toolchain.pkgconf), "--version"]) != toolchain.pkgconf_version:
        found.append(f"{toolchain.pkgconf} does not report {toolchain.pkgconf_version}")
    installed = _dpkg_versions(list(toolchain.system_packages))
    for pinned in toolchain.system_packages:
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


def package_report(toolchain: LinuxToolchain) -> dict[str, str]:
    return _dpkg_versions(list(toolchain.system_packages))


@dataclass
class SealedRun:
    """A command in fresh user and network namespaces."""

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
