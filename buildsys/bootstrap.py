"""Toolchain and trust roots for the macOS target.

LLVM is fetched from the official release archive, whose bytes, license, and
Sigstore provenance metadata are pinned in the bootstrap lock. A bounded
allowlist extracts only the compiler/runtime closure into `.cache`. Resolved
GNU make and pkgconf executables supply host tools; the selected Xcode supplies
the SDK and Apple linker. This module checks them against the lock and fails
closed when a toolchain is missing or differs.

Three things are deliberately *not* pinned here:

- **The linker.** clang's driver selects Apple's `ld` and passes the
  matching `libLTO.dylib` from its LLVM prefix, which makes ThinLTO
  work at all: a separately installed LLVM 23 compiler paired with an LLD
  from a different LLVM generation gets a bitcode-version rejection at link
  time, not a graceful fallback. The linker is recorded as an observation
  (see `linker_identity`) rather than named in the environment.
- **pkgconf's digest.** The installed bytes have no verified digest.
  The tool is recorded by version and path without claiming byte identity.
- **Host package-manager files.** HOME is scrubbed and `PKG_CONFIG_LIBDIR` is
  narrowed so the private dependency prefix is the only search root.
"""

from __future__ import annotations

import platform
import plistlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from buildlib.locks import LockError, load_bootstrap

from .inputs import Input, InputError
from .recipes import Toolchain


class BootstrapError(Exception):
    """The toolchain lock is malformed or names a missing component."""


@dataclass(frozen=True)
class XcodeFacts:
    developer_dir: Path | None
    sdkroot: Path | None
    sdk_version: str | None
    xcode_version: str | None
    xcode_build: str | None
    problems: tuple[str, ...]


@dataclass(frozen=True)
class MacOSToolchain:
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
    llvm_cryptographic_verification: dict
    llvm_archive_root: str
    make: Path | None
    make_version: str | None
    make_minimum_version: str
    pkgconf: Path | None
    pkgconf_version: str | None
    pkgconf_minimum_version: str
    xcode_major: int
    sdk_minimum_version: str
    xcode: XcodeFacts
    host_floor: str
    deployment_target: str
    cpu_baseline: str

    @property
    def llvm_profdata(self) -> Path:
        """The profile merger paired with the locked clang installation."""
        return self.llvm_prefix / "bin" / "llvm-profdata"

    @property
    def sdkroot(self) -> Path:
        if self.xcode.sdkroot is None:
            raise BootstrapError("xcrun did not resolve the macOS SDK")
        return self.xcode.sdkroot

    @property
    def xcode_version(self) -> str | None:
        return self.xcode.xcode_version

    def llvm_input(self) -> Input:
        """The exact official binary archive pinned by bootstrap.lock.json."""
        return Input(
            name="llvm-macos-aarch64",
            version=self.llvm_version,
            urls=(self.llvm_archive_url,),
            sha256=self.llvm_archive_sha256,
            size=self.llvm_archive_size,
            role="build-source",
            target="aarch64-apple-darwin",
            license=self.llvm_license,
            purpose="official LLVM compiler and profiling tools for macOS builds",
        )

    def llvm_attestation_input(self) -> Input:
        """The digest-pinned Sigstore provenance statement for the archive."""
        return Input(
            name="llvm-macos-aarch64-provenance",
            version=self.llvm_version,
            urls=(self.llvm_attestation_url,),
            sha256=self.llvm_attestation_sha256,
            size=self.llvm_attestation_size,
            role="build-source",
            target="aarch64-apple-darwin",
            purpose="Sigstore provenance statement for the LLVM release archive",
        )

    def toolchain(self, *, jobs: int | None = None) -> Toolchain:
        """The recipe Toolchain this lock describes."""
        if self.make is None or self.xcode.sdkroot is None:
            raise BootstrapError("macOS make and selected Xcode SDK must resolve before building")
        binary = self.llvm_prefix / "bin"
        settings: dict = {
            "cc": str(binary / "clang"),
            "cxx": str(binary / "clang++"),
            "ar": str(binary / "llvm-ar"),
            "ranlib": str(binary / "llvm-ranlib"),
            "ld": "",
            "nm": str(binary / "llvm-nm"),
            "strip": str(binary / "llvm-strip"),
            "make": str(self.make),
            "family": "macos",
            "sdkroot": str(self.xcode.sdkroot),
            "deployment_target": self.deployment_target,
            "cpu_baseline": self.cpu_baseline,
        }
        if jobs is not None:
            settings["jobs"] = jobs
        return Toolchain(**settings)

    def identity(self) -> dict:
        """The subset that determines build output, for cache/identity use."""
        profdata_version = _tool_output([str(self.llvm_profdata), "--version"])
        return {
            "family": "macos",
            "llvm_version": self.llvm_version,
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
                "cryptographic_verification": self.llvm_cryptographic_verification,
            },
            "llvm_profdata_version": profdata_version.splitlines()[0]
            if profdata_version else "unavailable",
            "sdk": {
                "version": self.xcode.sdk_version,
            },
            "xcode": {
                "version": self.xcode.xcode_version,
                "build": self.xcode.xcode_build,
            },
            "deployment_target": self.deployment_target,
            "host_floor": self.host_floor,
            "cpu_baseline": self.cpu_baseline,
            "xcode_major": self.xcode_major,
            "sdk_minimum_version": self.sdk_minimum_version,
            "host_tools": {
                "make": {
                    "role": "parallel build executor",
                    "version": self.make_version,
                    "capability": "GNU Make",
                },
                "pkgconf": {
                    "role": "pkg-config metadata resolver",
                    "version": self.pkgconf_version,
                    "capability": "pkg-config query interface",
                },
            },
        }

    def observed_facts(self) -> dict:
        return {
            "llvm_prefix": str(self.llvm_prefix),
            "llvm_resource_dir": str(self.llvm_resource_dir),
            "llvm_profdata_path": str(self.llvm_profdata),
            "developer_dir": (
                str(self.xcode.developer_dir) if self.xcode.developer_dir else None
            ),
            "sdk_path": str(self.xcode.sdkroot) if self.xcode.sdkroot else None,
            "sdk_version": self.xcode.sdk_version,
            "xcode_version": self.xcode.xcode_version,
            "xcode_build": self.xcode.xcode_build,
            "host_tools": {
                "make": {
                    "path": str(self.make) if self.make else None,
                    "version": self.make_version,
                },
                "pkgconf": {
                    "path": str(self.pkgconf) if self.pkgconf else None,
                    "version": self.pkgconf_version,
                },
            },
        }


@dataclass(frozen=True)
class AlpineImage:
    docker_arch: str
    platform: str
    alpine_arch: str
    digest: str
    release: str
    revision: str


@dataclass(frozen=True)
class APKPackage:
    name: str
    version: str
    arch: str
    url: str
    sha256: str
    size: int

    def input(self, target: str) -> Input:
        return Input(
            name=f"apk-{self.name}-{self.version}-{self.arch}",
            version=self.version,
            urls=(self.url,),
            sha256=self.sha256,
            size=self.size,
            role="build-source",
            target=target,
            purpose="pinned offline Linux build-toolchain package",
        )


@dataclass(frozen=True)
class PinnedArchive:
    name: str
    version: str
    url: str
    sha256: str
    size: int
    archive_root: str
    suffix: str

    def input(self, target: str) -> Input:
        return Input(
            name=self.name,
            version=self.version,
            urls=(self.url,),
            sha256=self.sha256,
            size=self.size,
            role="build-source",
            target=target,
            purpose="pinned offline Linux build toolchain",
        )

    @property
    def filename(self) -> str:
        return f"{self.sha256}{self.suffix}"


@dataclass(frozen=True)
class LinuxLLVM:
    version: str
    release: str
    license: str
    archives: dict[str, PinnedArchive]


GCC_TOOLCHAIN_PACKAGE = re.compile(
    r"gcc|g\+\+|gcc-.+|libgcc-static|libstdc\+\+-dev|binutils(-.+)?|build-base"
    r"|compiler-rt|clang\d*(-.+)?|llvm\d*(-.+)?|lld\d*(-.+)?"
)


@dataclass(frozen=True)
class LinuxBootstrap:
    release: str
    image_index_digest: str
    repositories: tuple[str, ...]
    images: dict[str, AlpineImage]
    direct_packages: tuple[tuple[str, str], ...]
    base_packages: tuple[str, ...]
    apk_closure: dict[str, tuple[APKPackage, ...]]
    llvm: LinuxLLVM


def _pinned_archive(item: dict, where: str, *, name: str, version: str, suffix: str) -> PinnedArchive:
    archive = PinnedArchive(
        name=name,
        version=version,
        url=_require(item, "url", where),
        sha256=_require(item, "sha256", where),
        size=_require(item, "size", where),
        archive_root=_require(item, "archive_root", where),
        suffix=suffix,
    )
    if not re.fullmatch(r"[0-9a-f]{64}", archive.sha256) or not isinstance(archive.size, int):
        raise BootstrapError(f"{where}: invalid digest or size")
    if not archive.url.startswith("https://") or not archive.url.endswith(suffix):
        raise BootstrapError(f"{where}: URL must be an https {suffix} archive")
    return archive


def _require(document: dict, key: str, where: str):
    if not isinstance(document, dict):
        raise BootstrapError(f"{where}: expected an object")
    if key not in document:
        raise BootstrapError(f"{where}: missing {key!r}")
    return document[key]


def load_linux_bootstrap(path: Path) -> LinuxBootstrap:
    try:
        document = load_bootstrap(path)
    except LockError as error:
        raise BootstrapError(f"cannot read bootstrap lock: {error}") from error
    section = document.get("linux_toolchain")
    if not isinstance(section, dict):
        raise BootstrapError(f"{path}: no linux_toolchain section")
    release = _require(section, "release", "linux_toolchain")
    revision = _require(section, "image_revision", "linux_toolchain")
    repositories = tuple(_require(section, "repositories", "linux_toolchain"))
    if not repositories:
        raise BootstrapError("linux_toolchain.repositories must not be empty")
    image_index_digest = _require(section, "image_index_digest", "linux_toolchain")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_index_digest):
        raise BootstrapError("linux_toolchain.image_index_digest is invalid")
    images_data = _require(section, "images", "linux_toolchain")
    images: dict[str, AlpineImage] = {}
    for docker_arch, platform_name, alpine_arch in (
        ("amd64", "linux/amd64", "x86_64"),
        ("arm64", "linux/arm64", "aarch64"),
    ):
        item = _require(images_data, docker_arch, "linux_toolchain.images")
        images[docker_arch] = AlpineImage(
            docker_arch=docker_arch,
            platform=_require(item, "platform", f"images.{docker_arch}"),
            alpine_arch=_require(item, "alpine_arch", f"images.{docker_arch}"),
            digest=_require(item, "digest", f"images.{docker_arch}"),
            release=_require(item, "release", f"images.{docker_arch}"),
            revision=_require(item, "revision", f"images.{docker_arch}"),
        )
        image = images[docker_arch]
        if image.platform != platform_name or image.alpine_arch != alpine_arch:
            raise BootstrapError(f"images.{docker_arch}: platform mapping is invalid")
        if image.release != release or image.revision != revision:
            raise BootstrapError(f"images.{docker_arch}: release identity differs from lock")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image.digest):
            raise BootstrapError(f"images.{docker_arch}: image digest is invalid")
    direct_data = _require(section, "direct_packages", "linux_toolchain")
    direct = tuple(
        (
            _require(item, "name", "linux_toolchain.direct_packages[]"),
            _require(item, "version", "linux_toolchain.direct_packages[]"),
        )
        for item in direct_data
    )
    if len({name for name, _ in direct}) != len(direct):
        raise BootstrapError("linux_toolchain.direct_packages contains duplicate names")
    closures: dict[str, tuple[APKPackage, ...]] = {}
    closure_data = _require(section, "apk_closure", "linux_toolchain")
    for alpine_arch in ("x86_64", "aarch64"):
        entries = _require(closure_data, alpine_arch, "linux_toolchain.apk_closure")
        packages = tuple(
            APKPackage(
                name=_require(item, "name", f"apk_closure.{alpine_arch}[]"),
                version=_require(item, "version", f"apk_closure.{alpine_arch}[]"),
                arch=_require(item, "arch", f"apk_closure.{alpine_arch}[]"),
                url=_require(item, "url", f"apk_closure.{alpine_arch}[]"),
                sha256=_require(item, "sha256", f"apk_closure.{alpine_arch}[]"),
                size=_require(item, "size", f"apk_closure.{alpine_arch}[]"),
            )
            for item in entries
        )
        names = [package.name for package in packages]
        if not packages:
            raise BootstrapError(f"{alpine_arch}: APK closure must not be empty")
        if len(names) != len(set(names)):
            raise BootstrapError(f"{alpine_arch}: duplicate APK package name")
        for package in packages:
            try:
                package.input(
                    "x86_64-unknown-linux-musl"
                    if alpine_arch == "x86_64"
                    else "aarch64-unknown-linux-musl"
                )
            except InputError as error:
                raise BootstrapError(f"{alpine_arch}: invalid APK pin: {error}") from error
            if package.arch not in (alpine_arch, "noarch"):
                raise BootstrapError(
                    f"{alpine_arch}: {package.name} has mismatched APK architecture "
                    f"{package.arch}"
                )
            filename = f"{package.name}-{package.version}.apk"
            if not any(
                package.url == f"{repository}/{alpine_arch}/{filename}"
                for repository in repositories
            ):
                raise BootstrapError(
                    f"{alpine_arch}: {package.name} URL is outside pinned repositories"
                )
        versions = {package.name: package.version for package in packages}
        for name, version in direct:
            if versions.get(name) != version:
                raise BootstrapError(
                    f"{alpine_arch}: direct package {name} must be {version}"
                )
        closures[alpine_arch] = packages
    for alpine_arch, packages in closures.items():
        forbidden = sorted(
            package.name for package in packages if GCC_TOOLCHAIN_PACKAGE.fullmatch(package.name)
        )
        if forbidden:
            raise BootstrapError(f"{alpine_arch}: APK closure contains GCC or Alpine LLVM toolchain packages: {forbidden}")
    llvm_data = _require(section, "llvm", "linux_toolchain")
    llvm_version = _require(llvm_data, "version", "linux_toolchain.llvm")
    archives_data = _require(llvm_data, "archives", "linux_toolchain.llvm")
    llvm = LinuxLLVM(
        version=llvm_version,
        release=_require(llvm_data, "release", "linux_toolchain.llvm"),
        license=_require(llvm_data, "license", "linux_toolchain.llvm"),
        archives={
            alpine_arch: _pinned_archive(
                _require(archives_data, alpine_arch, "linux_toolchain.llvm.archives"),
                f"linux_toolchain.llvm.archives.{alpine_arch}",
                name=f"llvm-musl-{alpine_arch}", version=llvm_version, suffix=".tar.xz",
            )
            for alpine_arch in ("x86_64", "aarch64")
        },
    )
    if not re.fullmatch(r"23\.\d+\.\d+(-rc\d+)?", llvm.version):
        raise BootstrapError(f"linux_toolchain.llvm must be LLVM 23, found {llvm.version}")
    base_packages = tuple(_require(section, "base_packages", "linux_toolchain"))
    if any(not re.fullmatch(r"[A-Za-z0-9+_.-]+=[A-Za-z0-9+_.-]+", entry) for entry in base_packages):
        raise BootstrapError("linux_toolchain.base_packages is malformed")
    base_names = [entry.split("=", 1)[0] for entry in base_packages]
    if len(base_names) != len(set(base_names)):
        raise BootstrapError("linux_toolchain.base_packages contains duplicate names")
    return LinuxBootstrap(
        release=release,
        image_index_digest=image_index_digest,
        repositories=repositories,
        images=images,
        direct_packages=direct,
        base_packages=base_packages,
        apk_closure=closures,
        llvm=llvm,
    )


def _run_output(command: list[str]) -> tuple[str, str | None]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return "", str(error)
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        return "", output or f"command exited {result.returncode}"
    return output, None


def _version_text(output: str) -> str | None:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)+)", output)
    return match.group(1) if match else None


def _resolve_host_tool(alternatives: tuple[str, ...]) -> tuple[Path | None, str | None]:
    for candidate in alternatives:
        executable = shutil.which(candidate)
        if executable is not None:
            path = Path(executable).resolve()
            output, error = _run_output([str(path), "--version"])
            return path, _version_text(output) if error is None else None
    return None, None


def _resolve_xcode_facts() -> XcodeFacts:
    found: list[str] = []
    sdk_output, sdk_error = _run_output(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"]
    )
    sdkroot = Path(sdk_output).resolve() if sdk_error is None else None
    if sdkroot is None:
        found.append(f"cannot resolve macOS SDK with xcrun: {sdk_error}")

    sdk_version, sdk_version_error = _run_output(
        ["xcrun", "--sdk", "macosx", "--show-sdk-version"]
    )
    if sdk_version_error is not None:
        found.append(f"cannot resolve macOS SDK version with xcrun: {sdk_version_error}")
        sdk_version = None

    xcodebuild, xcodebuild_error = _run_output(["xcrun", "--find", "xcodebuild"])
    developer_dir = None
    xcode_version = None
    xcode_build = None
    if xcodebuild_error is not None:
        found.append(f"cannot resolve Xcode with xcrun: {xcodebuild_error}")
    else:
        executable = Path(xcodebuild).resolve()
        if len(executable.parents) > 2:
            developer_dir = executable.parents[2]
        else:
            found.append(
                f"cannot resolve selected developer directory from {xcodebuild!r}"
            )
        version_output, version_error = _run_output([xcodebuild, "-version"])
        if version_error is not None:
            found.append(f"cannot query selected Xcode version: {version_error}")
        else:
            lines = version_output.splitlines()
            version_match = re.fullmatch(r"Xcode\s+(\S+)", lines[0].strip()) if lines else None
            build_match = re.fullmatch(r"Build version\s+(\S+)", lines[1].strip()) if len(lines) > 1 else None
            if version_match is None:
                found.append(f"unexpected selected Xcode version output: {version_output!r}")
            else:
                xcode_version = version_match.group(1)
            if build_match is not None:
                xcode_build = build_match.group(1)
    return XcodeFacts(
        developer_dir=developer_dir,
        sdkroot=sdkroot,
        sdk_version=sdk_version,
        xcode_version=xcode_version,
        xcode_build=xcode_build,
        problems=tuple(found),
    )


def load_macos_toolchain(path: Path) -> MacOSToolchain:
    """Read the macOS toolchain section of the bootstrap lock."""
    try:
        document = load_bootstrap(path)
    except LockError as error:
        raise BootstrapError(f"cannot read bootstrap lock: {error}") from error
    section = document.get("macos_toolchain")
    if not isinstance(section, dict):
        raise BootstrapError(f"{path}: no macos_toolchain section")
    llvm = _require(section, "llvm", "macos_toolchain")
    artifact = _require(llvm, "archive", "llvm")
    provenance = _require(artifact, "provenance", "llvm.archive")
    tools = _require(section, "host_tools", "macos_toolchain")
    make = _require(tools, "make", "host_tools")
    pkgconf = _require(tools, "pkgconf", "host_tools")
    xcode = _require(section, "xcode", "macos_toolchain")
    make_path, make_version = _resolve_host_tool(
        tuple(_require(make, "resolution", "host_tools.make"))
    )
    pkgconf_path, pkgconf_version = _resolve_host_tool(
        tuple(_require(pkgconf, "resolution", "host_tools.pkgconf"))
    )
    return MacOSToolchain(
        llvm_prefix=(
            Path(path).resolve().parent
            / ".cache"
            / "llvm"
            / "toolchains"
            / f"{_require(llvm, 'version', 'llvm')}-{_require(artifact, 'sha256', 'llvm.archive')}"
        ),
        llvm_resource_dir=(
            Path(path).resolve().parent
            / ".cache"
            / "llvm"
            / "toolchains"
            / f"{_require(llvm, 'version', 'llvm')}-{_require(artifact, 'sha256', 'llvm.archive')}"
            / _require(llvm, "resource_dir", "llvm")
        ),
        llvm_version=_require(llvm, "version", "llvm"),
        llvm_archive_url=_require(artifact, "url", "llvm.archive"),
        llvm_archive_sha256=_require(artifact, "sha256", "llvm.archive"),
        llvm_archive_size=_require(artifact, "size", "llvm.archive"),
        llvm_license=_require(artifact, "license", "llvm.archive"),
        llvm_release_tag=_require(provenance, "release_tag", "llvm.archive.provenance"),
        llvm_source_commit=_require(provenance, "source_commit", "llvm.archive.provenance"),
        llvm_attestation_url=_require(provenance, "attestation_url", "llvm.archive.provenance"),
        llvm_attestation_sha256=_require(
            provenance, "attestation_sha256", "llvm.archive.provenance"
        ),
        llvm_attestation_size=_require(
            provenance, "attestation_size", "llvm.archive.provenance"
        ),
        llvm_workflow=_require(provenance, "workflow", "llvm.archive.provenance"),
        llvm_workflow_path=_require(
            provenance, "workflow_path", "llvm.archive.provenance"
        ),
        llvm_cryptographic_verification=_require(
            provenance,
            "cryptographic_verification",
            "llvm.archive.provenance",
        ),
        llvm_archive_root=_require(artifact, "archive_root", "llvm.archive"),
        make=make_path,
        make_version=make_version,
        make_minimum_version=_require(make, "minimum_version", "host_tools.make"),
        pkgconf=pkgconf_path,
        pkgconf_version=pkgconf_version,
        pkgconf_minimum_version=_require(
            pkgconf, "minimum_version", "host_tools.pkgconf"
        ),
        xcode_major=_require(xcode, "major", "macos_toolchain.xcode"),
        sdk_minimum_version=_require(xcode, "sdk_minimum_version", "macos_toolchain.xcode"),
        xcode=_resolve_xcode_facts(),
        host_floor=_require(section, "host_floor", "macos_toolchain"),
        deployment_target=_require(section, "deployment_target", "macos_toolchain"),
        cpu_baseline=_require(section, "cpu_baseline", "macos_toolchain"),
    )


def _tool_output(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return f"<failed: {error}>"
    return (result.stdout + result.stderr).strip()


def sdk_version(sdkroot: Path) -> str:
    """The SDK's own declared version, from its SDKSettings.plist."""
    try:
        with (Path(sdkroot) / "SDKSettings.plist").open("rb") as handle:
            return str(plistlib.load(handle).get("Version", "unknown"))
    except (OSError, plistlib.InvalidFileException):
        return "unknown"


def linker_identity() -> dict:
    """Observed linker identity; recorded, not pinned (see module docstring)."""
    command = ["xcrun", "-f", "ld"]
    path = _tool_output(command).splitlines()[0].strip() if _tool_output(command) else ""
    version = _tool_output(["ld", "-v"]).splitlines()
    return {
        "path": path,
        "version": version[0].strip() if version else "unknown",
    }


def problems(toolchain: MacOSToolchain, *, host_floor: str = "") -> list[str]:
    """Every way the locked toolchain disagrees with the machine present.

    Returns an empty list when the host satisfies the lock. `doctor` reports
    these verbatim; the build fails closed on a non-empty list rather than
    silently building with a different compiler than the lock names.
    """
    found: list[str] = []
    host_floor = host_floor or toolchain.host_floor
    if host_floor:
        current = platform.mac_ver()[0]
        if not current:
            found.append("cannot determine host macOS version")
        elif _version_tuple(current) < _version_tuple(host_floor):
            found.append(
                f"host macOS {current} is below the required floor {host_floor}"
            )
    archive = (
        toolchain.llvm_prefix.parent.parent
        / "objects"
        / f"{toolchain.llvm_archive_sha256}.blob"
    )
    if not archive.is_file():
        found.append(
            "verified official LLVM archive is not cached at "
            f"{archive}; run `python3 build.py fetch "
            "--target aarch64-apple-darwin`"
        )
    else:
        from .llvm import verify_llvm_prefix_from_archive

        try:
            verify_llvm_prefix_from_archive(
                archive,
                toolchain.llvm_prefix,
                archive_root=toolchain.llvm_archive_root,
                sha256=toolchain.llvm_archive_sha256,
                size=toolchain.llvm_archive_size,
                version=toolchain.llvm_version,
            )
        except InputError as error:
            found.append(str(error))
    clang = toolchain.llvm_prefix / "bin" / "clang"
    if not clang.is_file():
        found.append(f"clang not found at {clang}")
    else:
        reported = _tool_output([str(clang), "--version"])
        if toolchain.llvm_version not in reported:
            found.append(
                f"{clang} reports {reported.splitlines()[0] if reported else 'nothing'}; "
                f"lock pins {toolchain.llvm_version}"
            )
        resource_dir = _tool_output([str(clang), "-print-resource-dir"])
        if Path(resource_dir) != toolchain.llvm_resource_dir:
            found.append(
                f"{clang} resource directory is {resource_dir!r}; expected "
                f"{toolchain.llvm_resource_dir}"
            )
    profdata = toolchain.llvm_profdata
    if not profdata.is_file():
        found.append(f"llvm-profdata not found at {profdata}")
    else:
        reported = _tool_output([str(profdata), "--version"])
        if toolchain.llvm_version not in reported:
            found.append(
                f"{profdata} reports {reported.splitlines()[0] if reported else 'nothing'}; "
                f"lock pins {toolchain.llvm_version}"
            )
    if toolchain.make is None:
        found.append("GNU Make 3.82 or newer is not available as gmake or make")
    else:
        reported = _tool_output([str(toolchain.make), "--version"])
        version = _version_text(reported)
        if not Path(toolchain.make).is_file() or not reported.startswith("GNU Make "):
            found.append(f"{toolchain.make} is not a usable GNU Make executable")
        elif version is None or _version_tuple(version) < _version_tuple(
            toolchain.make_minimum_version
        ):
            found.append(
                f"{toolchain.make} reports {reported.splitlines()[0] if reported else 'nothing'}; "
                f"requires GNU Make {toolchain.make_minimum_version} or newer"
            )
    if toolchain.pkgconf is None:
        found.append("pkgconf is not available on PATH")
    else:
        reported, version_error = _run_output([str(toolchain.pkgconf), "--version"])
        version = _version_text(reported)
        if version_error is not None or version is None:
            found.append(
                f"{toolchain.pkgconf} does not report a pkgconf version"
            )
        elif _version_tuple(version) < _version_tuple(toolchain.pkgconf_minimum_version):
            found.append(
                f"{toolchain.pkgconf} reports {version}; requires pkgconf "
                f"{toolchain.pkgconf_minimum_version} or newer"
            )
        capabilities, capability_error = _run_output([str(toolchain.pkgconf), "--help"])
        if capability_error is not None or "--modversion" not in capabilities:
            found.append(f"{toolchain.pkgconf} lacks the pkg-config query interface")
        pkg_config = shutil.which("pkg-config")
        if pkg_config is None:
            found.append("pkg-config executable required by CPython was not found on PATH")
        elif Path(pkg_config).resolve() != toolchain.pkgconf.resolve():
            found.append(
                f"pkg-config resolves to {Path(pkg_config).resolve()}, not "
                f"the verified pkgconf executable {toolchain.pkgconf}"
            )
    found.extend(toolchain.xcode.problems)
    if toolchain.xcode.developer_dir is None or not toolchain.xcode.developer_dir.is_dir():
        found.append("selected Xcode developer directory is unavailable")
    if toolchain.xcode.xcode_version is None:
        found.append("selected Xcode version is unavailable")
    elif _version_tuple(toolchain.xcode.xcode_version)[:1] != (toolchain.xcode_major,):
        found.append(
            f"selected Xcode {toolchain.xcode.xcode_version} does not match required major "
            f"{toolchain.xcode_major}"
        )
    if toolchain.xcode.sdkroot is None or not toolchain.xcode.sdkroot.is_dir():
        found.append("macOS SDK resolved by xcrun is unavailable")
    else:
        if (
            toolchain.xcode.developer_dir is not None
            and not toolchain.xcode.sdkroot.is_relative_to(toolchain.xcode.developer_dir)
        ):
            found.append(
                f"xcrun SDK {toolchain.xcode.sdkroot} is outside selected developer "
                f"directory {toolchain.xcode.developer_dir}"
            )
        declared = sdk_version(toolchain.xcode.sdkroot)
        reported_sdk = toolchain.xcode.sdk_version or declared
        if declared != "unknown" and reported_sdk != declared:
            found.append(
                f"xcrun reports SDK {reported_sdk}, but SDKSettings.plist reports {declared}"
            )
        if reported_sdk != "unknown" and _version_tuple(reported_sdk) < _version_tuple(
            toolchain.sdk_minimum_version
        ):
            found.append(
                f"SDK {reported_sdk} predates required SDK {toolchain.sdk_minimum_version}"
            )
    return found


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


_LTO_LIBRARY_C = "int answer(void) { return 42; }\n"
_LTO_MAIN_C = (
    "#include <stdio.h>\n"
    "extern int answer(void);\n"
    "int main(void) { printf(\"%d\\n\", answer()); return answer() != 42; }\n"
)


def lto_smoke_test(toolchain: Toolchain, workdir: Path) -> dict:
    """Prove the locked compiler and linker can actually build and run ThinLTO.

    This gate runs before any dependency build.
    On macOS the risk is specific and silent: clang passes its own
    `libLTO.dylib` to the platform linker, so a compiler/linker generation
    mismatch surfaces as a hard bitcode rejection — but only if LTO is
    actually exercised. A build that quietly dropped `-flto` would otherwise
    look like a pass, so the deployment floor and architecture of the result
    are inspected too, not just the exit status.
    """
    from . import macho

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    library = workdir / "lto-library.c"
    main = workdir / "lto-main.c"
    library.write_text(_LTO_LIBRARY_C)
    main.write_text(_LTO_MAIN_C)
    objects = workdir / "lto-library.o"
    archive = workdir / "liblto.a"
    binary = workdir / "lto-smoke"
    base = [toolchain.cc, "-O3", toolchain.cpu_baseline,
            f"-mmacosx-version-min={toolchain.deployment_target}", "-flto=thin"]

    steps = [
        (base + ["-c", str(library), "-o", str(objects)], "compile library"),
        ([toolchain.ar, "cr", str(archive), str(objects)], "archive"),
        (base + [str(main), str(archive), "-o", str(binary)], "link with thinlto"),
    ]
    for command, description in steps:
        result = subprocess.run(
            command, capture_output=True, text=True, env=toolchain.env(),
            check=False,
        )
        if result.returncode != 0:
            raise BootstrapError(
                f"LTO smoke test failed to {description}: "
                f"{(result.stderr or result.stdout).strip()[:400]}"
            )

    # Prove -flto actually took effect. If the flag were dropped or ignored,
    # the object would be an ordinary Mach-O and the build would report a
    # passing LTO gate while shipping non-LTO code. Darwin emits bitcode
    # inside a wrapper (0x0B17C0DE) rather than the bare 'BC\xc0\xde' form,
    # so both count as bitcode and anything else is a failure.
    magic = objects.read_bytes()[:4]
    if magic not in (b"BC\xc0\xde", b"\xde\xc0\x17\x0b"):
        raise BootstrapError(
            f"LTO smoke object is not LLVM bitcode (magic {magic!r}); "
            f"-flto=thin was not honoured by {toolchain.cc}"
        )

    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, env=toolchain.env(),
        check=False,
    )
    if run.returncode != 0 or run.stdout.strip() != "42":
        raise BootstrapError(
            f"LTO smoke binary did not run correctly: rc={run.returncode} "
            f"out={run.stdout.strip()!r} err={run.stderr.strip()!r}"
        )

    header = macho.read_header(binary)
    version = macho.build_version(binary)
    report = {
        "compiler": toolchain.cc,
        "lto_mode": "thin",
        "arch": header.arch,
        "minos": version.minos if version else None,
        "sdk": version.sdk if version else None,
        "expected_minos": toolchain.deployment_target,
        "runs": True,
    }
    if report["arch"] != "arm64":
        raise BootstrapError(f"LTO smoke produced {report['arch']}, expected arm64")
    if report["minos"] != toolchain.deployment_target:
        raise BootstrapError(
            f"LTO smoke minos {report['minos']} != declared floor "
            f"{toolchain.deployment_target}"
        )
    return report


def toolchain_for(target, lock_path: Path, *, jobs: int | None = None) -> Toolchain:
    """The toolchain a target's recipes build with.

    The macOS toolchain is resolved from the bootstrap lock and verified
    against the machine before any compilation starts, so a toolchain that
    silently changed under the lock fails here rather than producing an
    artifact nobody can attribute. The Linux targets keep the bare-name
    toolchain their container's PATH provides.
    """
    if not target.is_macos:
        return Toolchain(jobs=jobs) if jobs else Toolchain()
    locked = load_macos_toolchain(lock_path)
    found = problems(locked, host_floor=locked.host_floor)
    if found:
        raise BootstrapError(
            "locked macOS toolchain does not match this machine: " + "; ".join(found)
        )
    return locked.toolchain(jobs=jobs)
