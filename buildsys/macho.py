"""Mach-O inspection from buildlib plus the load-command edits this build needs.

Every edit here invalidates the binary's code signature. On Apple Silicon a
Mach-O without a valid signature does not launch at all, so `sign_adhoc`
is not optional cleanup: an edited-but-unsigned binary is a hard failure,
not a warning. Callers must edit and re-sign as one operation, which is why
`resign` defaults to on rather than being something to remember.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from buildlib.macho import (
    LC_BUILD_VERSION,
    LC_VERSION_MIN_MACOSX,
    PLATFORM_MACOS,
    MachOError,
    build_version,
    dependencies,
    dylib_id,
    find_machos,
    is_macho,
    load_commands,
    read_header,
    rpaths,
    signature_metadata_ranges,
    signature_status,
)

__all__ = [
    "LC_BUILD_VERSION",
    "LC_VERSION_MIN_MACOSX",
    "PLATFORM_MACOS",
    "MachOError",
    "add_rpath",
    "build_version",
    "change_dependency",
    "dependencies",
    "dylib_id",
    "find_machos",
    "is_macho",
    "load_commands",
    "read_header",
    "rpaths",
    "set_install_name",
    "sign_adhoc",
    "signature_metadata_ranges",
    "signature_status",
]


def _tool(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MachOError(
            f"{command[0]} failed on {command[-1]}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def set_install_name(path: Path, name: str, *, resign: bool = True) -> None:
    """Set a dylib's own LC_ID_DYLIB (`install_name_tool -id`)."""
    _tool(["install_name_tool", "-id", name, str(path)])
    if resign:
        sign_adhoc(path)


def change_dependency(path: Path, old: str, new: str, *, resign: bool = True) -> None:
    """Rewrite one LC_LOAD_DYLIB entry by name."""
    _tool(["install_name_tool", "-change", old, new, str(path)])
    if resign:
        sign_adhoc(path)


def add_rpath(path: Path, rpath: str, *, resign: bool = True) -> None:
    """Add an LC_RPATH search path; a no-op if already present.

    Requires the image to have been linked with `-headerpad_max_install_names`
    or otherwise have room for the extra load command; `install_name_tool`
    cannot grow the load-command region.
    """
    if rpath in rpaths(path):
        return
    _tool(["install_name_tool", "-add_rpath", rpath, str(path)])
    if resign:
        sign_adhoc(path)


def sign_adhoc(path: Path) -> None:
    """Apply a linker-style ad-hoc signature, replacing any invalidated one.

    `install_name_tool` re-signs a modified image only when its signature is
    linker-signed. uv rewrites libpython's install name after extraction, so
    a plain ad-hoc signature would leave the interpreter killed on launch.
    """
    _tool(["codesign", "--force", "--sign", "-", "--options", "linker-signed", str(path)])
