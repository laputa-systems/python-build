"""Verified content-addressed acquisition for locked build inputs.

Roles (build-source, source, reference, test) separate inputs that may become
product payload from comparison-only or test-only inputs. The role is checked
structurally so excluded inputs cannot enter the product by convention alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROLES = ("source", "build-source", "reference", "test")
_CHUNK = 1 << 20
INPUT_MIRROR_REPOSITORY = "laputa-systems/python-build"
INPUT_MIRROR_RELEASE = "bootstrap-inputs"


def mirror_url(sha256: str) -> str:
    return (
        f"https://github.com/{INPUT_MIRROR_REPOSITORY}/releases/download/"
        f"{INPUT_MIRROR_RELEASE}/{sha256}"
    )


def input_sources(sha256: str, upstream: str) -> tuple[str, str]:
    return mirror_url(sha256), upstream


LOCK_SCHEMA_VERSION = 1


class InputError(Exception):
    """Raised when locks are malformed or inputs fail verification."""


@dataclass(frozen=True)
class Input:
    name: str
    version: str
    url: str
    sha256: str
    role: str
    target: str = "x86_64-unknown-linux-musl"
    size: int | None = None
    license: str | None = None
    purpose: str | None = None

    def __post_init__(self) -> None:
        for field in ("name", "version", "url", "sha256", "role", "target"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise InputError(f"{field}: expected nonempty string")
        if self.size is not None and (type(self.size) is not int or self.size < 0):
            raise InputError(f"{self.name}: size must be a nonnegative integer")
        for field in ("license", "purpose"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise InputError(f"{self.name}: {field} must be a nonempty string")
        if self.role not in ROLES:
            raise InputError(f"{self.name}: unknown role {self.role!r}")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise InputError(f"{self.name}: sha256 must be lowercase hex digest")

    def identity(self) -> str:
        """Stable digest of the pin itself (url, hash, size, role, target)."""
        material = canonical_json(
            {
                "name": self.name,
                "version": self.version,
                "url": self.url,
                "sha256": self.sha256,
                "role": self.role,
                "target": self.target,
                "size": self.size,
            }
        )
        return identity(material.encode())


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def identity(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_lock(path: Path) -> list[Input]:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise InputError(f"cannot read lock {path}: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("inputs"), list):
        raise InputError(f"{path}: expected top-level object with 'inputs' list")
    if document.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise InputError(f"{path}: unsupported lock schema_version {document.get('schema_version')!r}")
    inputs: list[Input] = []
    seen: set[str] = set()
    for index, entry in enumerate(document["inputs"]):
        if not isinstance(entry, dict):
            raise InputError(f"{path}: input #{index} is not an object")
        try:
            item = Input(
                name=entry["name"],
                version=entry["version"],
                url=entry["url"],
                sha256=entry["sha256"],
                role=entry["role"],
                target=entry.get("target", "x86_64-unknown-linux-musl"),
                size=entry.get("size"),
                license=entry.get("license"),
                purpose=entry.get("purpose"),
            )
        except KeyError as error:
            raise InputError(f"{path}: input #{index} missing {error}") from error
        if item.name in seen:
            raise InputError(f"{path}: duplicate input {item.name}")
        seen.add(item.name)
        inputs.append(item)
    return inputs


def _digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class Cache:
    """Content-addressed cache; publications are atomic and verified on every read."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _object(self, input_: Input) -> Path:
        return self.root / "objects" / f"{input_.sha256}.blob"

    def require(self, input_: Input) -> Path:
        path = self._object(input_)
        if path.is_symlink() or not path.is_file():
            raise InputError(f"{input_.name}: absent from cache ({path})")
        found, size = _digest(path)
        if found != input_.sha256:
            raise InputError(f"{input_.name}: cache object tampered ({path})")
        if input_.size is not None and size != input_.size:
            raise InputError(
                f"{input_.name}: size mismatch (found {size}, want {input_.size})"
            )
        return path

    def store(self, path: Path, input_: Input) -> Path:
        found, size = _digest(path)
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
            if _digest(temporary) != (found, size):
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
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.root, suffix=".download", delete=False
        ) as handle:
            staged = Path(handle.name)
        failures = []
        try:
            for url in input_sources(input_.sha256, input_.url):
                request = urllib.request.Request(url, headers={"User-Agent": "python-build-m1/1.0"})
                try:
                    with urllib.request.urlopen(request, timeout=timeout) as response, staged.open("wb") as out:
                        shutil.copyfileobj(response, out, _CHUNK)
                except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                    failures.append(f"{url}: {error}")
                    continue
                return self.store(staged, input_)
            raise InputError(f"{input_.name}: no source served the pinned bytes: " + "; ".join(failures))
        finally:
            staged.unlink(missing_ok=True)


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
