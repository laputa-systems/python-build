#!/usr/bin/env python3
"""Build and validate the isolated Rust-for-CPython lane.

The lane runs natively on Apple Silicon macOS (`aarch64-apple-darwin`) or in
the pinned x86_64 Alpine container (`x86_64-unknown-linux-musl`).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import http.server
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

REPO = Path(__file__).resolve().parents[1]
LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(LANE))

from lane_cache import cache_path, cache_root

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
    locked_inputs,
    pin_identity,
    safe_extract,
)
from buildsys.llvm import (
    ExtractionLayout,
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
SERC_OVERLAY = LANE / "serc-overlay"
SERC_CARRIER_MANIFEST = Path("Modules/cpython-rust-staticlib/Cargo.toml")
SERC_CARRIER_FEATURE = '[features]\nserc = ["dep:serc-lane"]\n'
SERC_CFLAGS_NODIST = "-DPy_SERC"
BOOTSTRAP_LOCK = REPO / "bootstrap.lock.json"
LINUX_TOOLCHAIN_LOCK = LANE / "linux-musl-toolchain.lock.json"
MUSL_X86_64_LAYOUT = ExtractionLayout(
    tools=frozenset({
        "bin/clang-23", "bin/lld", "bin/llvm-readobj", "bin/llvm-ar",
        "bin/llvm-nm", "bin/llvm-objcopy",
    }),
    links={
        "bin/clang": "clang-23", "bin/clang++": "clang",
        "bin/ld.lld": "lld", "bin/llvm-readelf": "llvm-readobj",
        "bin/llvm-strip": "llvm-objcopy", "bin/llvm-ranlib": "llvm-ar",
        "lib/libclang.so": "libclang.so.23.1",
        "lib/libclang.so.23.1": "libclang.so.23.1.5",
        "lib/clang/23/lib/linux/clang_rt.crtbegin.o": "clang_rt.crtbegin-x86_64.o",
        "lib/clang/23/lib/linux/clang_rt.crtend.o": "clang_rt.crtend-x86_64.o",
        "lib/clang/23/lib/linux/crtbegin.o": "clang_rt.crtbegin-x86_64.o",
        "lib/clang/23/lib/linux/crtbeginS.o": "clang_rt.crtbegin-x86_64.o",
        "lib/clang/23/lib/linux/crtbeginT.o": "clang_rt.crtbegin-x86_64.o",
        "lib/clang/23/lib/linux/crtend.o": "clang_rt.crtend-x86_64.o",
        "lib/clang/23/lib/linux/crtendS.o": "clang_rt.crtend-x86_64.o",
    },
    required=frozenset({
        "bin/clang-23", "bin/lld", "bin/llvm-readobj", "bin/llvm-ar",
        "bin/llvm-nm", "bin/llvm-objcopy",
        "lib/clang/23/include/stdint.h",
        "lib/clang/23/lib/linux/libclang_rt.builtins-x86_64.a",
        "lib/clang/23/lib/linux/clang_rt.crtbegin-x86_64.o",
        "lib/clang/23/lib/linux/clang_rt.crtend-x86_64.o",
        "lib/libclang.so.23.1.5",
    }),
    library_files=frozenset({"lib/libclang.so.23.1.5"}),
    resource_prefixes=("lib/clang/23/include/",),
    resource_files=frozenset({
        "lib/clang/23/lib/linux/libclang_rt.builtins-x86_64.a",
        "lib/clang/23/lib/linux/clang_rt.crtbegin-x86_64.o",
        "lib/clang/23/lib/linux/clang_rt.crtend-x86_64.o",
    }),
    max_member_bytes=320 * 1024 * 1024,
    max_prefix_bytes=900 * 1024 * 1024,
)
LOCAL_CACHE_ROOT = cache_root(REPO)
CACHE_ROOT = cache_root(REPO)
CARGO_HOME = LANE / ".cargo-home"
CARGO_CONFIG_ISOLATED_CWD = Path("/")
INDEX_FORWARDER_PORT = 18737
LANE_INDEX_SOURCE = "lane-official"
LANE_INDEX_URL = f"sparse+http://127.0.0.1:{INDEX_FORWARDER_PORT}/"
LANE_CARGO_SOURCE_ARGS = (
    "--config", f'source.crates-io.replace-with="{LANE_INDEX_SOURCE}"',
    "--config", f'source.{LANE_INDEX_SOURCE}.registry="{LANE_INDEX_URL}"',
)
LANE_CARGO_CONFIG = (
    "[source.crates-io]\n"
    f'replace-with = "{LANE_INDEX_SOURCE}"\n\n'
    f"[source.{LANE_INDEX_SOURCE}]\n"
    f'registry = "{LANE_INDEX_URL}"\n'
)
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
        entries = locked_inputs(SOURCE_LOCK)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, InputError) as error:
        raise LaneError(f"cannot read Rust-for-CPython source lock: {error}") from error
    names = {entry.name for entry in entries}
    if "cpython-rust" not in names or names - {"cpython-rust", "rust-std-src", "serc"}:
        raise LaneError(f"{SOURCE_LOCK}: expected CPython and only the named source inputs")
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


def _read_serc_lock() -> tuple[dict[str, Any], Input]:
    _read_lock()
    try:
        document = json.loads(SOURCE_LOCK.read_text())
        metadata = document["serc_source"]
        entries = [entry for entry in locked_inputs(SOURCE_LOCK) if entry.name == "serc"]
        required = {"repository", "commit", "version", "license", "cargo_lock_sha256"}
        if not isinstance(metadata, dict) or required - metadata.keys():
            raise LaneError(f"{SOURCE_LOCK}: incomplete Serc source metadata")
        if len(entries) != 1:
            raise LaneError(f"{SOURCE_LOCK}: expected one Serc source input")
        entry = entries[0]
        commit = metadata["commit"]
        lock_hash = metadata["cargo_lock_sha256"]
        if (not isinstance(metadata["license"], str) or not metadata["license"]
                or not isinstance(metadata["repository"], str) or not metadata["repository"]
                or entry.version != metadata["version"]
                or entry.license != metadata["license"]
                or entry.role != "build-source" or entry.target not in LANE_TARGETS
                or entry.url != f"https://codeload.github.com/astral-experiments/serc/tar.gz/{commit}"
                or type(entry.size) is not int or entry.size <= 0
                or not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None
                or not isinstance(lock_hash, str) or re.fullmatch(r"[0-9a-f]{64}", lock_hash) is None):
            raise LaneError(f"{SOURCE_LOCK}: Serc metadata and archive pin disagree")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, InputError) as error:
        raise LaneError(f"cannot read Serc source lock: {error}") from error
    return metadata, entry


def _read_rust_std_lock() -> tuple[dict[str, Any], Input]:
    # The compiler source is a separate archive owner, never a replacement for
    # CPython or a registry package with an invented path-crate checksum.
    _read_lock()
    try:
        document = json.loads(SOURCE_LOCK.read_text())
        metadata = document["rust_std_source"]
        entries = [entry for entry in locked_inputs(SOURCE_LOCK)
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
        "input": {
            "name": source_input.name, "version": source_input.version, "url": source_input.url,
            "sha256": source_input.sha256, "role": source_input.role, "target": source_input.target,
            "size": source_input.size, "license": source_input.license, "purpose": source_input.purpose,
        },
        "input_identity": pin_identity(source_input),
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
            toolchain = lane_linux.load_musl_toolchain(
                LINUX_TOOLCHAIN_LOCK, cache_root(REPO, LOCAL_CACHE_ROOT))
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
            "Alpine Linux in the pinned container; cross-compilation is not implemented"
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
        entries = [entry for entry in locked_inputs(PRODUCT_SOURCE_LOCK) if entry.name == "mpdecimal"]
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
    llvm_cache = Cache(_llvm_cache_root(toolchain))
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
        failures.append(f"the locked LLVM {toolchain.llvm_version} toolchain is not provisioned; run fetch")
    if not _mpdecimal_cached():
        failures.append("the locked mpdecimal source is not verified in .cache; run fetch")
    spec_kind, spec = lane_linux.host_spec(toolchain)
    return {
        "host": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "libc": ["musl", toolchain.alpine_release],
            "spec": spec_kind,
            "provisional": True,
            "provisional_reason": (
                "OD23: pinned Alpine container on an unqualified host; timing needs a "
                "qualified quiet x86-64 host"
            ),
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
        "pgo_profdata": None,
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
        if wrapper.is_symlink():
            wrapper.unlink()
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
    for required in ("Lib", "Modules", "Include/patchlevel.h"):
        if not (source / required).exists():
            raise LaneError(f"pinned source archive is missing {required}")
    return source


def _verify_cargo_lock(source: Path) -> None:
    metadata, _entry = _read_lock()
    lock = source / "Cargo.lock"
    if not lock.is_file():
        raise LaneError("assembled source has no Cargo.lock; the overlay must provide it")
    found_lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    if found_lock_hash != metadata["cargo_lock_sha256"]:
        raise LaneError(
            "Cargo.lock digest disagrees with the source lock: "
            f"{found_lock_hash} != {metadata['cargo_lock_sha256']}"
        )


def _extract_fresh(destination: Path, *, apply_serc: bool = True) -> Path:
    metadata, source_input = _read_lock()
    del metadata
    blob = Cache(CACHE_ROOT).require(source_input)
    if destination.exists():
        shutil.rmtree(destination)
    extraction = safe_extract(blob, destination)
    source = _source_root(extraction)
    if apply_serc:
        _apply_serc_source(source)
    return source


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


def _copy_overlay_tree(overlay: Path, destination: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    if overlay.is_symlink() or (overlay.exists() and not overlay.is_dir()):
        raise LaneError(f"source overlay is not a directory: {overlay}")
    if not overlay.is_dir():
        return {"files": 0, "sha256": digest.hexdigest()}
    for path in sorted(overlay.rglob("*")):
        if path.is_symlink():
            raise LaneError(f"source overlay cannot contain symlinks: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise LaneError(f"source overlay contains a non-file: {path}")
        relative = path.relative_to(overlay)
        target = destination / relative
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


def _apply_serc_source(source: Path) -> dict[str, Any]:
    metadata, entry = _read_serc_lock()
    blob = Cache(CACHE_ROOT).require(entry)
    destination = source / "serc"
    if destination.exists():
        shutil.rmtree(destination)
    safe_extract(blob, destination)
    roots = [child for child in destination.iterdir() if child.is_dir()]
    if len(roots) != 1:
        raise LaneError(
            f"expected one Serc source directory beneath {destination}, found {len(roots)}"
        )
    root = roots[0]
    for child in list(root.iterdir()):
        os.replace(child, destination / child.name)
    root.rmdir()
    lock = destination / "Cargo.lock"
    found_lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    if found_lock_hash != metadata["cargo_lock_sha256"]:
        raise LaneError(
            "Serc Cargo.lock digest disagrees with the source lock: "
            f"{found_lock_hash} != {metadata['cargo_lock_sha256']}"
        )
    return {"overlay": _copy_overlay_tree(SERC_OVERLAY, destination)}


def serc_carrier_manifest(manifest: str) -> str:
    """Return the static carrier manifest of a `--serc` build: `serc` is a default feature."""
    if manifest.count(SERC_CARRIER_FEATURE) != 1:
        raise LaneError(f"{SERC_CARRIER_MANIFEST} must declare its optional serc feature once")
    return manifest.replace(
        SERC_CARRIER_FEATURE, '[features]\ndefault = ["serc"]\nserc = ["dep:serc-lane"]\n')


def _link_serc(source: Path) -> None:
    manifest = source / SERC_CARRIER_MANIFEST
    manifest.write_text(serc_carrier_manifest(manifest.read_text()))


def _llvm_layout():
    if IS_LINUX and isinstance(_toolchain()[0], lane_linux.MuslToolchain):
        return MUSL_X86_64_LAYOUT
    return LINUX_X86_64_LAYOUT if IS_LINUX else MACOS_AARCH64_LAYOUT


def _llvm_cache_root(toolchain) -> Path:
    if isinstance(toolchain, lane_linux.MuslToolchain):
        return CACHE_ROOT / "bootstrap"
    return CACHE_ROOT / "llvm"


def _llvm_ready(toolchain) -> Path:
    llvm_cache = Cache(_llvm_cache_root(toolchain))
    archive = llvm_cache.require(toolchain.llvm_input())
    if isinstance(toolchain, lane_linux.MuslToolchain):
        extract_llvm_archive(
            archive,
            toolchain.llvm_raw_prefix,
            archive_root=toolchain.llvm_archive_root,
            sha256=toolchain.llvm_archive_sha256,
            size=toolchain.llvm_archive_size,
            version=toolchain.llvm_version,
            layout=_llvm_layout(),
        )
        prefix = lane_linux.configure_llvm(toolchain)
        return prefix
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
    llvm_cache = Cache(_llvm_cache_root(toolchain))
    if isinstance(toolchain, lane_linux.MuslToolchain):
        return _fetch_llvm_archive(toolchain, llvm_cache)
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
        if isinstance(toolchain, lane_linux.MuslToolchain):
            extract_llvm_archive(
                archive,
                toolchain.llvm_raw_prefix,
                archive_root=toolchain.llvm_archive_root,
                sha256=toolchain.llvm_archive_sha256,
                size=toolchain.llvm_archive_size,
                version=toolchain.llvm_version,
                layout=_llvm_layout(),
            )
            return lane_linux.configure_llvm(toolchain)
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


def _file_sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _musl_llvm_dwarfdump_inputs(toolchain, *, fetch: bool) -> dict[str, Path]:
    document = json.loads(LINUX_TOOLCHAIN_LOCK.read_text())
    entry = document["llvm_dwarfdump"]
    source = entry["source"]
    dev = entry["dev_archive"]
    if entry["version"] != toolchain.llvm_version:
        raise LaneError("llvm-dwarfdump version does not match the musl toolchain")

    inputs = (
        (
            "source",
            source["archive_url"],
            source["archive_sha256"],
            source["archive_size"],
            source["cache_path"],
        ),
        (
            "dev",
            dev["archive_url"],
            dev["archive_sha256"],
            dev["archive_size"],
            dev["cache_path"],
        ),
        (
            "attestation",
            source["attestation_url"],
            source["attestation_sha256"],
            source.get("attestation_size"),
            source["attestation_cache_path"],
        ),
    )
    cache = Cache(CACHE_ROOT / "bootstrap")
    paths = {}
    for name, url, sha256, size, cache_relative_path in inputs:
        input_ = Input(
            name=f"llvm-dwarfdump-{name}",
            version=entry["version"],
            urls=(url,),
            sha256=sha256,
            size=size,
            role="build-source",
            target=toolchain.target,
            license=entry["license"],
            purpose="locked input for lane-only llvm-dwarfdump",
        )
        blob = cache.fetch(input_) if fetch else cache.require(input_)
        path = cache_path(REPO, cache_relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}-", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copyfile(blob, temporary)
            if (size is not None and temporary.stat().st_size != size) or _file_sha256(temporary) != sha256:
                raise LaneError(f"{input_.name}: cached input digest/size mismatch")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        paths[name] = path
    return paths


def _cached_musl_llvm_dwarfdump(toolchain) -> Path:
    """Build the one missing LLVM JIT tool from cached locked inputs only."""
    document = json.loads(LINUX_TOOLCHAIN_LOCK.read_text())
    entry = document["llvm_dwarfdump"]
    recipe = entry["build"]
    dev = entry["dev_archive"]
    cached = cache_path(REPO, recipe["cache_path"])
    expected_binary_sha = recipe["binary_sha256"]
    expected_binary_size = recipe["binary_size"]
    if (
        cached.is_file()
        and not cached.is_symlink()
        and cached.stat().st_size == expected_binary_size
        and _file_sha256(cached) == expected_binary_sha
    ):
        return cached

    try:
        inputs = _musl_llvm_dwarfdump_inputs(toolchain, fetch=False)
    except (InputError, OSError) as error:
        raise LaneError(f"locked llvm-dwarfdump inputs are not cached: {error}") from error
    source_archive = inputs["source"]
    dev_archive = inputs["dev"]
    attestation = inputs["attestation"]
    if not attestation.is_file():
        raise LaneError(f"LLVM source attestation bundle is missing: {attestation}")

    dev_prefix = cache_path(REPO, dev["extracted_prefix"])
    llvm_config = dev_prefix / "bin" / "llvm-config"
    if not llvm_config.is_file():
        extraction_root = dev_prefix.parent
        if extraction_root.exists():
            raise LaneError(f"LLVM dev archive extraction is incomplete: {extraction_root}")
        try:
            safe_extract(dev_archive, extraction_root)
        except (InputError, OSError, tarfile.TarError) as error:
            raise LaneError(f"cannot extract the locked LLVM dev archive: {error}") from error
    version = subprocess.run(
        [str(llvm_config), "--version"], capture_output=True, text=True, check=False
    )
    if version.returncode or version.stdout.strip() != entry["version"]:
        raise LaneError(
            f"LLVM dev archive llvm-config reports {version.stdout.strip()!r}, "
            f"expected {entry['version']}"
        )

    names = (
        "Coverage.cpp",
        "SectionSizes.cpp",
        "Statistics.cpp",
        "llvm-dwarfdump.cpp",
        "llvm-dwarfdump.h",
    )
    archive_root = "llvm-project-23.1.5.src/llvm/tools/llvm-dwarfdump"
    work = WORK
    work.mkdir(parents=True, exist_ok=True)
    cached.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llvm-dwarfdump-", dir=work) as temporary:
        source_dir = Path(temporary) / "source"
        source_dir.mkdir()
        try:
            with tarfile.open(source_archive, "r:xz") as tar:
                for name in names:
                    member = tar.getmember(f"{archive_root}/{name}")
                    if not member.isfile() or member.size > 8 * 1024 * 1024:
                        raise LaneError(f"unsafe llvm-dwarfdump source member: {member.name}")
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise LaneError(f"cannot read llvm-dwarfdump source: {member.name}")
                    with stream:
                        (source_dir / name).write_bytes(stream.read())
        except (OSError, tarfile.TarError, KeyError) as error:
            raise LaneError(f"cannot extract locked llvm-dwarfdump sources: {error}") from error

        def llvm_config_flags(*arguments: str) -> list[str]:
            result = subprocess.run(
                [str(llvm_config), *arguments],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise LaneError(f"llvm-config failed: {result.stderr.strip()}")
            return shlex.split(result.stdout)

        cxxflags = llvm_config_flags("--link-static", "--cxxflags")
        linkflags = llvm_config_flags(
            "--link-static",
            "--ldflags",
            "--libs",
            *recipe["llvm_config_components"],
            "--system-libs",
        )
        build_recipe = recipe["deterministic"]
        build_env = os.environ.copy()
        build_env.update({
            name: str(value) for name, value in build_recipe["environment"].items()
        })
        path_maps = build_recipe["path_maps"]
        prefix_maps = (
            (source_dir, path_maps["source"]),
            (Path(temporary), path_maps["build"]),
            (dev_prefix, path_maps["llvm_dev"]),
            (dev_prefix.resolve(), path_maps["llvm_dev"]),
            (toolchain.llvm_prefix, path_maps["llvm_toolchain"]),
            (toolchain.llvm_prefix.resolve(), path_maps["llvm_toolchain"]),
            (REPO, path_maps["checkout"]),
        )
        prefix_flags = [
            f"{flag}={source}={destination}"
            for source, destination in prefix_maps
            for flag in (
                "-ffile-prefix-map",
                "-fdebug-prefix-map",
                "-fmacro-prefix-map",
            )
        ]
        binary = Path(temporary) / "llvm-dwarfdump"
        unwind_library = Path("/usr/lib/libunwind.so.1")
        if not unwind_library.is_file():
            raise LaneError(f"locked llvm-libunwind runtime is missing: {unwind_library}")
        (Path(temporary) / "libunwind.so").symlink_to(unwind_library)
        command = [
            str(toolchain.llvm_prefix / "bin" / "clang++"),
            "-fuse-ld=lld",
            "-rtlib=compiler-rt",
            "-unwindlib=libunwind",
            "-Wl,--build-id=sha1",
            *cxxflags,
            *prefix_flags,
            f"-fdebug-compilation-dir={path_maps['build']}",
            "-isystem",
            "/usr/include/c++/v1",
            "-I",
            str(source_dir),
            "-L",
            str(Path(temporary)),
            "-L",
            str(toolchain.llvm_prefix / "lib"),
            *(str(source_dir / name) for name in names[:4]),
            *linkflags,
            "-o",
            str(binary),
        ]
        result = subprocess.run(
            command,
            cwd=source_dir,
            env=build_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise LaneError(
                f"cannot build locked llvm-dwarfdump:\n{result.stdout}{result.stderr}"
            )
        version = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, check=False
        )
        if version.returncode or entry["version"] not in version.stdout:
            raise LaneError(
                f"built llvm-dwarfdump reports {version.stdout.strip()!r}, "
                f"expected {entry['version']}"
            )
        actual_sha = _file_sha256(binary)
        if expected_binary_sha and actual_sha != expected_binary_sha:
            raise LaneError(
                f"built llvm-dwarfdump digest {actual_sha} (size {binary.stat().st_size}) "
                f"!= locked {expected_binary_sha} (size {expected_binary_size})"
            )
        actual_size = binary.stat().st_size
        if expected_binary_size is not None and actual_size != expected_binary_size:
            raise LaneError(
                f"built llvm-dwarfdump size {actual_size} != locked {expected_binary_size}"
            )
        with tempfile.NamedTemporaryFile(
            dir=cached.parent, prefix=f".{cached.name}-", delete=False
        ) as handle:
            temporary_cache = Path(handle.name)
        try:
            shutil.copyfile(binary, temporary_cache)
            temporary_cache.chmod(0o755)
            os.replace(temporary_cache, cached)
        finally:
            temporary_cache.unlink(missing_ok=True)
    print(f"llvm-dwarfdump cache: {cached} sha256={_file_sha256(cached)}")
    return cached


def _jit_llvm_tools(toolchain) -> Path:
    """Extract the locked release's object inspection tools for JIT builds."""
    directory = WORK / "jit-tools" / toolchain.llvm_version / "bin"
    musl_toolchain = isinstance(toolchain, lane_linux.MuslToolchain)
    names = ("llvm-objdump",) if musl_toolchain else (
        ("llvm-objdump", "llvm-dwarfdump") if IS_LINUX else
        ("llvm-objdump", "llvm-dwarfdump", "llvm-readobj")
    )
    missing = [
        name for name in names
        if not (directory / name).is_file()
    ]
    if missing:
        archive = _object_path(_llvm_cache_root(toolchain), toolchain.llvm_input())
        if not archive.is_file():
            raise LaneError(f"locked LLVM archive is missing: {archive}")
        directory.mkdir(parents=True, exist_ok=True)
        wanted = {f"{toolchain.llvm_archive_root}/bin/{name}": name for name in missing}
        found: set[str] = set()
        with tarfile.open(archive, "r|xz") as tar:
            for member in tar:
                name = wanted.get(member.name)
                if name is None:
                    continue
                if not member.isfile() or member.size > 64 * 1024 * 1024:
                    raise LaneError(f"LLVM archive member is not a bounded file: {member.name}")
                source = tar.extractfile(member)
                if source is None:
                    raise LaneError(f"cannot read LLVM archive member: {member.name}")
                with source, tempfile.TemporaryDirectory(prefix=f".{name}-", dir=directory) as scratch:
                    temporary = Path(scratch) / name
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    temporary.chmod(0o755)
                    os.replace(temporary, directory / name)
                found.add(name)
                if found == set(missing):
                    break
        missing = [name for name in missing if not (directory / name).is_file()]
        if missing:
            raise LaneError(
                f"locked LLVM archive lacks JIT tools: {', '.join(missing)}"
            )
    if musl_toolchain:
        cached_dwarfdump = _cached_musl_llvm_dwarfdump(toolchain)
        installed = directory / "llvm-dwarfdump"
        expected = json.loads(LINUX_TOOLCHAIN_LOCK.read_text())[
            "llvm_dwarfdump"
        ]["build"].get("binary_sha256")
        if not installed.is_file() or not expected or hashlib.sha256(
            installed.read_bytes()
        ).hexdigest() != expected:
            shutil.copy2(cached_dwarfdump, installed)
            installed.chmod(0o755)
        version = subprocess.run(
            [str(installed), "--version"], capture_output=True, text=True, check=False
        )
        if version.returncode or toolchain.llvm_version not in version.stdout:
            raise LaneError(
                f"staged llvm-dwarfdump reports {version.stdout.strip()!r}, "
                f"expected {toolchain.llvm_version}"
            )
    return directory


def _musl_rustc_wrapper() -> Path:
    runtime_lib_dir = _musl_runtime_link_dir()
    wrapper = WORK / "musl-rustc-wrapper"
    script = (
        "#!/bin/sh\n"
        "rustc=$1\n"
        "shift\n"
        f"runtime_lib_dir={str(runtime_lib_dir)!r}\n"
        "for arg in \"$@\"; do\n"
        "    case \"$arg\" in\n"
        "        --target|--target=*) exec \"$rustc\" -L \"native=$runtime_lib_dir\" -C link-arg=-fuse-ld=lld \"$@\" ;;\n"
        "    esac\n"
        "done\n"
        "exec \"$rustc\" -L \"native=$runtime_lib_dir\" -C target-feature=-crt-static -C link-arg=-fuse-ld=lld \"$@\"\n"
    )
    if not wrapper.is_file() or wrapper.read_text() != script:
        wrapper.write_text(script)
    wrapper.chmod(0o755)
    return wrapper


def _musl_runtime_link_dir() -> Path:
    directory = WORK / "musl-runtime-lib"
    directory.mkdir(parents=True, exist_ok=True)
    runtime = Path("/usr/lib/libgcc_s.so.1")
    if not runtime.is_file():
        raise LaneError(f"Alpine libgcc runtime is missing: {runtime}")
    alias = directory / "libgcc_s.so"
    if alias.is_symlink():
        if os.readlink(alias) != str(runtime):
            raise LaneError(f"unexpected musl runtime link: {alias} -> {os.readlink(alias)}")
    elif alias.exists():
        raise LaneError(f"musl runtime link path is occupied: {alias}")
    else:
        alias.symlink_to(runtime)
    return directory


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
        "CARGO_NET_GIT_FETCH_WITH_CLI": (
            "false" if isinstance(toolchain, lane_linux.MuslToolchain) else "true"
        ),
        "CARGO_TARGET_DIR": str(build_dir / "target"),
        "HOST_CC": str(llvm_bin / "clang"),
        "HOST_CXX": str(llvm_bin / "clang++"),
        "HOST_AR": str(llvm_bin / "llvm-ar"),
        "RANLIB": str(llvm_bin / "llvm-ranlib"),
        "RUSTUP_TOOLCHAIN": RUST_CHANNEL,
        "RUST_SHARED_BUILD": "1",
        "LLVM_TARGET": TARGET,
        "BINDGEN_EXTRA_CLANG_ARGS": f"-resource-dir={getattr(toolchain, 'bindgen_resource_dir', toolchain.llvm_resource_dir)}",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": str(WORK / "pycache"),
        "TMPDIR": str(temp_dir),
        "PKG_CONFIG": str(toolchain.pkgconf),
    })
    if IS_LINUX:
        env[_cargo_linker_variable()] = str(llvm_bin / "clang")
        rustflags = "-C link-arg=-fuse-ld=lld -C target-feature=-crt-static"
        if TARGET.endswith("-musl"):
            runtime_lib_dir = _musl_runtime_link_dir()
            rustflags = f"-L native={runtime_lib_dir} {rustflags}"
            env[_cargo_rustflags_variable()] = rustflags
            env["RUSTC_WRAPPER"] = str(_musl_rustc_wrapper())
        else:
            env[_cargo_rustflags_variable()] = rustflags
    # Regeneration needs >= 3.11; a distro may only ship an older python3 on
    # the sealed PATH. Bind the newest versioned python3.N the host's ambient
    # PATH offers when it is new enough.
    if "PYTHON_FOR_REGEN" not in env:
        regen = _regen_python(os.environ.get("PATH", ""))
        if regen:
            env["PYTHON_FOR_REGEN"] = regen
    return env


def _regen_python(path_env: str) -> str | None:
    """Newest versioned python3.N on PATH satisfying the >= 3.11 regen floor."""
    candidates: list[tuple[int, Path]] = []
    for directory in path_env.split(os.pathsep):
        for entry in Path(directory).glob("python3.*"):
            match = re.fullmatch(r"python3\.(\d+)", entry.name)
            if match and entry.is_file():
                candidates.append((int(match.group(1)), entry))
    for minor, candidate in sorted(candidates, reverse=True):
        if minor >= 11:
            return str(candidate)
    return None


def _test_environment(toolchain) -> dict[str, str]:
    """Let CPython's test runner populate its isolated bytecode cache."""
    env = _environment(toolchain, offline=True)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    if IS_LINUX and lane_linux.docker_offline():
        # The container's private /tmp gives tests room for Unix socket paths.
        env["TMPDIR"] = "/tmp"
        # Import tests rename bytecode between the cache and their temp trees.
        env["PYTHONPYCACHEPREFIX"] = "/tmp/python-build-pycache"
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


class _IndexForwarder(http.server.BaseHTTPRequestHandler):
    upstream = "https://index.crates.io/"

    def do_GET(self) -> None:
        try:
            with urllib.request.urlopen(
                self.upstream + self.path.lstrip("/"), timeout=60
            ) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as error:
            self.send_response(error.code)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception:  # noqa: BLE001 - the proxy reports every upstream failure as 502
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


@contextlib.contextmanager
def _official_index_forwarder():
    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", INDEX_FORWARDER_PORT), _IndexForwarder
        )
    except OSError as error:
        raise LaneError(
            f"cannot listen on the lane index forwarder port {INDEX_FORWARDER_PORT}: {error}"
        ) from error
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LANE_INDEX_URL
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_cargo_source_config(directory: Path) -> Path:
    config = directory / ".cargo" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.is_file() or config.read_text() != LANE_CARGO_CONFIG:
        config.write_text(LANE_CARGO_CONFIG)
    return config


def _fetch_cargo_dependencies(
    manifest: Path, toolchain, *, log: Path, extra_args: tuple[str, ...] = ()
) -> None:
    source = manifest.parent
    env = _environment(toolchain, offline=False)
    env["PATH"] = f"{CARGO_HOME / 'bin'}:{env['PATH']}"
    for variable in ("CARGO_HTTP_SSL_CAINFO", "GIT_SSL_CAINFO"):
        if variable in os.environ:
            env[variable] = os.environ[variable]
    command = [
        str(CARGO_HOME / "bin" / "cargo"), "fetch", "--locked",
        "--manifest-path", str(manifest), *LANE_CARGO_SOURCE_ARGS, *extra_args,
    ]
    cargo_lock = source / "Cargo.lock"
    before = hashlib.sha256(cargo_lock.read_bytes()).hexdigest()
    _require_command(
        command, cwd=CARGO_CONFIG_ISOLATED_CWD, env=env, log=log
    )
    after = hashlib.sha256(cargo_lock.read_bytes()).hexdigest()
    if after != before:
        raise LaneError("cargo fetch changed the locked Cargo.lock")


def _musl_allocator_metadata(name: str) -> dict[str, Any]:
    if name not in {"mimalloc", "jemalloc"}:
        raise LaneError(f"unsupported musl allocator source {name!r}")
    try:
        metadata = json.loads(LINUX_TOOLCHAIN_LOCK.read_text())["allocators"][name]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise LaneError(f"cannot read the locked {name} source input: {error}") from error
    return metadata


def allocator_profile_config(allocator: str, profile: str) -> str:
    if allocator not in {"musl", "mimalloc", "jemalloc"}:
        raise LaneError(f"unknown allocator {allocator!r}")
    if profile not in {"default", "low-retention"}:
        raise LaneError(f"unknown allocator profile {profile!r}")
    if profile == "default":
        return "--disable-initial-exec-tls" if allocator == "jemalloc" else ""
    if allocator == "mimalloc":
        return "-DMI_DEFAULT_ARENA_EAGER_COMMIT=0 -DMI_DEFAULT_PAGE_FULL_RETAIN=0"
    if allocator == "jemalloc":
        return (
            "--disable-initial-exec-tls "
            "--with-malloc-conf=dirty_decay_ms:0,muzzy_decay_ms:0,narenas:1"
        )
    raise LaneError(f"allocator profile {profile!r} requires mimalloc or jemalloc")


def allocator_cache_key(allocator: str, profile: str) -> str:
    allocator_profile_config(allocator, profile)
    return f"{allocator}-{profile}"


def verify_locked_patch(path: Path, expected_sha256: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise LaneError(f"locked allocator patch is missing or invalid: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise LaneError(f"allocator patch digest mismatch: {path}")
    return digest


def _musl_allocator_input(name: str, metadata: dict[str, Any], target: str) -> Input:
    return Input(
        name=f"{name}-allocator-source",
        version=metadata["version"],
        urls=(metadata["url"],),
        sha256=metadata["sha256"],
        size=metadata["size"],
        role="build-source",
        target=target,
        license=metadata["license"],
        purpose=f"locked {name} allocator source for the musl lane",
    )


def _cache_musl_allocator_source(name: str, toolchain, *, fetch: bool) -> Path:
    metadata = _musl_allocator_metadata(name)
    path = cache_path(REPO, metadata["cache_path"]).resolve()
    bootstrap_cache = (CACHE_ROOT / "bootstrap").resolve()
    if not path.is_relative_to(bootstrap_cache):
        raise LaneError(f"{name} source cache path escapes the repository")

    def matches(candidate: Path) -> bool:
        if not candidate.is_file() or candidate.stat().st_size != metadata["size"]:
            return False
        return hashlib.sha256(candidate.read_bytes()).hexdigest() == metadata["sha256"]

    if matches(path):
        return path
    if not fetch:
        raise LaneError(f"locked {name} source is missing or mismatched; run `perf.py fetch`")
    source_input = _musl_allocator_input(name, metadata, toolchain.target)
    blob = Cache(CACHE_ROOT / "bootstrap").fetch(source_input)
    if not matches(blob):
        raise LaneError(f"cached {name} source digest/size differs from the lane lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copyfile(blob, temporary)
        if not matches(temporary):
            raise LaneError(f"copied {name} source digest/size differs from the lane lock")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def allocator_link_flags(allocator: str, archive: Path | None = None) -> str:
    if allocator == "musl":
        return ""
    if not isinstance(allocator, str) or allocator not in {"mimalloc", "jemalloc"} \
            or archive is None:
        raise LaneError(f"invalid allocator link request for {allocator!r}")
    return shlex.join(("-Wl,--whole-archive", str(archive), "-Wl,--no-whole-archive"))


def _mimalloc_internal_symbols(nm_output: str, prefixes: tuple[str, ...]) -> list[str]:
    symbols = set()
    for line in nm_output.splitlines():
        fields = line.split()
        if fields and fields[-1].startswith(prefixes):
            symbols.add(fields[-1])
    return sorted(symbols)


def build_musl_allocator(
    allocator: str, work: Path, toolchain, *, profile: str = "default"
) -> dict[str, Any]:
    config = allocator_profile_config(allocator, profile)
    if allocator == "musl":
        return {"name": "musl", "profile": profile, "config": config,
                "link_flags": None, "artifact": None}
    if allocator not in {"mimalloc", "jemalloc"} or not isinstance(
        toolchain, lane_linux.MuslToolchain
    ):
        raise LaneError(f"allocator {allocator!r} is only supported by the musl lane")
    metadata = _musl_allocator_metadata(allocator)
    source_archive = _cache_musl_allocator_source(allocator, toolchain, fetch=False)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    extracted = safe_extract(source_archive, work / "source")
    source = extracted / metadata["archive_root"]
    if not source.is_dir():
        raise LaneError(f"locked {allocator} source root is missing: {source}")
    tool_bin = toolchain.llvm_prefix / "bin"
    environment = toolchain.env()
    environment.update({
        "CC": str(toolchain.clang),
        "CFLAGS": "-O2 -fPIC",
        "LDFLAGS": "-fuse-ld=lld",
        "PATH": os.pathsep.join((str(tool_bin), environment.get("PATH", ""))),
        "AR": str(tool_bin / "llvm-ar"),
        "RANLIB": str(tool_bin / "llvm-ranlib"),
    })
    if allocator == "mimalloc":
        patch_sha256 = None
        if profile == "low-retention":
            patch = metadata["low_retention_patch"]
            patch_path = LANE / patch["path"]
            patch_sha256 = verify_locked_patch(patch_path, patch["sha256"])
            _require_command(
                ["patch", "-p1", "-i", str(patch_path)],
                cwd=source, env=environment, log=work / "mimalloc-patch.log",
            )
        object_file = work / "mimalloc.o"
        archive = work / "libmimalloc.a"
        compile_flags = ["-O2", "-fPIC", "-DMI_MALLOC_OVERRIDE", *shlex.split(config)]
        _require_command(
            [
                str(toolchain.clang), *compile_flags,
                "-I", str(source / "include"), "-I", str(source / "src"),
                "-c", str(source / "src/static.c"), "-o", str(object_file),
            ],
            cwd=source, env=environment, log=work / "mimalloc-compile.log",
        )
        prefixes = tuple(metadata["build"]["localize_prefixes"])
        symbols = subprocess.run(
            [str(tool_bin / "llvm-nm"), "-g", "--defined-only", str(object_file)],
            cwd=source, env=environment, capture_output=True, text=True, check=False,
        )
        if symbols.returncode:
            raise LaneError(f"cannot inspect mimalloc symbols: {symbols.stderr.strip()}")
        local_symbols = _mimalloc_internal_symbols(symbols.stdout, prefixes)
        if not local_symbols:
            raise LaneError("mimalloc build has no internal symbols to localize")
        symbol_list = work / "mimalloc-local-symbols.txt"
        symbol_list.write_text("\n".join(local_symbols) + "\n")
        _require_command(
            [
                str(tool_bin / "llvm-objcopy"),
                f"--localize-symbols={symbol_list}", str(object_file),
            ],
            cwd=source, env=environment, log=work / "mimalloc-localize.log",
        )
        _require_command(
            [str(tool_bin / "llvm-ar"), "rcs", str(archive), str(object_file)],
            cwd=source, env=environment, log=work / "mimalloc-archive.log",
        )
    else:
        configure = list(metadata["build"]["configure"])
        if config:
            configure.extend(shlex.split(config))
        _require_command(
            configure,
            cwd=source, env=environment, log=work / "jemalloc-configure.log",
        )
        _require_command(
            ["/usr/bin/make", "build_lib_static"],
            cwd=source, env=environment, log=work / "jemalloc-build.log",
        )
        archive = source / metadata["build"]["archive"]
        if not archive.is_file():
            raise LaneError(f"jemalloc build did not produce {archive}")
    if not archive.is_file():
        raise LaneError(f"{allocator} build did not produce {archive}")
    return {
        "name": allocator,
        "version": metadata["version"],
        "license": metadata["license"],
        "input_url": metadata["url"],
        "input_sha256": metadata["sha256"],
        "input_size": metadata["size"],
        "profile": profile,
        "config": config,
        "patch_sha256": patch_sha256 if allocator == "mimalloc" else None,
        "artifact": str(archive),
        "include_dir": str(source / "include"),
        "artifact_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "artifact_size": archive.stat().st_size,
        "link_flags": allocator_link_flags(allocator, archive),
    }


def _fetch_musl_allocator_sources(toolchain) -> None:
    for name in ("mimalloc", "jemalloc"):
        archive = _cache_musl_allocator_source(name, toolchain, fetch=True)
        version = _musl_allocator_metadata(name)["version"]
        print(f"OK    {name} {version} -> {archive}")


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
    _serc_metadata, serc_input = _read_serc_lock()
    try:
        serc_blob = Cache(CACHE_ROOT).fetch(serc_input)
    except (InputError, OSError) as error:
        raise LaneError(f"cannot fetch the pinned Serc source: {error}") from error
    print(f"OK    Serc source archive -> {serc_blob}")
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
        for name, path in _musl_llvm_dwarfdump_inputs(toolchain, fetch=True).items():
            print(f"OK    llvm-dwarfdump {name} input -> {path}")
        _fetch_musl_allocator_sources(toolchain)
        mpdecimal = _mpdecimal_input()
        mirror = dataclasses.replace(
            mpdecimal, urls=(MPDECIMAL_MIRROR.format(version=mpdecimal.version),))
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
        _apply_serc_source(source)
        _verify_cargo_lock(source)
        with _official_index_forwarder():
            _fetch_cargo_dependencies(
                source / "Cargo.toml", toolchain, log=LOGS / "cargo-fetch.log",
                extra_args=("-Z", "build-std=std", "-Z", "build-std-features="),
            )
            _fetch_cargo_dependencies(
                source / "serc" / "Cargo.toml", toolchain, log=LOGS / "cargo-fetch-serc.log",
            )
    print(
        f"OK    Cargo dependencies cached under {CARGO_HOME}; "
        f"source pins {metadata['commit']} and Serc {serc_input.version}"
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


def _cargo_rustflags_variable() -> str:
    return f"CARGO_TARGET_{TARGET.upper().replace('-', '_')}_RUSTFLAGS"


@dataclasses.dataclass(frozen=True)
class JitConfiguration:
    enabled: bool
    argument: str
    environment: dict[str, str]
    llvm_version: str


def jit_configuration(toolchain, *, jit: bool) -> JitConfiguration:
    major = toolchain.llvm_version.split(".", 1)[0]
    return JitConfiguration(
        enabled=jit,
        argument=f"--enable-experimental-jit={'yes' if jit else 'no'}",
        environment={
            "LLVM_VERSION": major,
            "LLVM_TOOLS_INSTALL_DIR": str(toolchain.llvm_prefix),
        },
        llvm_version=toolchain.llvm_version,
    )


def _configuration(toolchain, target, *, jit: bool = False,
                   serc: bool = False) -> tuple[list[str], dict[str, str]]:
    platform_flags = _platform_flags(toolchain, target)
    jit_config = jit_configuration(toolchain, jit=jit)
    args = [
        f"--prefix={STAGE}",
        "--enable-shared",
        jit_config.argument,
        "--with-tail-call-interp=no",
        "--without-ensurepip",
    ]
    args.append("--with-pydebug")
    if target.triple.endswith("-musl"):
        args.append("--with-libs=-Wl,-l:libgcc_s.so.1 -lc")
    env = _environment(toolchain, offline=True)
    env.update(platform_flags)
    env.update(jit_config.environment)
    if serc:
        env["CFLAGS_NODIST"] = SERC_CFLAGS_NODIST
    return args, env


_JIT_PROBE = """
import json, sys
def hot(n):
    total = 0
    for i in range(n):
        total += (i * 3) % 11
        if sys._jit.is_active():
            return True
    return False
active = hot(200_000)
print(json.dumps({"available": sys._jit.is_available(),
                  "enabled": sys._jit.is_enabled(),
                  "active": active}))
"""


def _jit_stage_state(python: Path, env: dict[str, str]) -> dict[str, bool]:
    result = subprocess.run(
        [str(python), "-I", "-S", "-c", _JIT_PROBE],
        env=env, capture_output=True, text=True, timeout=300, check=False,
    )
    if result.returncode != 0:
        raise LaneError(
            "JIT activation probe failed: "
            f"{(result.stderr or result.stdout).strip()[-1000:]}"
        )
    try:
        state = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise LaneError(f"cannot parse JIT activation probe: {result.stdout!r}") from error
    if set(state) != {"available", "enabled", "active"} or not all(
            isinstance(value, bool) for value in state.values()):
        raise LaneError(f"JIT activation probe returned {state!r}")
    return state


def _require_jit_state(state: dict[str, bool], *, jit: bool) -> None:
    if jit:
        if not (state["available"] and state["enabled"]):
            raise LaneError("JIT stage does not report sys._jit available and enabled")
        if not state["active"]:
            raise LaneError(
                "JIT stage did not become active in a 200,000-iteration hot loop"
            )
    elif state["available"]:
        raise LaneError("no-JIT stage reports sys._jit.is_available()")


def _python_for_regen_version(makefile: Path, env: dict[str, str]) -> str:
    value = _make_value(makefile, "PYTHON_FOR_REGEN")
    if not value:
        raise LaneError("configured Makefile has no PYTHON_FOR_REGEN")
    result = subprocess.run(
        [*shlex.split(value), "-VV"], env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )
    if result.returncode != 0:
        raise LaneError(
            f"cannot run PYTHON_FOR_REGEN {value!r}: "
            f"{(result.stderr or result.stdout).strip()[-1000:]}"
        )
    match = re.match(r"Python (\d+)\.(\d+)\.(\d+)", result.stdout)
    if match is None:
        raise LaneError(f"cannot parse PYTHON_FOR_REGEN version: {result.stdout!r}")
    version = tuple(int(part) for part in match.groups())
    rendered = ".".join(str(part) for part in version)
    if version[:2] < (3, 11):
        raise LaneError(f"PYTHON_FOR_REGEN {rendered} is older than 3.11")
    return rendered


def _check_jit_command(makefile: Path, toolchain) -> None:
    command = ""
    for line in makefile.read_text(errors="replace").splitlines():
        if "Tools/jit/build.py" in line:
            command = line
            break
    if not command:
        raise LaneError("JIT build has no Tools/jit/build.py recipe")
    major = toolchain.llvm_version.split(".", 1)[0]

    def option(name: str) -> str:
        match = re.search(rf'--{name}="?([^"\s]+)"?', command)
        if match is None:
            raise LaneError(f"JIT command does not pass --{name}")
        return match.group(1)

    if option("llvm-version") != major:
        raise LaneError(
            f"JIT command selects LLVM {option('llvm-version')}, expected {major}"
        )
    if Path(option("llvm-tools-install-dir")) != toolchain.llvm_prefix:
        raise LaneError(
            "JIT command does not select the locked LLVM at "
            f"{toolchain.llvm_prefix}"
        )


_JIT_CLANG_PATH = re.compile(r"((?:/[A-Za-z0-9._+~-]+)+/bin/clang(?:-[0-9]+)?(?![A-Za-z0-9_+.-]))")


def stencil_clangs(log: str) -> set[str]:
    return set(_JIT_CLANG_PATH.findall(log))


def _check_jit_logs(logs: tuple[Path, ...], toolchain) -> None:
    locked = str(toolchain.llvm_prefix / "bin" / "clang")
    for log in logs:
        if not log.is_file():
            continue
        for found in stencil_clangs(log.read_text(errors="replace")):
            if os.path.normpath(found) != locked:
                raise LaneError(
                    f"{log.name} shows a stencil clang other than {locked}: {found}"
                )


def verify_jit_build(*, build_dir: Path, python: Path, env: dict[str, str],
                     toolchain, target_triple: str, jit: bool,
                     logs: tuple[Path, ...]) -> None:
    if jit:
        _python_for_regen_version(build_dir / "Makefile", env)
        _check_jit_command(build_dir / "Makefile", toolchain)
        _check_jit_logs(logs, toolchain)
        header = build_dir / f"jit_stencils-{target_triple}.h"
        if not header.is_file():
            raise LaneError(f"JIT build did not generate {header.name}")
    _require_jit_state(_jit_stage_state(python, env), jit=jit)


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
    for line in makefile.read_text(errors="replace").splitlines():
        for separator in (f"{name}?=", f"{name}="):
            if line.startswith(separator):
                return line[len(separator):].strip()
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


def _module_report(install: Path, python: Path, toolchain, *,
                   rust_base64: bool = True) -> dict[str, Any]:
    lines = ["import json, sys, sysconfig"]
    if rust_base64:
        lines += [
            "import base64, binascii, _base64",
            'vectors = [b"", b"f", b"fo", b"foo", bytes(range(256)), b"rust-cpython" * 4096]',
            "for value in vectors:",
            "    actual = _base64.standard_b64encode(value)",
            "    expected = binascii.b2a_base64(value, newline=False)",
            "    if actual != expected or actual != base64.b64encode(value):",
            '        raise SystemExit("_base64 disagrees with the CPython base64/binascii result")',
            'for value in (bytearray(b"buffer"), memoryview(b"view")):',
            "    if _base64.standard_b64encode(value) != binascii.b2a_base64(value, newline=False):",
            '        raise SystemExit("_base64 buffer-protocol result differs from binascii")',
        ]
    lines += [
        'if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 16):',
        '    raise SystemExit("interpreter is not CPython 3.16")',
        'if sysconfig.get_config_var("Py_GIL_DISABLED") in (1, "1"):',
        '    raise SystemExit("the experimental build unexpectedly disabled the GIL")',
        'if hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled():',
        '    raise SystemExit("the interpreter reports that the GIL is disabled")',
        'fields = {"implementation": sys.implementation.name,',
        '          "version": sys.version,',
        '          "gil_disabled": sysconfig.get_config_var("Py_GIL_DISABLED"),',
        '          "gil_enabled": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True,',
        '          "extension_suffix": sysconfig.get_config_var("EXT_SUFFIX"),',
        '          "config_args": sysconfig.get_config_var("CONFIG_ARGS"),',
        '          "cflags": sysconfig.get_config_var("CFLAGS"),',
        '          "ldflags": sysconfig.get_config_var("LDFLAGS")}',
    ]
    if rust_base64:
        lines += ['fields["base64_vectors"] = len(vectors) + 2',
                  'fields["base64_matches_binascii"] = True']
    lines.append("print(json.dumps(fields))")
    code = "\n".join(lines)
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
            f"installed interpreter{'/_base64' if rust_base64 else ''} smoke test failed: "
            f"{(result.stderr or result.stdout).strip()[-1000:]}"
        )
    try:
        interpreter = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise LaneError(f"cannot parse installed interpreter report: {result.stdout!r}") from error
    if IS_LINUX:
        _check_linux_interpreter(python, toolchain, install)
    if not rust_base64:
        return interpreter
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
        str(source / "Cargo.toml"), *LANE_CARGO_SOURCE_ARGS,
    ]
    result = subprocess.run(
        command, cwd=CARGO_CONFIG_ISOLATED_CWD, env=env, capture_output=True, text=True,
        timeout=120, check=False,
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
         "--format-version", "1", "--manifest-path", str(source / "Cargo.toml"),
         *LANE_CARGO_SOURCE_ARGS],
        cwd=CARGO_CONFIG_ISOLATED_CWD, env=env, capture_output=True, text=True,
        timeout=120, check=False,
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
           for argument in ("--package", engine.carrier_package)],
         *LANE_CARGO_SOURCE_ARGS],
        cwd=CARGO_CONFIG_ISOLATED_CWD, env=cargo_env, log=log, sealed=sealed,
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
                      sandbox: SealedRun, overlay: dict[str, Any], *,
                      jit: bool, serc: bool) -> dict[str, Any]:
    rustup = _require_nightly()
    _rust_tool_wrappers(rustup)
    for path in (BUILD, STAGE):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    _write_cargo_source_config(BUILD)
    args, env = _configuration(toolchain, target, jit=jit, serc=serc)
    if jit:
        env["PATH"] = f"{_jit_llvm_tools(toolchain)}:{env['PATH']}"
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
    verify_jit_build(
        build_dir=BUILD, python=python, env=env, toolchain=toolchain,
        target_triple=TARGET, jit=jit,
        logs=(LOGS / "cpython-build.log", LOGS / "cpython-install.log"),
    )
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
        "jit": jit,
        "serc": serc,
        "interpreter": module,
        "build_interpreter": str(_build_python()),
        "rust": _rust_identity(rustup),
        "c_toolchain": toolchain.identity(),
        "network_sandbox": lane_linux.describe() if IS_LINUX else None,
        "sdk": _platform_sdk_report(toolchain),
        "tests": {},
    }
    _write_json(BUILD_REPORT, report)
    return report


def _platform_sdk_report(toolchain) -> dict[str, Any]:
    if IS_LINUX:
        return {"host_packages": lane_linux.package_report(toolchain),
                "libc": ["musl", toolchain.alpine_release]}
    return {
        "path": str(toolchain.sdkroot),
        "version": sdk_version(toolchain.sdkroot),
        "xcode_version": toolchain.xcode_version,
    }


def build(*, jobs: int | None = None, jit: bool = False, serc: bool = False) -> int:
    _require_host()
    if jobs is not None and jobs < 1:
        raise LaneError("build jobs must be positive")
    if not doctor_report()["ok"]:
        raise LaneError("doctor found prerequisites missing; run doctor for details")
    toolchain, target = _toolchain()
    _llvm_ready(toolchain)
    source = _extract_fresh(SOURCE)
    overlay = _apply_overlay(source)
    if serc:
        _link_serc(source)
    _verify_cargo_lock(source)
    env = _environment(toolchain, offline=True)
    _require_command(
        [str(CARGO_HOME / "bin" / "cargo"), "fetch", "--locked", "--offline",
         "--manifest-path", str(source / "Cargo.toml"), *LANE_CARGO_SOURCE_ARGS],
        cwd=CARGO_CONFIG_ISOLATED_CWD, env=env, log=LOGS / "cargo-offline-check.log",
    )
    sandbox = _sealed_sandbox()
    workers = jobs if jobs is not None else max(1, (os.cpu_count() or 4) - 1)
    report = _configure_source(source, toolchain, target, workers, sandbox, overlay,
                               jit=jit, serc=serc)
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
    build_parser.add_argument("--jit", action="store_true",
                              help="build the experimental JIT with the locked LLVM")
    build_parser.add_argument("--serc", action="store_true",
                              help="link Serc and compile source-mode exec requests with it")
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
            return build(jobs=args.jobs, jit=args.jit, serc=args.serc)
        if args.command == "test":
            return test(args.suite, all_suites=args.all, jobs=args.jobs)
        return clean()
    except (LaneError, InputError, BootstrapError, SandboxError, OSError, ValueError) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
