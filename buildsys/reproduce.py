"""Compare two clean-build install trees for internal reproducibility.

Sharing immutable downloaded inputs is fine; this compares *build outputs*
from two independent, cache-busted sealed builds, so any prior-object reuse
would show up as a suspiciously-easy match rather than real evidence.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from buildlib.hashing import sha256_file


@dataclass(frozen=True)
class _TreeEntry:
    kind: int
    mode: int
    content_digest: str | None = None
    symlink_target: str | None = None


def _relative_entries(root: Path) -> dict[str, _TreeEntry]:
    entries: dict[str, _TreeEntry] = {}
    for path in root.rglob("*"):
        info = path.lstat()
        kind = stat.S_IFMT(info.st_mode)
        entries[str(path.relative_to(root))] = _TreeEntry(
            kind=kind,
            mode=stat.S_IMODE(info.st_mode),
            content_digest=sha256_file(path) if stat.S_ISREG(info.st_mode) else None,
            symlink_target=os.readlink(path) if stat.S_ISLNK(info.st_mode) else None,
        )
    return entries


def macho_difference(first: Path, second: Path) -> dict:
    """Split a binary difference into signing metadata and everything else.

    "These two builds differ" and "these two builds differ only in their code
    signatures" are different claims, and a raw hash comparison cannot tell
    them apart. On Apple Silicon every Mach-O carries an ad-hoc signature that
    covers the whole image, so any difference at all — including the
    per-link LC_UUID — propagates into the signature bytes and makes a naive
    comparison look far worse than the underlying change.
    """
    from . import macho

    a, b = Path(first).read_bytes(), Path(second).read_bytes()
    if len(a) != len(b):
        return {"size_delta": len(a) - len(b), "comparable": False}
    metadata = set()
    for path in (first, second):
        for offset, size in macho.signature_metadata_ranges(path):
            metadata.update(range(offset, min(offset + size, len(a))))
    differing = [i for i in range(len(a)) if a[i] != b[i]]
    in_metadata = [i for i in differing if i in metadata]
    residual = [i for i in differing if i not in metadata]
    return {
        "comparable": True,
        "differing_bytes": len(differing),
        "signature_metadata_bytes": len(in_metadata),
        # Bytes outside the UUID and the signature: the compiled content.
        "compiled_content_bytes": len(residual),
        "compiled_content_identical": not residual,
        "first_residual_offsets": residual[:8],
    }


def compare_trees(first: Path, second: Path) -> dict:
    """Report entry identity differences between two install trees."""
    a, b = _relative_entries(first), _relative_entries(second)
    only_in_first = sorted(a.keys() - b.keys())
    only_in_second = sorted(b.keys() - a.keys())
    shared = a.keys() & b.keys()
    entry_type_differs = sorted(name for name in shared if a[name].kind != b[name].kind)
    mode_differs = sorted(name for name in shared if a[name].mode != b[name].mode)
    regular_files = {
        name for name in shared
        if a[name].kind == stat.S_IFREG and b[name].kind == stat.S_IFREG
    }
    content_differs = sorted(
        name for name in regular_files
        if a[name].content_digest != b[name].content_digest
    )
    symlink_target_differs = sorted(
        name for name in shared
        if a[name].kind == stat.S_IFLNK
        and b[name].kind == stat.S_IFLNK
        and a[name].symlink_target != b[name].symlink_target
    )
    identical = sum(
        a[name].content_digest == b[name].content_digest
        and a[name].mode == b[name].mode
        for name in regular_files
    )
    return {
        "files_compared": len(regular_files),
        "only_in_first": only_in_first,
        "only_in_second": only_in_second,
        "entry_type_differs": entry_type_differs,
        "mode_differs": mode_differs,
        "content_differs": content_differs,
        "symlink_target_differs": symlink_target_differs,
        "byte_identical": not any((
            only_in_first,
            only_in_second,
            entry_type_differs,
            mode_differs,
            content_differs,
            symlink_target_differs,
        )),
        "identical_file_count": identical,
    }


def compare_archives(first: Path, second: Path, scratch: Path) -> dict:
    """Compare two release archives built independently from the same inputs.

    Reproducible means: identical entry sets, types, modes, and symlink
    targets, and every content difference is a Mach-O image whose bytes differ
    only in the LC_UUID and ad-hoc signature. A differing ELF or text file is
    a finding, never excused.
    """
    from . import macho
    from .package import extract_package_archive

    first, second, scratch = Path(first), Path(second), Path(scratch)
    trees = [extract_package_archive(archive, scratch / name)
             for name, archive in (("first", first), ("second", second))]
    result = compare_trees(*trees)
    signature_only = {}
    findings = []
    for relative in result["content_differs"]:
        a, b = trees[0] / relative, trees[1] / relative
        if macho.is_macho(a) and macho.is_macho(b):
            difference = macho_difference(a, b)
            if difference.get("compiled_content_identical"):
                signature_only[relative] = difference["signature_metadata_bytes"]
                continue
        findings.append(relative)
    structural = [
        result[key] for key in (
            "only_in_first", "only_in_second", "entry_type_differs",
            "mode_differs", "symlink_target_differs",
        )
    ]
    return {
        "archives": {
            "first": {"sha256": sha256_file(first), "size_bytes": first.stat().st_size},
            "second": {"sha256": sha256_file(second), "size_bytes": second.stat().st_size},
        },
        "files_compared": result["files_compared"],
        "raw_archives_identical": sha256_file(first) == sha256_file(second),
        "structural_differences": {
            key: result[key] for key in (
                "only_in_first", "only_in_second", "entry_type_differs",
                "mode_differs", "symlink_target_differs",
            ) if result[key]
        },
        "signature_only_images": sorted(signature_only),
        "content_differences": findings,
        "reproducible": not any(structural) and not findings,
    }
