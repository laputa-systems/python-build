"""Verified content-addressed acquisition for locked build inputs.

Roles (build-source, source, reference, test) separate inputs that may become
product payload from comparison-only or test-only inputs. The role is checked
structurally so excluded inputs cannot enter the product by convention alone.
Lock parsing, pinned downloads, and file digests come from laputa-buildlib.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import replace
from pathlib import Path

from buildlib.hashing import digest_file
from buildlib.locks import FetchError, Input, LockError, fetch_input, load_sources

from .wheel import object_path

ROLES = ("source", "build-source", "reference", "test")
DEFAULT_TARGET = "x86_64-unknown-linux-musl"
INPUT_MIRROR_REPOSITORY = "laputa-systems/python-build"
INPUT_MIRROR_RELEASE = "bootstrap-inputs"


def mirror_url(sha256: str) -> str:
    return (
        f"https://github.com/{INPUT_MIRROR_REPOSITORY}/releases/download/"
        f"{INPUT_MIRROR_RELEASE}/{sha256}"
    )


def input_sources(sha256: str, *upstream: str) -> tuple[str, ...]:
    return mirror_url(sha256), *upstream


class InputError(Exception):
    """Raised when locks are malformed or inputs fail verification."""


def pin_identity(input_: Input) -> str:
    """Stable digest of the pin itself (url, hash, size, role, target)."""
    material = canonical_json(
        {
            "name": input_.name,
            "version": input_.version,
            "url": input_.url,
            "sha256": input_.sha256,
            "role": input_.role,
            "target": input_.target or DEFAULT_TARGET,
            "size": input_.size,
        }
    )
    return identity(material.encode())


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def identity(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locked_inputs(path: Path) -> list[Input]:
    """The inputs of a sources lock, each with one of this repository's roles."""
    try:
        lock = load_sources(path)
    except LockError as error:
        raise InputError(str(error)) from error
    for item in lock.inputs:
        if item.role not in ROLES:
            raise InputError(f"{path}: {item.name}: unknown role {item.role!r}")
    return list(lock.inputs)


class Cache:
    """Content-addressed cache; publications are atomic and verified on every read."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _object(self, input_: Input) -> Path:
        return object_path(self.root, input_.sha256)

    def require(self, input_: Input) -> Path:
        path = self._object(input_)
        if path.is_symlink() or not path.is_file():
            raise InputError(f"{input_.name}: absent from cache ({path})")
        found, size = digest_file(path)
        if found != input_.sha256:
            raise InputError(f"{input_.name}: cache object tampered ({path})")
        if input_.size is not None and size != input_.size:
            raise InputError(
                f"{input_.name}: size mismatch (found {size}, want {input_.size})"
            )
        return path

    def store(self, path: Path, input_: Input) -> Path:
        found, size = digest_file(path)
        if found != input_.sha256:
            raise InputError(
                f"{input_.name}: downloaded bytes digest {found} != pinned {input_.sha256}"
            )
        if input_.size is not None and size != input_.size:
            raise InputError(
                f"{input_.name}: downloaded size {size} != pinned {input_.size}"
            )
        target = self._object(input_)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self.require(input_)
        except InputError:
            pass
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=".part", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copyfile(path, temporary)
            if digest_file(temporary) != (found, size):
                raise InputError(f"{input_.name}: input changed during publication")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return self.require(input_)

    def fetch(self, input_: Input, timeout: float = 120.0) -> Path:
        try:
            return self.require(input_)
        except InputError:
            pass
        mirrored = replace(input_, urls=input_sources(input_.sha256, *input_.urls))
        try:
            fetch_input(mirrored, self._object(input_), timeout=timeout)
        except FetchError as error:
            raise InputError(str(error)) from error
        return self.require(input_)


_UNSAFE = ("traversal", "absolute", "special", "link-escape")


def _check_member(member: tarfile.TarInfo, destination: Path) -> None:
    name = member.name
    if name.startswith("/") or ".." in Path(name).parts or (name and Path(name).is_absolute()):
        raise InputError(f"unsafe archive entry (traversal/absolute): {name!r}")
    if member.islnk() or member.issym():
        link = Path(member.linkname)
        if member.issym():
            target_path = (destination / name).parent / link
            if not str(target_path.resolve()).startswith(str(destination.resolve())):
                raise InputError(f"unsafe archive entry (link-escape): {name!r} -> {member.linkname!r}")
        else:
            if link.is_absolute() or ".." in link.parts:
                raise InputError(f"unsafe archive entry (link-escape): {name!r} -> {member.linkname!r}")
    if member.isdev() or member.isfifo():
        raise InputError(f"unsafe archive entry (special): {name!r}")


def safe_extract(archive: Path, destination: Path) -> Path:
    """Extract a verified tar archive, rejecting unsafe members before creating output."""
    destination = Path(destination)
    if destination.exists():
        raise InputError(f"{destination}: already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="extract-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "payload"
        with tarfile.open(archive, "r:*") as tar:
            for member in tar:
                _check_member(member, staged)
            # Second pass to extract; members were validated above.
            tar.extractall(staged, filter="data")
        os.replace(staged, destination)
    return destination
