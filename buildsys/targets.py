"""Target description: the one place architecture-specific data lives.

Every target shares one dependency graph, one toolchain model, and one
packaging pipeline; only the target-specific values below differ by machine.
The triples, machines, families, and Linux container strings come from
laputa-buildlib; this module adds the values only this build needs. The running
interpreter's own `platform.machine()` is what's actually true inside a
container or on a host, including under QEMU emulation. Target selection reads
that value rather than trusting an external flag that could disagree with
reality.

Two families are implemented, and they are deliberately different shapes:

  linux-musl  Alpine userspace in a pinned Docker container. ELF,
              musl loader, Alpine apk toolchain.
  macos       Native Apple Silicon macOS 27.0+. Mach-O,
              Apple libSystem, the locked official LLVM archive, and the Xcode SDK.

`family` selects the toolchain, relocation module, and binary-format checks.
Fields belonging to the other family are left unset; do not read
`musl_loader` on a macOS target or `deployment_target` on a Linux one.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

from buildlib import targets as shared
from buildlib.targets import LINUX_MUSL, MACOS, UnsupportedTargetError

FAMILIES = (LINUX_MUSL, MACOS)
_DOCKER_ARCHITECTURES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
}


@dataclass(frozen=True, kw_only=True)
class Target(shared.Target):
    cpu_baseline_cflag: str  # explicit, recorded ISA floor
    openssl_configure_target: str  # `Configure <target>` string
    uv_arch: str
    uv_os: str
    uv_libc: str
    pgo: bool = False

    # macos only
    deployment_target: str = ""  # -mmacosx-version-min floor, e.g. "27.0"
    platform_tag_prefix: str = ""  # PEP 425 macOS tag prefix, e.g. "macosx_27_0"

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise UnsupportedTargetError(f"{self.triple}: unknown family {self.family!r}")
        if self.family == LINUX_MUSL and not (self.alpine_arch and self.musl_loader):
            raise UnsupportedTargetError(f"{self.triple}: linux-musl target needs loader/arch")
        if self.family == MACOS and not self.deployment_target:
            raise UnsupportedTargetError(f"{self.triple}: macos target needs a deployment floor")
        if not all((self.uv_arch, self.uv_os, self.uv_libc)):
            raise UnsupportedTargetError(f"{self.triple}: target needs complete uv metadata")


def _describe(triple: str, **specific: str | bool) -> Target:
    base = shared.target_for_triple(triple)
    return Target(**{field.name: getattr(base, field.name) for field in fields(base)}, **specific)


TARGETS: dict[str, Target] = {
    "x86_64-unknown-linux-musl": _describe(
        "x86_64-unknown-linux-musl",
        # Broadwell is the oldest x86-64 floor the TLS backend supports;
        # Graviola requires AES-NI/PCLMULQDQ and the AVX2/BMI2 integer
        # instructions its constant-time code is built for.
        cpu_baseline_cflag="-march=broadwell",
        openssl_configure_target="linux-x86_64",
        uv_arch="x86_64",
        uv_os="linux",
        uv_libc="musl",
    ),
    "aarch64-unknown-linux-musl": _describe(
        "aarch64-unknown-linux-musl",
        # ARMv8-A with crypto extensions is the oldest arm64 floor the TLS
        # backend supports; Rust and C code both require AES, PMULL, SHA2,
        # and ASIMD on every CPU.
        cpu_baseline_cflag="-march=armv8-a+crypto",
        openssl_configure_target="linux-aarch64",
        uv_arch="aarch64",
        uv_os="linux",
        uv_libc="musl",
    ),
    # Apple Silicon only. `apple-m1` is clang 23's own default `-target-cpu`
    # for arm64-apple-macosx, made explicit so a toolchain change cannot move
    # the ISA floor silently; M1 is the oldest Apple Silicon, so this is the
    # narrowest baseline that still covers every supported machine.
    "aarch64-apple-darwin": _describe(
        "aarch64-apple-darwin",
        cpu_baseline_cflag="-mcpu=apple-m1",
        openssl_configure_target="darwin64-arm64-cc",
        uv_arch="aarch64",
        uv_os="darwin",
        uv_libc="none",
        deployment_target="27.0",
        platform_tag_prefix="macosx_27_0",
    ),
}


def target_for_triple(triple: str) -> Target:
    return TARGETS[shared.target_for_triple(triple).triple]


def docker_execution_mode(
    target: Target, daemon_architecture: str
) -> Literal["native", "emulated"]:
    if target.family != LINUX_MUSL:
        raise UnsupportedTargetError(
            f"Docker runtime qualification requires a Linux target: {target.triple}"
        )
    try:
        daemon_machine = _DOCKER_ARCHITECTURES[daemon_architecture]
    except KeyError:
        raise UnsupportedTargetError(
            f"unsupported Docker daemon architecture {daemon_architecture!r}"
        ) from None
    return "native" if daemon_machine == target.machine else "emulated"


def native_target() -> Target:
    """The target matching the machine this process is actually running on."""
    return TARGETS[shared.native_target().triple]
