#!/usr/bin/env python3
"""Build and validate the isolated Rust-for-CPython lane.

The lane runs natively on Apple Silicon macOS (`aarch64-apple-darwin`) or on
x86_64 glibc Linux (`x86_64-unknown-linux-gnu`); the host selects the target.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

REPO = Path(__file__).resolve().parents[1]
LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(LANE))

import lane_linux

from buildsys import macho
from buildsys.bootstrap import (
    BootstrapError,
    load_macos_toolchain,
    sdk_version,
)
from buildsys.bootstrap import (
    problems as toolchain_problems,
)
from buildsys.inputs import (
    Cache,
    Input,
    InputError,
    load_lock,
    safe_extract,
)
from buildsys.llvm import (
    LINUX_X86_64_LAYOUT,
    MACOS_AARCH64_LAYOUT,
    extract_llvm_archive,
    verify_llvm_attestation_metadata,
)
from buildsys.sandbox import (
    SandboxError,
    SealedRun,
    network_boundary_selftest,
)
from buildsys.sandbox import (
    available as sandbox_available,
)
from buildsys.targets import target_for_triple

MACOS_TARGET = "aarch64-apple-darwin"
LINUX_TARGET = lane_linux.TARGET
# Source archives are platform independent. Locks written before the Linux
# lane name the macOS target; either lane target is accepted for them.
LANE_TARGETS = frozenset({MACOS_TARGET, LINUX_TARGET})
TARGET = LINUX_TARGET if lane_linux.supported_host() else MACOS_TARGET
IS_LINUX = TARGET == LINUX_TARGET
# Mach-O prefixes C symbol names with an underscore; ELF does not.
SYMBOL_PREFIX = "" if IS_LINUX else "_"
RUST_CHANNEL = "nightly-2026-09-15"
SOURCE_LOCK = LANE / "sources.lock.json"
OVERLAY = LANE / "overlay"
BOOTSTRAP_LOCK = REPO / "bootstrap.lock.json"
LINUX_TOOLCHAIN_LOCK = LANE / "linux-toolchain.lock.json"
CACHE_ROOT = REPO / ".cache"
CARGO_HOME = LANE / ".cargo-home"
WORK = LANE / "work"
BUILD = WORK / "build"
SOURCE = WORK / "source"
STAGE = LANE / "stage"
LOGS = LANE / "logs"
RESULTS = LANE / "results"
BUILD_REPORT = RESULTS / "build.json"
# Ubuntu 24.04 ships no libmpdec-dev, and the pinned 3.16 sources no longer
# bundle libmpdec. The Linux lane builds the product's locked mpdecimal source
# into a private static prefix so `_decimal` (and its Rust route) is built.
PRODUCT_SOURCE_LOCK = REPO / "sources.lock.json"
MPDECIMAL_PREFIX = WORK / "deps" / "mpdecimal"
# Byte-identical Ubuntu orig tarball of the locked upstream archive; the cache
# verifies it against the root lock's digest like any other route.
MPDECIMAL_MIRROR = (
    "http://archive.ubuntu.com/ubuntu/pool/universe/m/mpdecimal/"
    "mpdecimal_{version}.orig.tar.gz"
)


class LaneError(Exception):
    """The pinned experimental build lane cannot safely continue."""


class LaneTarget(NamedTuple):
    """The Linux lane's target; the production target table stays unchanged."""

    triple: str
    cpu_baseline_cflag: str


def _read_lock() -> tuple[dict[str, Any], Input]:
    try:
        document = json.loads(SOURCE_LOCK.read_text())
        metadata = document["source"]
        entries = load_lock(SOURCE_LOCK)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, InputError) as error:
        raise LaneError(f"cannot read Rust-for-CPython source lock: {error}") from error
    names = {entry.name for entry in entries}
    if "cpython-rust" not in names or names - {"cpython-rust", "rust-std-src"}:
        raise LaneError(f"{SOURCE_LOCK}: expected CPython and only the named Rust std input")
    entry = next(entry for entry in entries if entry.name == "cpython-rust")
    required = {
        "repository", "branch", "commit", "version", "license",
        "cargo_lock_sha256",
    }
    if not isinstance(metadata, dict):
        raise LaneError(f"{SOURCE_LOCK}: source metadata must be an object")
    missing = sorted(required - metadata.keys())
    if missing:
        raise LaneError(f"{SOURCE_LOCK}: source metadata is missing {', '.join(missing)}")
    if (
        entry.name != "cpython-rust"
        or entry.version != metadata["version"]
        or entry.role != "build-source"
        or entry.target not in LANE_TARGETS
        or metadata["commit"] not in entry.url
        or entry.license != metadata["license"]
    ):
        raise LaneError(f"{SOURCE_LOCK}: source metadata and archive pin disagree")
    return metadata, entry


def _read_rust_std_lock() -> tuple[dict[str, Any], Input]:
    # The compiler source is a separate archive owner, never a replacement for
    # CPython or a registry package with an invented path-crate checksum.
    _read_lock()
    try:
        document = json.loads(SOURCE_LOCK.read_text())
        metadata = document["rust_std_source"]
        entries = [entry for entry in load_lock(SOURCE_LOCK)
                   if entry.name == "rust-std-src"]
        required = {"version", "compiler_revision", "library_cargo_lock_sha256", "license"}
        if not isinstance(metadata, dict) or required - metadata.keys():
            raise LaneError(f"{SOURCE_LOCK}: incomplete Rust std source metadata")
        if len(entries) != 1:
            raise LaneError(f"{SOURCE_LOCK}: expected the named Rust std source input")
        entry = entries[0]
        revision = metadata["compiler_revision"]
        lock_hash = metadata["library_cargo_lock_sha256"]
        if (not isinstance(metadata["license"], str) or not metadata["license"]
                or entry.version != metadata["version"]
                or entry.license != metadata["license"]
                or entry.role != "build-source" or entry.target != MACOS_TARGET
                or entry.url != f"https://static.rust-lang.org/dist/{RUST_CHANNEL.removeprefix('nightly-')}/rust-src-nightly.tar.xz"
                or type(entry.size) is not int or entry.size <= 0
                or not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None
                or not isinstance(lock_hash, str) or re.fullmatch(r"[0-9a-f]{64}", lock_hash) is None):
            raise LaneError(f"{SOURCE_LOCK}: Rust std metadata and archive pin disagree")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, InputError) as error:
        raise LaneError(f"cannot read Rust std source lock: {error}") from error
    return metadata, entry


def _rust_std_source_root(extraction: Path) -> Path:
    metadata, _entry = _read_rust_std_lock()
    archive = extraction / "rust-src-nightly"
    library = archive / "rust-src/lib/rustlib/src/rust/library"
    try:
        if ((archive / "version").read_text().strip() != metadata["version"]
                or (archive / "git-commit-hash").read_text().strip() != metadata["compiler_revision"]
                or hashlib.sha256((library / "Cargo.lock").read_bytes()).hexdigest()
                != metadata["library_cargo_lock_sha256"]):
            raise LaneError("Rust std extracted version, revision or Cargo.lock differs from its pin")
        for name in ("std", "core", "alloc", "panic_abort"):
            if not (library / name / "Cargo.toml").is_file():
                raise LaneError(f"Rust std source is missing {name}/Cargo.toml")
    except OSError as error:
        raise LaneError(f"cannot verify extracted Rust std source: {error}") from error
    return library


def _extract_rust_std_source(destination: Path) -> Path:
    _metadata, source_input = _read_rust_std_lock()
    blob = Cache(CACHE_ROOT).require(source_input)
    # Fresh extraction restores all source bytes from the verified archive,
    # including path crates that Cargo does not checksum. Header checks alone
    # cannot authorize reuse of a previously modified compiler-source tree.
    if destination.exists():
        shutil.rmtree(destination)
    return _rust_std_source_root(safe_extract(blob, destination))


def _rust_std_source_environment(destination: Path) -> dict[str, str]:
    metadata, source_input = _read_rust_std_lock()
    library = _extract_rust_std_source(destination)
    receipt = destination / "source-input.json"
    receipt.write_text(json.dumps({
        "schema": 1,
        "input": dataclasses.asdict(source_input),
        "input_identity": source_input.identity(),
        "compiler_revision": metadata["compiler_revision"],
        "library_cargo_lock_sha256": metadata["library_cargo_lock_sha256"],
        "library": str(library.resolve()),
    }, indent=2, sort_keys=True) + "\n")
    return {
        "PYTHON_BUILD_RUST_STD_SOURCE": str(library.resolve()),
        "PYTHON_BUILD_RUST_STD_REVISION": metadata["compiler_revision"],
        "PYTHON_BUILD_RUST_STD_LOCK_SHA256": metadata["library_cargo_lock_sha256"],
        "PYTHON_BUILD_RUST_STD_INPUT_RECEIPT": str(receipt.resolve()),
    }


def _toolchain():
    if IS_LINUX:
        try:
            toolchain = lane_linux.load_linux_toolchain(LINUX_TOOLCHAIN_LOCK, CACHE_ROOT)
        except lane_linux.LinuxHostError as error:
            raise LaneError(str(error)) from error
        return toolchain, LaneTarget(LINUX_TARGET, toolchain.cpu_baseline)
    try:
        toolchain = load_macos_toolchain(BOOTSTRAP_LOCK)
    except (BootstrapError, OSError, ValueError) as error:
        raise LaneError(f"cannot load the locked macOS toolchain: {error}") from error
    target = target_for_triple(TARGET)
    if toolchain.cpu_baseline != target.cpu_baseline_cflag:
        raise LaneError(
            "bootstrap lock CPU baseline does not match the target table: "
            f"{toolchain.cpu_baseline!r} != {target.cpu_baseline_cflag!r}"
        )
    return toolchain, target


def _supported_host() -> bool:
    if IS_LINUX:
        return True
    return platform.system() == "Darwin" and platform.machine().lower() in {
        "arm64", "aarch64",
    }


def _require_host() -> None:
    if not _supported_host():
        raise LaneError(
            "rust-cpython supports only native Apple Silicon macOS or x86_64 "
            "glibc Linux hosts; cross-compilation is not implemented"
        )


def _command(
    argv: list[str], *, env: dict[str, str] | None = None, timeout: int = 30
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, env=env, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"argv": argv, "returncode": None, "output": str(error)}
    return {
        "argv": argv,
        "returncode": result.returncode,
        "output": (result.stdout + result.stderr).strip(),
    }


def _tool_output(argv: list[str]) -> str:
    return str(_command(argv, timeout=60)["output"])


def _rust_identity(rustup: str | None) -> dict[str, Any]:
    if rustup is None:
        return {
            "rustup": None,
            "installed": False,
            "rustc_vv": "",
            "cargo_v": "",
            "active_toolchain": "",
        }
    env = dict(os.environ)
    env["RUSTUP_TOOLCHAIN"] = RUST_CHANNEL
    env["CARGO_HOME"] = str(CARGO_HOME)
    listing = _command([rustup, "toolchain", "list"], env=env)
    installed = listing["returncode"] == 0 and any(
        line.split()[0].startswith(RUST_CHANNEL)
        for line in listing["output"].splitlines() if line.split()
    )
    if not installed:
        return {
            "rustup": rustup,
            "installed": False,
            "toolchain_list": listing["output"],
            "rustc_vv": "",
            "cargo_v": "",
            "active_toolchain": "",
        }
    return {
        "rustup": rustup,
        "installed": True,
        "toolchain_list": listing["output"],
        "rustc_vv": _command(
            [rustup, "run", RUST_CHANNEL, "rustc", "-Vv"], env=env
        )["output"],
        "cargo_v": _command(
            [rustup, "run", RUST_CHANNEL, "cargo", "-V"], env=env
        )["output"],
        "active_toolchain": _command(
            [rustup, "show", "active-toolchain"], env=env
        )["output"],
    }


def _mpdecimal_input() -> Input:
    try:
        entries = [entry for entry in load_lock(PRODUCT_SOURCE_LOCK) if entry.name == "mpdecimal"]
    except (OSError, InputError) as error:
        raise LaneError(f"cannot read the locked mpdecimal source: {error}") from error
    if len(entries) != 1 or entries[0].role != "source":
        raise LaneError(f"{PRODUCT_SOURCE_LOCK}: expected one mpdecimal source entry")
    return entries[0]


def _mpdecimal_cached() -> bool:
    input_ = _mpdecimal_input()
    if not _object_path(CACHE_ROOT, input_).is_file():
        return False
    try:
        Cache(CACHE_ROOT).require(input_)
    except InputError:
        return False
    return True


def _object_path(cache_root: Path, input_: Input) -> Path:
    return Path(cache_root) / "objects" / f"{input_.sha256}.blob"


def doctor_report() -> dict[str, Any]:
    metadata, source_input = _read_lock()
    toolchain, target = _toolchain()
    rustup = shutil.which("rustup")
    rust = _rust_identity(rustup)
    source_blob = _object_path(CACHE_ROOT, source_input)
    try:
        if source_blob.is_file():
            Cache(CACHE_ROOT).require(source_input)
            source_cached = True
        else:
            source_cached = False
    except InputError:
        source_cached = False
    llvm_cache = Cache(CACHE_ROOT / "llvm")
    llvm_archive = _object_path(llvm_cache.root, toolchain.llvm_input())
    llvm_prefix_ready = (
        toolchain.llvm_prefix / ".verified.json"
    ).is_file()
    if IS_LINUX:
        return _linux_doctor_report(
            metadata, source_input, source_cached, toolchain, target, rust, rustup,
            llvm_archive.is_file() and llvm_prefix_ready,
        )
    failures: list[str] = []
    if not _supported_host():
        failures.append(
            "this experiment supports only native Apple Silicon macOS "
            f"(found {platform.system()} {platform.machine()})"
        )
    failures.extend(toolchain_problems(toolchain, host_floor="26.0"))
    if rustup is None:
        failures.append("rustup is not on PATH")
    elif not rust["installed"]:
        failures.append(
            f"install the pinned compiler with `rustup toolchain install {RUST_CHANNEL} --profile minimal`"
        )
    elif not rust["rustc_vv"].startswith("rustc ") or "nightly" not in rust["rustc_vv"].splitlines()[0]:
        failures.append(f"rustup could not run the pinned compiler {RUST_CHANNEL}")
    if not rust.get("cargo_v", "").startswith("cargo ") or "nightly" not in rust["cargo_v"].splitlines()[0]:
        failures.append(f"rustup could not run the pinned Cargo for {RUST_CHANNEL}")
    if not source_cached:
        failures.append("the pinned CPython archive is not verified in .cache; run fetch")
    if not llvm_archive.is_file() or not llvm_prefix_ready:
        failures.append("the locked LLVM 23.1.2 toolchain is not provisioned; run fetch")
    if not sandbox_available():
        failures.append("/usr/bin/sandbox-exec is unavailable; the offline build cannot be sealed")
    active = rust.get("active_toolchain", "")
    if rust.get("installed") and not active.startswith(RUST_CHANNEL):
        failures.append(f"rustup did not activate {RUST_CHANNEL} for the build")

    clang = toolchain.llvm_prefix / "bin" / "clang"
    xcodebuild = _command(["xcodebuild", "-version"])
    sdk_report = _command(["xcrun", "--sdk", "macosx", "--show-sdk-version"])
    sdk_path_report = _command(["xcrun", "--sdk", "macosx", "--show-sdk-path"])
    locked_sdk_version = sdk_version(toolchain.sdkroot)
    reported_xcode_version = xcodebuild["output"].splitlines()[0] if xcodebuild["output"] else ""
    if xcodebuild["returncode"] != 0 or not reported_xcode_version.startswith(
        f"Xcode {toolchain.xcode_version}"
    ):
        failures.append(
            f"Xcode reports {reported_xcode_version or 'unavailable'}; "
            f"lock requires {toolchain.xcode_version}"
        )
    if locked_sdk_version == "unknown" or sdk_report["output"] != locked_sdk_version:
        failures.append(
            f"active macOS SDK reports {sdk_report['output'] or 'unavailable'}; "
            f"locked SDK at {toolchain.sdkroot} reports {locked_sdk_version}"
        )
    if (
        sdk_path_report["returncode"] != 0
        or not sdk_path_report["output"]
        or Path(sdk_path_report["output"]).resolve() != toolchain.sdkroot.resolve()
    ):
        failures.append(
            f"active SDK path {sdk_path_report['output'] or 'unavailable'} does not match "
            f"locked SDK path {toolchain.sdkroot}"
        )
    make_report = _command([str(toolchain.make), "--version"])
    report = {
        "host": {"os": platform.system(), "architecture": platform.machine()},
        "target": target.triple,
        "source": {
            **metadata,
            "archive_url": source_input.url,
            "archive_sha256": source_input.sha256,
            "archive_size": source_input.size,
            "cached_and_verified": source_cached,
        },
        "rust": rust,
        "cargo": {
            "private_home": str(CARGO_HOME),
            "wrapper_present": (CARGO_HOME / "bin" / "cargo").is_file(),
            "registry_cache_present": (CARGO_HOME / "registry").is_dir(),
        },
        "c_toolchain": {
            "locked_identity": toolchain.identity(),
            "clang_path": str(clang),
            "clang_version": _tool_output([str(clang), "--version"]),
            "llvm_archive_cached": llvm_archive.is_file(),
            "llvm_prefix_ready": llvm_prefix_ready,
        },
        "xcode": {
            "locked_version": toolchain.xcode_version,
            "reported_version": xcodebuild["output"],
            "sdk_path": str(toolchain.sdkroot),
            "sdk_version": locked_sdk_version,
            "active_sdk_version": sdk_report["output"],
            "active_sdk_path": sdk_path_report["output"],
        },
        "deployment_floor": toolchain.deployment_target,
        "gnu_make": {
            "path": str(toolchain.make),
            "version": make_report["output"],
        },
        "pgo_profdata": {
            "path": str(toolchain.llvm_profdata),
            "version": _tool_output([str(toolchain.llvm_profdata), "--version"]),
        },
        "network_sandbox_available": sandbox_available(),
        "problems": failures,
        "ok": not failures,
    }
    return report


def _rust_problems(rust: dict[str, Any], rustup: str | None) -> list[str]:
    failures: list[str] = []
    if rustup is None:
        failures.append("rustup is not on PATH")
    elif not rust["installed"]:
        failures.append(
            f"install the pinned compiler with `rustup toolchain install {RUST_CHANNEL} --profile minimal`"
        )
    elif not rust["rustc_vv"].startswith("rustc ") or "nightly" not in rust["rustc_vv"].splitlines()[0]:
        failures.append(f"rustup could not run the pinned compiler {RUST_CHANNEL}")
    if not rust.get("cargo_v", "").startswith("cargo ") or "nightly" not in rust["cargo_v"].splitlines()[0]:
        failures.append(f"rustup could not run the pinned Cargo for {RUST_CHANNEL}")
    active = rust.get("active_toolchain", "")
    if rust.get("installed") and not active.startswith(RUST_CHANNEL):
        failures.append(f"rustup did not activate {RUST_CHANNEL} for the build")
    return failures


def _linux_doctor_report(
    metadata: dict[str, Any], source_input: Input, source_cached: bool, toolchain,
    target, rust: dict[str, Any], rustup: str | None, llvm_ready: bool,
) -> dict[str, Any]:
    failures = lane_linux.problems(toolchain)
    failures.extend(_rust_problems(rust, rustup))
    if not source_cached:
        failures.append("the pinned CPython archive is not verified in .cache; run fetch")
    if not llvm_ready:
        failures.append("the locked LLVM 23.1.2 toolchain is not provisioned; run fetch")
    if not _mpdecimal_cached():
        failures.append("the locked mpdecimal source is not verified in .cache; run fetch")
    return {
        "host": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "target": target.triple,
        "source": {
            **metadata,
            "archive_url": source_input.url,
            "archive_sha256": source_input.sha256,
            "archive_size": source_input.size,
            "cached_and_verified": source_cached,
        },
        "rust": rust,
        "cargo": {
            "private_home": str(CARGO_HOME),
            "wrapper_present": (CARGO_HOME / "bin" / "cargo").is_file(),
            "registry_cache_present": (CARGO_HOME / "registry").is_dir(),
        },
        "c_toolchain": {
            "locked_identity": toolchain.identity(),
            "clang_path": str(toolchain.clang),
            "clang_version": _tool_output([str(toolchain.clang), "--version"]),
            "llvm_prefix_ready": llvm_ready,
        },
        "host_packages": lane_linux.package_report(toolchain),
        "gnu_make": {"path": str(toolchain.make),
                     "version": _tool_output([str(toolchain.make), "--version"])},
        "pgo_profdata": {
            "path": str(toolchain.llvm_profdata),
            "version": _tool_output([str(toolchain.llvm_profdata), "--version"]),
        },
        "network_sandbox": lane_linux.describe(),
        "problems": failures,
        "ok": not failures,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def doctor() -> int:
    try:
        report = doctor_report()
    except (LaneError, BootstrapError, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "problems": [str(error)]}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def _require_nightly() -> str:
    rustup = shutil.which("rustup")
    if rustup is None:
        raise LaneError("rustup is not on PATH")
    env = dict(os.environ)
    env["RUSTUP_TOOLCHAIN"] = RUST_CHANNEL
    env["CARGO_HOME"] = str(CARGO_HOME)
    result = subprocess.run(
        [rustup, "run", RUST_CHANNEL, "rustc", "-Vv"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise LaneError(
            f"the pinned Rust toolchain is unavailable; install it with "
            f"`rustup toolchain install {RUST_CHANNEL} --profile minimal`"
        )
    return rustup


def _rust_tool_wrappers(rustup: str) -> Path:
    # Cargo invokes rustc by name. Both launchers must be on the private
    # PATH because Homebrew can install its proxies outside rustup's directory.
    bin_dir = CARGO_HOME / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in ("cargo", "rustc"):
        wrapper = bin_dir / tool
        content = (
            "#!/bin/sh\n"
            f"exec {shlex.quote(rustup)} run {shlex.quote(RUST_CHANNEL)} {tool} \"$@\"\n"
        )
        if not wrapper.is_file() or wrapper.read_text() != content:
            wrapper.write_text(content)
        wrapper.chmod(0o755)
    return bin_dir / "cargo"


def _source_root(extraction: Path) -> Path:
    matches = [
        child for child in extraction.iterdir()
        if child.is_dir() and (child / "configure").is_file()
    ]
    if len(matches) != 1:
        raise LaneError(
            f"expected one CPython source directory beneath {extraction}, found {len(matches)}"
        )
    source = matches[0]
    for required in ("Cargo.toml", "Cargo.lock", "Lib", "Modules", "Include/patchlevel.h"):
        if not (source / required).exists():
            raise LaneError(f"pinned source archive is missing {required}")
    metadata, _entry = _read_lock()
    found_lock_hash = hashlib.sha256((source / "Cargo.lock").read_bytes()).hexdigest()
    if found_lock_hash != metadata["cargo_lock_sha256"]:
        raise LaneError(
            "Cargo.lock digest disagrees with the source lock: "
            f"{found_lock_hash} != {metadata['cargo_lock_sha256']}"
        )
    return source


def _extract_fresh(destination: Path) -> Path:
    metadata, source_input = _read_lock()
    del metadata
    blob = Cache(CACHE_ROOT).require(source_input)
    if destination.exists():
        shutil.rmtree(destination)
    extraction = safe_extract(blob, destination)
    return _source_root(extraction)


def _apply_overlay(source: Path) -> dict[str, Any]:
    """Copy worktree source files over a freshly verified upstream tree."""
    digest = hashlib.sha256()
    count = 0
    if OVERLAY.is_symlink() or (OVERLAY.exists() and not OVERLAY.is_dir()):
        raise LaneError(f"source overlay is not a directory: {OVERLAY}")
    if not OVERLAY.is_dir():
        return {"files": 0, "sha256": digest.hexdigest()}
    for path in sorted(OVERLAY.rglob("*")):
        if ("__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}
                or path.name == ".DS_Store"):
            continue
        if path.is_symlink():
            raise LaneError(f"source overlay cannot contain symlinks: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise LaneError(f"source overlay contains a non-file: {path}")
        relative = path.relative_to(OVERLAY)
        if relative.parts[:2] == ("Lib", "test"):
            raise LaneError(f"CPython test files cannot be changed by the source overlay: {relative}")
        target = source / relative
        parent = source
        for component in relative.parts[:-1]:
            parent = parent / component
            if parent.is_symlink():
                raise LaneError(f"source overlay traverses a symlink: {relative}")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise LaneError(f"source overlay target is not a file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        mode = path.stat().st_mode & 0o777
        target.write_bytes(data)
        target.chmod(mode)
        digest.update(
            relative.as_posix().encode() + b"\0" + str(mode).encode()
            + b"\0" + data + b"\0"
        )
        count += 1
    return {"files": count, "sha256": digest.hexdigest()}


def _llvm_layout():
    return LINUX_X86_64_LAYOUT if IS_LINUX else MACOS_AARCH64_LAYOUT


def _llvm_ready(toolchain) -> Path:
    llvm_cache = Cache(CACHE_ROOT / "llvm")
    archive = llvm_cache.require(toolchain.llvm_input())
    attestation = llvm_cache.require(toolchain.llvm_attestation_input())
    verify_llvm_attestation_metadata(
        attestation,
        archive_filename=Path(toolchain.llvm_archive_url).name,
        archive_sha256=toolchain.llvm_archive_sha256,
        release_tag=toolchain.llvm_release_tag,
        source_commit=toolchain.llvm_source_commit,
        workflow=toolchain.llvm_workflow,
        workflow_path=toolchain.llvm_workflow_path,
    )
    prefix = extract_llvm_archive(
        archive,
        toolchain.llvm_prefix,
        archive_root=toolchain.llvm_archive_root,
        sha256=toolchain.llvm_archive_sha256,
        size=toolchain.llvm_archive_size,
        version=toolchain.llvm_version,
        layout=_llvm_layout(),
    )
    if IS_LINUX:
        deb = llvm_cache.require(toolchain.icu_input())
        lane_linux.provision_icu_runtime(toolchain, deb)
    return prefix


def _fetch_llvm(toolchain) -> Path:
    llvm_cache = Cache(CACHE_ROOT / "llvm")
    if IS_LINUX:
        prefix = _fetch_llvm_archive(toolchain, llvm_cache)
        try:
            deb = llvm_cache.fetch(toolchain.icu_input())
            lane_linux.provision_icu_runtime(toolchain, deb)
        except (InputError, OSError, lane_linux.LinuxHostError) as error:
            raise LaneError(f"cannot provision the ld.lld ICU runtime: {error}") from error
        return prefix
    return _fetch_llvm_archive(toolchain, llvm_cache)


def _fetch_llvm_archive(toolchain, llvm_cache: Cache) -> Path:
    try:
        archive = llvm_cache.fetch(toolchain.llvm_input())
        attestation = llvm_cache.fetch(toolchain.llvm_attestation_input())
        verify_llvm_attestation_metadata(
            attestation,
            archive_filename=Path(toolchain.llvm_archive_url).name,
            archive_sha256=toolchain.llvm_archive_sha256,
            release_tag=toolchain.llvm_release_tag,
            source_commit=toolchain.llvm_source_commit,
            workflow=toolchain.llvm_workflow,
            workflow_path=toolchain.llvm_workflow_path,
        )
        return extract_llvm_archive(
            archive,
            toolchain.llvm_prefix,
            archive_root=toolchain.llvm_archive_root,
            sha256=toolchain.llvm_archive_sha256,
            size=toolchain.llvm_archive_size,
            version=toolchain.llvm_version,
            layout=_llvm_layout(),
        )
    except (InputError, OSError, ValueError) as error:
        raise LaneError(f"cannot provision the locked LLVM toolchain: {error}") from error


def _environment(toolchain, *, offline: bool, build_dir: Path | None = None) -> dict[str, str]:
    if build_dir is None:
        build_dir = BUILD
    rustup = shutil.which("rustup")
    rustup_directory = str(Path(rustup).parent) if rustup else "/usr/bin"
    kache = shutil.which("kache")
    cargo_bin = CARGO_HOME / "bin"
    llvm_bin = toolchain.llvm_prefix / "bin"
    path_parts = [
        str(cargo_bin), str(llvm_bin),
        *([str(Path(kache).parent)] if kache else []),
        rustup_directory,
        *(() if IS_LINUX else ("/opt/homebrew/bin",)),
        "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]
    path = ":".join(dict.fromkeys(path_parts))
    temp_dir = WORK / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = toolchain.toolchain().env()
    env.update({
        "PATH": path,
        "HOME": str(LANE / ".home"),
        "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
        "CARGO_HOME": str(CARGO_HOME),
        "CARGO_NET_OFFLINE": "true" if offline else "false",
        "CARGO_TARGET_DIR": str(build_dir / "target"),
        "RUSTUP_TOOLCHAIN": RUST_CHANNEL,
        "RUST_SHARED_BUILD": "1",
        "LLVM_TARGET": TARGET,
        "BINDGEN_EXTRA_CLANG_ARGS": f"-resource-dir={toolchain.llvm_resource_dir}",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": str(WORK / "pycache"),
        "TMPDIR": str(temp_dir),
        "PKG_CONFIG": str(toolchain.pkgconf),
    })
    return env


def _test_environment(toolchain) -> dict[str, str]:
    """Let CPython's test runner populate its isolated bytecode cache."""
    env = _environment(toolchain, offline=True)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    # A host egress proxy changes urllib/http.client test behavior; CPython's
    # module suites use direct loopback servers.
    for name in tuple(env):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            env.pop(name)
    return env


def _run_logged(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Path,
    sealed: SealedRun | None = None,
) -> subprocess.CompletedProcess:
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"RUN   {' '.join(shlex.quote(part) for part in argv)}")
    print(f"LOG   {log}")
    if sealed is not None:
        return sealed.run(argv, cwd=cwd, env=env, log=log)
    with log.open("w") as output:
        return subprocess.run(
            argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
            check=False,
        )


def _require_command(
    argv: list[str], *, cwd: Path, env: dict[str, str], log: Path,
    sealed: SealedRun | None = None,
) -> subprocess.CompletedProcess:
    result = _run_logged(argv, cwd=cwd, env=env, log=log, sealed=sealed)
    if result.returncode != 0:
        raise LaneError(
            f"command failed with exit status {result.returncode}; "
            f"inspect {log}"
        )
    return result


def _fetch_cargo_dependencies(source: Path, toolchain) -> None:
    env = _environment(toolchain, offline=False)
    env["PATH"] = f"{CARGO_HOME / 'bin'}:{env['PATH']}"
    command = [
        str(CARGO_HOME / "bin" / "cargo"), "fetch", "--locked",
        "--manifest-path", str(source / "Cargo.toml"),
    ]
    cargo_lock = source / "Cargo.lock"
    before = hashlib.sha256(cargo_lock.read_bytes()).hexdigest()
    _require_command(
        command, cwd=source, env=env, log=LOGS / "cargo-fetch.log"
    )
    after = hashlib.sha256(cargo_lock.read_bytes()).hexdigest()
    if after != before:
        raise LaneError("cargo fetch changed the locked Cargo.lock")


def fetch() -> int:
    _require_host()
    rustup = _require_nightly()
    _rust_tool_wrappers(rustup)
    metadata, source_input = _read_lock()
    toolchain, _target = _toolchain()
    try:
        source_blob, route = lane_linux.fetch_source(Cache(CACHE_ROOT), source_input)
    except (InputError, OSError) as error:
        raise LaneError(f"cannot fetch the pinned CPython source: {error}") from error
    print(f"OK    source archive ({route}) -> {source_blob}")
    if not IS_LINUX:
        _std_metadata, std_input = _read_rust_std_lock()
        try:
            std_blob = Cache(CACHE_ROOT).fetch(std_input)
        except (InputError, OSError) as error:
            raise LaneError(f"cannot fetch the pinned Rust std source: {error}") from error
        print(f"OK    Rust std source archive -> {std_blob}")
    _fetch_llvm(toolchain)
    print(f"OK    LLVM {toolchain.llvm_version} -> {toolchain.llvm_prefix}")
    if IS_LINUX:
        mpdecimal = _mpdecimal_input()
        mirror = dataclasses.replace(
            mpdecimal, url=MPDECIMAL_MIRROR.format(version=mpdecimal.version))
        errors = []
        for route in (mpdecimal, mirror):
            try:
                blob = Cache(CACHE_ROOT).fetch(route)
                break
            except (InputError, OSError) as error:
                errors.append(f"{route.url}: {error}")
        else:
            raise LaneError("cannot fetch the locked mpdecimal source: " + "; ".join(errors))
        print(f"OK    mpdecimal {mpdecimal.version} -> {blob}")
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cargo-fetch-", dir=WORK) as temporary:
        source = _source_root(safe_extract(source_blob, Path(temporary) / "source"))
        _apply_overlay(source)
        _fetch_cargo_dependencies(source, toolchain)
    print(
        f"OK    Cargo dependencies cached under {CARGO_HOME}; "
        f"source pin {metadata['commit']}"
    )
    return 0


def _test_jobs() -> int:
    return max(1, min(4, (os.cpu_count() or 4) - 1))


def _brew_pkg_config_path() -> str:
    brew = shutil.which("brew")
    if not brew:
        return ""
    found: list[str] = []
    for formula in ("openssl@3", "sqlite", "libffi", "xz", "bzip2", "zstd", "mpdecimal"):
        result = _command([brew, "--prefix", formula])
        if result["returncode"] != 0:
            continue
        pkgconfig = Path(result["output"]) / "lib" / "pkgconfig"
        if pkgconfig.is_dir():
            found.append(str(pkgconfig))
    return ":".join(dict.fromkeys(found))


def _macos_deployment_cache(deployment_target: str) -> dict[str, str]:
    # New SDKs expose these symbols to configure's link probes, but an
    # unguarded call cannot run below macOS 27. Keep the older POSIX paths.
    if tuple(int(part) for part in deployment_target.split(".")) < (27, 0):
        return {"ac_cv_func_dup3": "no", "ac_cv_func_pipe2": "no"}
    return {}


def _platform_flags(toolchain, target, prefix: Path | None = None) -> dict[str, str]:
    """C, C++, preprocessor, linker and pkg-config inputs for the host target.

    Linux binaries find libpython through an absolute runpath to the build's
    own install prefix, matching the macOS lane's absolute install names. An
    `$ORIGIN` runpath is not usable: the fork's cargo rule passes the linker
    arguments through a double-quoted shell word, which expands it away.
    """
    if prefix is None:
        prefix = STAGE
    if IS_LINUX:
        flags = f"-O0 -g3 {target.cpu_baseline_cflag} -fPIC"
        return {
            "CFLAGS": flags,
            "CXXFLAGS": flags,
            "CPPFLAGS": "",
            "PY_CPPFLAGS": "",
            "LDFLAGS": f"-fuse-ld=lld -Wl,-rpath,{Path(prefix) / 'lib'}",
            "PKG_CONFIG_PATH": "",
        }
    flags = " ".join((
        "-O0", "-g3", target.cpu_baseline_cflag, "-fPIC",
        f"-mmacosx-version-min={toolchain.deployment_target}",
    ))
    return {
        "CFLAGS": flags,
        "CXXFLAGS": flags,
        "CPPFLAGS": f"-isysroot {toolchain.sdkroot}",
        "PY_CPPFLAGS": f"-isysroot {toolchain.sdkroot}",
        "LDFLAGS": f"-mmacosx-version-min={toolchain.deployment_target}",
        "PKG_CONFIG_PATH": _brew_pkg_config_path(),
        **_macos_deployment_cache(toolchain.deployment_target),
    }


def _cargo_linker_variable() -> str:
    return f"CARGO_TARGET_{TARGET.upper().replace('-', '_')}_LINKER"


def _configuration(toolchain, target) -> tuple[list[str], dict[str, str]]:
    platform_flags = _platform_flags(toolchain, target)
    args = [
        f"--prefix={STAGE}",
        "--enable-shared",
        "--enable-experimental-jit=no",
        "--with-tail-call-interp=no",
        "--without-ensurepip",
    ]
    args.append("--with-pydebug")
    env = _environment(toolchain, offline=True)
    env.update(platform_flags)
    return args, env


def _sealed_sandbox():
    """The offline boundary for this host, proven against a live listener."""
    if IS_LINUX:
        return _linux_sandbox()
    if not sandbox_available():
        raise LaneError("offline build requires /usr/bin/sandbox-exec")
    sandbox = SealedRun(write_paths=[LANE], home=LANE / ".home")
    try:
        proof = network_boundary_selftest(Path(sys.executable), WORK / "sandbox-probe")
    except (SandboxError, OSError, subprocess.SubprocessError) as error:
        raise LaneError(f"cannot prove the offline network boundary: {error}") from error
    if not proof.get("ok"):
        raise LaneError(f"sandbox network-denial self-test failed: {proof}")
    return sandbox


def _linux_sandbox() -> lane_linux.SealedRun:
    sandbox = lane_linux.SealedRun(write_paths=[LANE], home=LANE / ".home")
    try:
        proof = lane_linux.network_boundary_selftest(Path(sys.executable), WORK / "sandbox-probe")
    except (lane_linux.LinuxHostError, OSError, subprocess.SubprocessError) as error:
        raise LaneError(f"cannot prove the offline network boundary: {error}") from error
    if not proof.get("ok"):
        raise LaneError(f"namespace network-denial self-test failed: {proof}")
    return sandbox


def _make_value(makefile: Path, name: str) -> str:
    prefix = f"{name}="
    for line in makefile.read_text(errors="replace").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _build_python() -> Path:
    makefile = BUILD / "Makefile"
    if not makefile.is_file():
        raise LaneError(f"configured build Makefile is missing: {makefile}")
    name = _make_value(makefile, "BUILDPYTHON")
    suffix = _make_value(makefile, "BUILDEXE")
    name = name.replace("$(BUILDEXE)", suffix)
    if not name or "$" in name or Path(name).name != name:
        raise LaneError(f"cannot resolve CPython build interpreter name {name!r}")
    return BUILD / name


LINUX_ELF_MACHINE = "Advanced Micro Devices X86-64"


def _binary_identity(path: Path, toolchain) -> dict[str, Any]:
    """Architecture, deployment floor, and dynamic references of one binary."""
    if IS_LINUX:
        machine = lane_linux.elf_machine(toolchain, path)
        if machine != LINUX_ELF_MACHINE:
            raise LaneError(f"{path.name} ELF machine is {machine!r}, expected x86-64")
        dynamic = lane_linux.elf_dynamic(toolchain, path)
        return {"arch": "x86_64", "minos": "", "dependencies": dynamic["needed"],
                "rpaths": dynamic["rpaths"]}
    header = macho.read_header(path)
    version = macho.build_version(path)
    if header.arch != "arm64":
        raise LaneError(f"{path.name} architecture is {header.arch}, expected arm64")
    if version is None or version.minos != toolchain.deployment_target:
        raise LaneError(
            f"{path.name} deployment floor is {version.minos if version else None}, "
            f"expected {toolchain.deployment_target}"
        )
    return {"arch": header.arch, "minos": version.minos,
            "dependencies": macho.dependencies(path), "rpaths": macho.rpaths(path)}


def _check_linux_interpreter(python: Path, toolchain, install: Path) -> None:
    """The installed interpreter must find libpython relative to itself."""
    dynamic = lane_linux.elf_dynamic(toolchain, python.resolve())
    # configure repeats LDFLAGS on the link line, so the entry can repeat.
    entries = {entry for value in dynamic["rpaths"] for entry in value.split(":")}
    if entries != {str(install / "lib")}:
        raise LaneError(f"installed interpreter runpath is {dynamic['rpaths']!r}")
    if not any(name.startswith("libpython3.16") for name in dynamic["needed"]):
        raise LaneError("installed interpreter does not link the shared libpython")
    allowed = {str(install / "lib")}
    for binary in sorted((install / "lib").glob("**/*.so*")):
        if binary.is_symlink() or not binary.is_file():
            continue
        runpaths = lane_linux.elf_dynamic(toolchain, binary)["rpaths"]
        found = {entry for value in runpaths for entry in value.split(":")}
        if not found <= allowed:
            raise LaneError(f"{binary.relative_to(install)} has unexpected runpath {sorted(found)}")


def _defined_symbols(path: Path, toolchain) -> str:
    symbols = _command([
        str(toolchain.llvm_prefix / "bin" / "llvm-nm"), "-g", "--defined-only", str(path),
    ])
    if symbols["returncode"] != 0:
        raise LaneError(f"cannot inspect symbols of {path}: {symbols['output']}")
    return str(symbols["output"])


def _module_report(install: Path, python: Path, toolchain) -> dict[str, Any]:
    code = r'''
import base64, binascii, json, sys, sysconfig, _base64
vectors = [b"", b"f", b"fo", b"foo", bytes(range(256)), b"rust-cpython" * 4096]
for value in vectors:
    actual = _base64.standard_b64encode(value)
    expected = binascii.b2a_base64(value, newline=False)
    if actual != expected or actual != base64.b64encode(value):
        raise SystemExit("_base64 disagrees with the CPython base64/binascii result")
for value in (bytearray(b"buffer"), memoryview(b"view")):
    if _base64.standard_b64encode(value) != binascii.b2a_base64(value, newline=False):
        raise SystemExit("_base64 buffer-protocol result differs from binascii")
if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 16):
    raise SystemExit("interpreter is not CPython 3.16")
if sysconfig.get_config_var("Py_GIL_DISABLED") in (1, "1"):
    raise SystemExit("the experimental build unexpectedly disabled the GIL")
if hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled():
    raise SystemExit("the interpreter reports that the GIL is disabled")
print(json.dumps({
    "implementation": sys.implementation.name,
    "version": sys.version,
    "gil_disabled": sysconfig.get_config_var("Py_GIL_DISABLED"),
    "gil_enabled": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True,
    "extension_suffix": sysconfig.get_config_var("EXT_SUFFIX"),
    "config_args": sysconfig.get_config_var("CONFIG_ARGS"),
    "cflags": sysconfig.get_config_var("CFLAGS"),
    "ldflags": sysconfig.get_config_var("LDFLAGS"),
    "base64_vectors": len(vectors) + 2,
    "base64_matches_binascii": True,
}))
'''
    env = _environment(toolchain, offline=True)
    result = subprocess.run(
        [str(python), "-I", "-S", "-c", code],
        cwd=install,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise LaneError(
            "installed interpreter/_base64 smoke test failed: "
            f"{(result.stderr or result.stdout).strip()[-1000:]}"
        )
    try:
        interpreter = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise LaneError(f"cannot parse installed interpreter report: {result.stdout!r}") from error
    suffix = interpreter["extension_suffix"]
    module_path = Path(subprocess.run(
        [str(python), "-I", "-S", "-c", "import _base64; print(_base64.__file__)"],
        cwd=install, env=env, capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()).resolve()
    if not module_path.name.endswith(suffix):
        raise LaneError(f"Rust module {module_path.name} does not use {suffix}")
    try:
        module_path.relative_to(install.resolve())
    except ValueError as error:
        raise LaneError(f"Rust module was loaded outside the stage tree: {module_path}") from error

    binary = _binary_identity(module_path, toolchain)
    dependencies = binary["dependencies"]
    rpaths = binary["rpaths"]
    if IS_LINUX:
        _check_linux_interpreter(python, toolchain, install)
    forbidden_markers = (
        "/opt/homebrew", ".rustup", ".cargo-home", "/.cache/llvm/",
    )
    unexpected = [
        item for item in [*dependencies, *rpaths]
        if any(marker in item for marker in forbidden_markers)
    ]
    if unexpected:
        raise LaneError(
            "Rust module has a build-tool/Homebrew dependency or rpath: "
            + ", ".join(unexpected)
        )
    nm = toolchain.llvm_prefix / "bin" / "llvm-nm"
    symbols = _command([str(nm), "-g", str(module_path)])
    if symbols["returncode"] != 0:
        raise LaneError(f"cannot inspect Rust module exports: {symbols['output']}")
    exports = []
    for line in symbols["output"].splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-1].lstrip("_") == "PyInit__base64":
            exports.append(fields[-1])
    if not exports:
        raise LaneError("Rust module does not export PyInit__base64")
    interpreter["module_path"] = str(module_path)
    interpreter["module_dependencies"] = dependencies
    interpreter["module_rpaths"] = rpaths
    interpreter["module_exports"] = exports
    interpreter["module_arch"] = binary["arch"]
    interpreter["module_minos"] = binary["minos"]
    return interpreter


def _workspace_members(source: Path, env: dict[str, str]) -> list[str]:
    command = [
        str(CARGO_HOME / "bin" / "cargo"), "metadata", "--locked", "--offline",
        "--no-deps", "--format-version", "1", "--manifest-path",
        str(source / "Cargo.toml"),
    ]
    result = subprocess.run(
        command, cwd=source, env=env, capture_output=True, text=True, timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise LaneError(f"cargo metadata failed: {(result.stderr or result.stdout).strip()}")
    document = json.loads(result.stdout)
    names = {package["id"]: package["name"] for package in document["packages"]}
    return sorted(names[member] for member in document["workspace_members"] if member in names)


class CompressionEngine(NamedTuple):
    carrier_package: str
    engine_crate: str
    library: str
    configure_prefix: str

    @property
    def archive(self) -> str:
        return f"lib{self.library}.a"


COMPRESSION_ENGINES = (
    CompressionEngine("cpython-libz-rs", "libz-rs-sys", "cpython_libz_rs", "ZLIB"),
    CompressionEngine("cpython-libbz2-rs", "libbz2-rs-sys", "cpython_libbz2_rs", "BZIP2"),
)


def _package_include_directory(source: Path, env: dict[str, str], package: str) -> Path:
    result = subprocess.run(
        [str(CARGO_HOME / "bin" / "cargo"), "metadata", "--locked", "--offline",
         "--format-version", "1", "--manifest-path", str(source / "Cargo.toml")],
        cwd=source, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise LaneError(f"cargo metadata failed: {(result.stderr or result.stdout).strip()}")
    manifests = [Path(item["manifest_path"]) for item in json.loads(result.stdout)["packages"]
                 if item["name"] == package]
    if len(manifests) != 1 or not (manifests[0].parent / "include").is_dir():
        raise LaneError(f"locked crate {package} has no unique include directory: {manifests}")
    return manifests[0].parent / "include"


def compression_engine_environment(
    source: Path, env: dict[str, str], *, target_dir: Path, profile: str, log: Path,
    sealed: SealedRun | None = None,
) -> dict[str, str]:
    """Static zlib-rs and libbz2-rs archives, as CPython's own configure inputs.

    CPython links its stock zlib and bzip2 modules through ZLIB_* and BZIP2_*.
    The returned variables point those at archives of the locked crates, with
    each crate's own header, so no system libz or libbz2 enters the modules.
    The Apple linker hides the archive's symbols and drops what the module
    does not reach, so a module exports only its own entry points.
    """
    if profile not in {"dev", "release"}:
        raise LaneError(f"compression engines build with the dev or release profile, not {profile!r}")
    cargo_env = dict(env)
    cargo_env["CARGO_TARGET_DIR"] = str(target_dir)
    _require_command(
        [str(CARGO_HOME / "bin" / "cargo"), "build", "--lib", "--locked", "--offline",
         "--profile", profile, "--manifest-path", str(source / "Cargo.toml"),
         *[argument for engine in COMPRESSION_ENGINES
           for argument in ("--package", engine.carrier_package)]],
        cwd=source, env=cargo_env, log=log, sealed=sealed,
    )
    profile_directory = "release" if profile == "release" else "debug"
    variables: dict[str, str] = {}
    for engine in COMPRESSION_ENGINES:
        archive = target_dir / profile_directory / engine.archive
        if not archive.is_file():
            raise LaneError(f"compression engine archive was not built: {archive}")
        include = _package_include_directory(source, cargo_env, engine.engine_crate)
        variables[f"{engine.configure_prefix}_CFLAGS"] = f"-I{include}"
        if IS_LINUX:
            variables[f"{engine.configure_prefix}_LIBS"] = (
                f"-L{archive.parent} -Wl,--exclude-libs,ALL -Wl,--gc-sections "
                f"-l{engine.library}")
        else:
            variables[f"{engine.configure_prefix}_LIBS"] = (
                f"-L{archive.parent} -Wl,-hidden-l{engine.library} -Wl,-dead_strip")
    return variables


def _built_workspace_members(source: Path, env: dict[str, str]) -> list[str]:
    members = set(_workspace_members(source, env))
    pattern = re.compile(r"(?:Compiling|Fresh) ([^ ]+) v[^ ]+")
    found: set[str] = set()
    for log in sorted(LOGS.glob("cpython-*.log")):
        for line in log.read_text(errors="replace").splitlines():
            match = pattern.search(line)
            if match and match.group(1) in members:
                found.add(match.group(1))
    return sorted(found)


def _build_mpdecimal(toolchain, target, jobs: int, sandbox) -> dict[str, str]:
    """Static, position-independent libmpdec for the Linux `_decimal` module."""
    input_ = _mpdecimal_input()
    blob = Cache(CACHE_ROOT).require(input_)
    sources = WORK / "deps" / "src"
    for path in (sources, MPDECIMAL_PREFIX):
        if path.exists():
            shutil.rmtree(path)
    source = safe_extract(blob, sources) / f"mpdecimal-{input_.version}"
    if not (source / "configure").is_file():
        raise LaneError(f"locked mpdecimal archive has no configure script under {source}")
    env = _environment(toolchain, offline=True)
    env.update(_platform_flags(toolchain, target))
    env.update({"CC": str(toolchain.llvm_prefix / "bin" / "clang"), "LDFLAGS": "-fuse-ld=lld"})
    env = sandbox.environment(env)
    _require_command(
        [str(source / "configure"), f"--prefix={MPDECIMAL_PREFIX}",
         "--disable-shared", "--enable-static", "--disable-cxx"],
        cwd=source, env=env, log=LOGS / "mpdecimal-configure.log", sealed=sandbox,
    )
    _require_command([str(toolchain.make), f"-j{jobs}"], cwd=source, env=env,
                     log=LOGS / "mpdecimal-build.log", sealed=sandbox)
    _require_command([str(toolchain.make), "install"], cwd=source, env=env,
                     log=LOGS / "mpdecimal-install.log", sealed=sandbox)
    library = MPDECIMAL_PREFIX / "lib" / "libmpdec.a"
    if not library.is_file():
        raise LaneError(f"mpdecimal build did not produce {library}")
    return {
        "LIBMPDEC_CFLAGS": f"-I{MPDECIMAL_PREFIX / 'include'}",
        "LIBMPDEC_LIBS": f"{library} -lm",
    }


def _configure_source(source: Path, toolchain, target, jobs: int,
                      sandbox: SealedRun, overlay: dict[str, Any]) -> dict[str, Any]:
    rustup = _require_nightly()
    _rust_tool_wrappers(rustup)
    for path in (BUILD, STAGE):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    args, env = _configuration(toolchain, target)
    if IS_LINUX:
        env.update(_build_mpdecimal(toolchain, target, jobs, sandbox))
    env.update({
        "PY_CC": str(toolchain.llvm_prefix / "bin" / "clang"),
        "PY_CPPFLAGS": env["CPPFLAGS"],
        "PY_CFLAGS": env["CFLAGS"],
        "PYTHON_BUILD_DIR": str(BUILD),
        "CARGO_BUILD_JOBS": str(jobs),
        _cargo_linker_variable(): str(toolchain.llvm_prefix / "bin" / "clang"),
        "IPHONEOS_DEPLOYMENT_TARGET": "",
    })
    env = sandbox.environment(env)
    configure = [str(source / "configure"), *args]
    _require_command(configure, cwd=BUILD, env=env,
                     log=LOGS / "cpython-configure.log", sealed=sandbox)
    setup_local = source / "Modules" / "Setup.local"
    if setup_local.is_file():
        # CPython reads local module declarations from the build directory.
        build_setup_local = BUILD / "Modules" / "Setup.local"
        build_setup_local.parent.mkdir(parents=True, exist_ok=True)
        setup_contents = setup_local.read_text()
        if IS_LINUX:
            setup_contents = lane_linux.setup_local_with_shared_ssl_rs(setup_contents)
        build_setup_local.write_text(setup_contents)
    make = [str(toolchain.make), f"-j{jobs}"]
    _require_command(make, cwd=BUILD, env=env,
                     log=LOGS / "cpython-build.log", sealed=sandbox)
    _require_command([str(toolchain.make), "install"], cwd=BUILD, env=env,
                     log=LOGS / "cpython-install.log", sealed=sandbox)
    python = STAGE / "bin" / "python3.16d"
    if not python.is_file():
        raise LaneError(f"CPython debug install did not produce {python}")
    module = _module_report(STAGE, python, toolchain)
    makefile = BUILD / "Makefile"
    cargo_profile = _make_value(makefile, "CARGO_PROFILE")
    if cargo_profile != "dev":
        raise LaneError(f"debug CPython configured Cargo profile {cargo_profile!r}; expected 'dev'")
    cargo_env = dict(env)
    cargo_env.update({
        "CARGO_TARGET_DIR": str(BUILD / "target"),
        "LLVM_TARGET": TARGET,
        "RUST_SHARED_BUILD": "1",
    })
    built_members = _built_workspace_members(source, cargo_env)
    overlay_members = set()
    for manifest in OVERLAY.glob("Modules/_*/Cargo.toml"):
        package = tomllib.loads(manifest.read_text()).get("package", {})
        name = package.get("name") if isinstance(package, dict) else None
        if not isinstance(name, str) or not name:
            raise LaneError(f"overlay Rust module has no package name: {manifest}")
        overlay_members.add(name)
    missing = ({"_base64", "cpython-sys"} | overlay_members) - set(built_members)
    if missing:
        raise LaneError("CPython build did not compile Rust members: " + ", ".join(sorted(missing)))
    metadata, source_input = _read_lock()
    report = {
        "status": "built",
        "build_mode": "debug",
        "target": TARGET,
        "source_commit": metadata["commit"],
        "source_archive_sha256": source_input.sha256,
        "overlay": overlay,
        "cargo_lock_sha256": hashlib.sha256((source / "Cargo.lock").read_bytes()).hexdigest(),
        "configure_arguments": configure,
        "cflags": env["CFLAGS"],
        "cargo_profile": cargo_profile,
        "interpreter": module,
        "build_interpreter": str(_build_python()),
        "rust": _rust_identity(rustup),
        "c_toolchain": toolchain.identity(),
        "sdk": _platform_sdk_report(toolchain),
        "tests": {},
    }
    _write_json(BUILD_REPORT, report)
    return report


def _platform_sdk_report(toolchain) -> dict[str, Any]:
    if IS_LINUX:
        return {"host_packages": lane_linux.package_report(toolchain),
                "libc": list(platform.libc_ver())}
    return {
        "path": str(toolchain.sdkroot),
        "version": sdk_version(toolchain.sdkroot),
        "xcode_version": toolchain.xcode_version,
    }


def build(*, jobs: int | None = None) -> int:
    _require_host()
    if jobs is not None and jobs < 1:
        raise LaneError("build jobs must be positive")
    if not doctor_report()["ok"]:
        raise LaneError("doctor found prerequisites missing; run doctor for details")
    toolchain, target = _toolchain()
    _llvm_ready(toolchain)
    source = _extract_fresh(SOURCE)
    overlay = _apply_overlay(source)
    env = _environment(toolchain, offline=True)
    _require_command(
        [str(CARGO_HOME / "bin" / "cargo"), "fetch", "--locked", "--offline",
         "--manifest-path", str(source / "Cargo.toml")],
        cwd=source, env=env, log=LOGS / "cargo-offline-check.log",
    )
    sandbox = _sealed_sandbox()
    workers = jobs if jobs is not None else max(1, (os.cpu_count() or 4) - 1)
    report = _configure_source(source, toolchain, target, workers, sandbox, overlay)
    print(f"OK    debug CPython {report['interpreter']['version'].split()[0]} -> {STAGE}")
    return 0


def _test_record(returncode: int, log: Path, summary: str = "") -> dict[str, Any]:
    return {
        "status": "passed" if returncode == 0 else "failed",
        "returncode": returncode,
        "log": str(log),
        "summary": summary,
    }


def _run_python_test(
    argv: list[str], *, cwd: Path, env: dict[str, str], log: Path
) -> dict[str, Any]:
    result = _run_logged(argv, cwd=cwd, env=env, log=log)
    output = log.read_text(errors="replace")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    summary = " | ".join(lines[-5:])
    return _test_record(result.returncode, log, summary)


def test(suites: list[str], *, all_suites: bool = False, jobs: int | None = None) -> int:
    _require_host()
    if all_suites == bool(suites):
        raise LaneError("select --all or at least one --suite, but not both")
    if any(re.fullmatch(r"test_[a-z0-9_]+", suite) is None for suite in suites):
        raise LaneError("suite names must be CPython test modules or packages named test_*")
    if jobs is not None and jobs < 1:
        raise LaneError("test jobs must be positive")
    if not BUILD_REPORT.is_file() or not _build_python().is_file():
        raise LaneError("no completed Rust-for-CPython debug build; run build first")
    report = json.loads(BUILD_REPORT.read_text())
    if report.get("status") not in {
        "built", "suite-failed", "suite-passed", "all-failed", "all-passed"
    }:
        raise LaneError("build report does not describe a completed build; run build first")
    if report.get("build_mode") != "debug":
        raise LaneError("coverage suites require a debug build without PGO")
    toolchain, _target = _toolchain()
    env = _test_environment(toolchain)
    build_python = _build_python()
    scope = "all" if all_suites else "suite"
    name = "all" if all_suites else "-".join(suites)
    if len(name) > 160:
        # Keep long suite lists within the filesystem's file-name limit.
        name = f"{'-'.join(suites[:3])}-{hashlib.sha256(name.encode()).hexdigest()[:16]}"
    log = LOGS / f"cpython-{name}.log"
    workers = jobs if jobs is not None else _test_jobs()
    result = _run_python_test(
        [str(build_python), "-m", "test", "-j", str(workers), "--timeout=900", *suites],
        cwd=BUILD, env=env, log=log,
    )
    report["tests"] = {"scope": scope, "suites": suites, **result}
    report["status"] = f"{scope}-passed" if result["returncode"] == 0 else f"{scope}-failed"
    _write_json(BUILD_REPORT, report)
    if result["returncode"] != 0:
        raise LaneError(f"CPython suite failed; inspect {log}")
    print("OK    default-resource CPython suite" if all_suites
          else f"OK    CPython suites: {', '.join(suites)}")
    return 0


def clean() -> int:
    for path in (WORK, STAGE, LOGS, RESULTS, LANE / ".home"):
        if path.exists():
            shutil.rmtree(path)
    print(f"OK    removed work, stage, logs, and results; kept private Cargo registry at {CARGO_HOME}")
    return 0


def restore_default_signals() -> None:
    """Ensure child CPython processes receive interrupts from the terminal."""
    if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    if signal.getsignal(signal.SIGQUIT) == signal.SIG_IGN:
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)


def main(argv: list[str] | None = None) -> int:
    restore_default_signals()
    parser = argparse.ArgumentParser(description="Rust-for-CPython 3.16 coverage builder")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="report host and locked input readiness")
    commands.add_parser("fetch", help="fetch source, toolchain, and locked Cargo dependencies")
    build_parser = commands.add_parser("build", help="build native debug CPython offline")
    test_parser = commands.add_parser("test", help="run complete named CPython Python suites")
    commands.add_parser("clean", help="remove generated outputs")
    build_parser.add_argument("--jobs", type=int, metavar="N", help="parallel build jobs")
    test_parser.add_argument("--all", action="store_true", help="run all default-resource CPython tests")
    test_parser.add_argument("--jobs", type=int, metavar="N", help="parallel test workers")
    test_parser.add_argument("--suite", action="append", default=[], metavar="TEST_NAME",
                             help="complete CPython test module or package; repeatable")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor()
        if args.command == "fetch":
            return fetch()
        if args.command == "build":
            return build(jobs=args.jobs)
        if args.command == "test":
            return test(args.suite, all_suites=args.all, jobs=args.jobs)
        return clean()
    except (LaneError, InputError, BootstrapError, SandboxError, OSError, ValueError) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
