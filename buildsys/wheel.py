"""Make laputa-buildlib importable from the wheel pinned in sources.lock.json.

buildsys runs under plain interpreters, in containers, and in offline sealed
builds where no project environment exists, so the pinned wheel itself is put
on sys.path (a pure wheel is a zip archive). It lives in the content-addressed
input cache like every other locked input.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT_NAME = "laputa-buildlib"
VIEW_RELATIVE_PATH = Path(".cache") / "bootstrap" / "buildlib"
_CHUNK = 1 << 20


def object_path(cache_root: Path, sha256: str) -> Path:
    return Path(cache_root) / "objects" / f"{sha256}.blob"


def _matches(path: Path, sha256: str, size: int) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest() == sha256 and total == size


def _download(url: str, destination: Path, sha256: str, size: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=destination.parent, suffix=".part")
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(url, timeout=120) as response:
            while chunk := response.read(_CHUNK):
                output.write(chunk)
        if not _matches(staged, sha256, size):
            raise ImportError(f"{INPUT_NAME}: {url} did not serve the pinned bytes")
        staged.chmod(0o644)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def ensure() -> None:
    if importlib.util.find_spec("buildlib") is not None:
        return
    document = json.loads((ROOT / "sources.lock.json").read_text())
    pin = next(entry for entry in document["inputs"] if entry["name"] == INPUT_NAME)
    wheel = object_path(ROOT / ".cache", pin["sha256"])
    if not _matches(wheel, pin["sha256"], pin["size"]):
        _download(pin["url"], wheel, pin["sha256"], pin["size"])
    sys.path.append(str(wheel))
