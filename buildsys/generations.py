"""Crash-consistent publication of a set of files as one immutable generation.

A target directory holds `generations/<id>/` directories, each sealed by a
`GENERATION.json` listing every file's size and SHA-256, and one `ACTIVE`
file naming the generation readers must use. A generation is built in a
staging directory and renamed into place; `ACTIVE` is then replaced with a
single atomic rename. At every instant, and after a kill at any step,
`active_generation` returns one complete, verified generation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from buildlib.hashing import digest_file

GENERATIONS = "generations"
ACTIVE = "ACTIVE"
RECORD = "GENERATION.json"
LOCK = ".publish.lock"
STAGING_PREFIX = ".staging-"
PENDING_PREFIX = ".active-"
_GENERATION_ID = re.compile(r"[0-9a-f]{16}")
_CHUNK = 1 << 20


class GenerationError(Exception):
    """A generation could not be published or does not verify."""


def publish_generation(target_directory: Path, files: Mapping[str, Path]) -> str:
    """Publish `files` (name -> source path) as the new active generation."""
    names = _validated_names(files)
    target = Path(target_directory)
    for name in names:
        source = Path(files[name])
        if source.is_symlink() or not source.is_file():
            raise GenerationError(f"{name}: source is not a regular file: {source}")
    generations = target / GENERATIONS
    generations.mkdir(parents=True, exist_ok=True)
    with (target / LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = _active_id_or_none(target)
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=generations))
        listing = {}
        for name in names:
            listing[name] = _copy_synced(Path(files[name]), staging / name)
        record = (json.dumps({"files": listing}, indent=2, sort_keys=True) + "\n").encode()
        _write_synced(staging / RECORD, record)
        _sync_directory(staging)
        generation = hashlib.sha256(record).hexdigest()[:16]
        final = generations / generation
        if final.exists():
            shutil.rmtree(staging)
        else:
            os.rename(staging, final)
        _sync_directory(generations)
        _select(target, generation)
        _prune(target, keep={generation, previous})
    return generation


def active_generation(target_directory: Path) -> Path:
    """Return the verified directory of the generation named by `ACTIVE`."""
    target = Path(target_directory)
    generation = _active_id_or_none(target)
    if generation is None:
        raise GenerationError(f"{target}: no active generation")
    directory = target / GENERATIONS / generation
    try:
        record = json.loads((directory / RECORD).read_text())
        listing = record["files"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise GenerationError(f"{directory}: generation record is unreadable: {error}") from error
    if not isinstance(listing, dict) or not listing:
        raise GenerationError(f"{directory}: generation record lists no files")
    present = {path.name for path in directory.iterdir()}
    if present != {*listing, RECORD}:
        raise GenerationError(f"{directory}: generation files differ from its record")
    for name, facts in listing.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise GenerationError(f"{directory}: {name} is not a regular file")
        digest, size = digest_file(path)
        if not isinstance(facts, dict) or (digest, size) != (
            facts.get("sha256"), facts.get("size_bytes")
        ):
            raise GenerationError(f"{directory}: {name} does not match its record")
    return directory


def _validated_names(files: Mapping[str, Path]) -> list[str]:
    if not files:
        raise GenerationError("a generation needs at least one file")
    for name in files:
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name.startswith(".")
            or name in {ACTIVE, RECORD}
        ):
            raise GenerationError(f"invalid generation file name {name!r}")
    return sorted(files)


def _active_id_or_none(target: Path) -> str | None:
    try:
        text = (target / ACTIVE).read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise GenerationError(f"{target}: cannot read {ACTIVE}: {error}") from error
    generation = text.strip()
    if _GENERATION_ID.fullmatch(generation) is None:
        raise GenerationError(f"{target}: {ACTIVE} names an invalid generation {text!r}")
    return generation


def _select(target: Path, generation: str) -> None:
    descriptor, name = tempfile.mkstemp(prefix=PENDING_PREFIX, dir=target)
    os.close(descriptor)
    pending = Path(name)
    _write_synced(pending, (generation + "\n").encode())
    os.replace(pending, target / ACTIVE)
    _sync_directory(target)


def _prune(target: Path, keep: set[str | None]) -> None:
    for entry in (target / GENERATIONS).iterdir():
        if entry.name not in keep:
            shutil.rmtree(entry)
    for entry in target.glob(PENDING_PREFIX + "*"):
        entry.unlink()


def _copy_synced(source: Path, destination: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        for chunk in iter(lambda: reader.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def _write_synced(path: Path, content: bytes) -> None:
    with path.open("wb") as writer:
        writer.write(content)
        writer.flush()
        os.fsync(writer.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
