"""Resolve cache and primary-checkout paths for the Rust CPython lane."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def checkout_root(repo: Path) -> Path:
    return Path(repo).resolve()


def cache_root(repo: Path, override: Path | None = None) -> Path:
    return Path(override) if override is not None else checkout_root(repo) / ".cache"


def cache_path(repo: Path, relative: str | Path) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else checkout_root(repo) / path


def git_common_dir(repo: Path) -> Path:
    checkout = checkout_root(repo)
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={checkout}",
            "-C",
            str(checkout),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
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


def share_worktree_caches(
    repo: Path, primary: Path | None = None
) -> tuple[Path, list[tuple[Path, Path]]]:
    checkout = checkout_root(repo)
    owner = checkout_root(primary) if primary is not None else primary_checkout(checkout)
    if owner == checkout:
        return owner, []

    primary_cache = cache_root(owner)
    worktree_cache = cache_root(checkout)
    links = [
        (primary_cache / "objects", worktree_cache / "objects"),
        (primary_cache / "bootstrap", worktree_cache / "bootstrap"),
    ]
    wheelhouse = owner / "benchmarks" / ".cache" / "wheelhouse"
    if wheelhouse.is_dir():
        links.append((wheelhouse, checkout / "benchmarks" / ".cache" / "wheelhouse"))
    llvm_objects = primary_cache / "llvm" / "objects"
    if llvm_objects.is_dir():
        links.append((llvm_objects, worktree_cache / "llvm" / "objects"))

    linked: list[tuple[Path, Path]] = []
    for source, target in links:
        if not source.is_dir():
            raise RuntimeError(f"primary checkout lacks {source}; run fetch there first")
        source = source.resolve()
        if target.is_symlink():
            if target.resolve() != source:
                raise RuntimeError(f"worktree cache link points elsewhere: {target}")
        elif target.exists():
            raise RuntimeError(f"worktree cache path is not shared: {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        linked.append((target, source))
    return owner, linked


def mount_root(repo: Path) -> Path:
    checkout = checkout_root(repo)
    roots = [checkout, primary_checkout(checkout)]
    cache = cache_root(checkout)
    if cache.is_symlink():
        roots.append(cache.resolve())
    else:
        for directory in (cache, cache / "llvm"):
            if directory.is_dir():
                roots.extend(
                    path.resolve() for path in directory.iterdir() if path.is_symlink()
                )
    return Path(os.path.commonpath([str(path) for path in roots]))
