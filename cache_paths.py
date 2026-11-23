"""Resolve checkout-local and shared bootstrap cache paths."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git_common_dir(repo: Path) -> Path:
    checkout = Path(repo).resolve()
    result = subprocess.run(
        ["git", "-c", f"safe.directory={checkout}", "-C", str(checkout),
         "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"cannot resolve git common directory: {result.stderr.strip()}")
    return Path(result.stdout.strip()).resolve()


def primary_checkout(repo: Path) -> Path:
    common = git_common_dir(repo)
    if common.name != ".git":
        raise RuntimeError(f"unexpected git common directory: {common}")
    return common.parent


def local_cache_root(repo: Path, override: Path | None = None) -> Path:
    return Path(override) if override is not None else Path(repo).resolve() / ".cache"


def shared_cache_root(repo: Path) -> Path:
    return primary_checkout(repo) / ".cache"


def shared_path(repo: Path, relative: str | Path) -> Path:
    return primary_checkout(repo) / relative


def mount_root(repo: Path) -> Path:
    return Path(os.path.commonpath((Path(repo).resolve(), primary_checkout(repo))))
