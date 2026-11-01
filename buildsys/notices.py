"""Install license notices and the source-acquisition record for bundled inputs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .deporder import dependency_order
from .inputs import Cache, Input, InputError, load_lock, safe_extract
from .targets import Target

LICENSE_DIRECTORY = Path("share/licenses/python-build")
NATIVE_DIRECTORY = LICENSE_DIRECTORY / "native"
SOURCES_RECORD = LICENSE_DIRECTORY / "SOURCES.md"
REPOSITORY_URL = "https://github.com/joshuarli/python-build"
RTLS_URL = "https://github.com/jawah/rtls"

NOT_SHIPPED_INPUTS = frozenset({"pkgconf", "openssl"})
HEADER_NOTICE_SUFFIX = ":header"

# Archive-relative notice files per bundled input. A ":header" suffix copies
# only the leading comment block, for licenses that exist only in a source
# file header. SQLite is a public-domain dedication and has no notice file.
NATIVE_NOTICES: dict[str, tuple[str, ...]] = {
    "zlib": ("LICENSE",),
    "bzip2": ("LICENSE",),
    "xz": ("COPYING", "COPYING.0BSD"),
    "zstd": ("LICENSE",),
    "expat": ("COPYING",),
    "mpdecimal": ("COPYRIGHT.txt",),
    "libffi": ("LICENSE",),
    "ncurses": ("COPYING",),
    "libedit": ("COPYING",),
    "libuuid": ("COPYING", "gen_uuid.c" + HEADER_NOTICE_SUFFIX),
    "bdb": ("LICENSE",),
    "sqlite": (),
}

SOURCE_OFFERS: dict[str, str] = {
    "bdb": (
        "Berkeley DB is statically linked into the `_dbm` extension "
        "module (`lib-dynload/_dbm*.so`) on Linux and nowhere else; macOS "
        "uses the platform ndbm. Its license requires that redistributions "
        "come with information on how to obtain the complete source of "
        "Berkeley DB and of the software that uses it. Both are free to "
        "download: the exact Berkeley DB archive is listed in the table above, "
        "and the CPython archive, its patches, and the build recipes are "
        "listed in the sections below."
    ),
}

CPYTHON_THIRD_PARTY_RECORD = "Doc/license.rst"


class NoticeError(Exception):
    """Bundled-input notices could not be installed or verified."""


def bundled_native_names(target: Target) -> tuple[str, ...]:
    return tuple(
        name for name in dependency_order(target) if name not in NOT_SHIPPED_INPUTS
    )


def expected_notice_paths(target: Target, locked: dict[str, Input]) -> list[Path]:
    paths = [LICENSE_DIRECTORY / _cpython_record_name(locked["cpython"]), SOURCES_RECORD]
    for name in bundled_native_names(target):
        for source in NATIVE_NOTICES[name]:
            paths.append(_notice_destination(locked[name], source))
    return paths


def install_notices(
    install: Path,
    *,
    target: Target,
    cache_root: Path,
    lock_path: Path,
    source_revision: str,
    rtls_revision: str,
    rtls_source_sha256: str,
) -> dict[str, object]:
    locked = {entry.name: entry for entry in load_lock(lock_path)}
    names = bundled_native_names(target)
    missing = [name for name in ("cpython", *names) if name not in locked]
    unknown = [name for name in names if name not in NATIVE_NOTICES]
    if missing or unknown:
        raise NoticeError(
            f"notice policy is incomplete: missing lock entries {missing}, "
            f"no notice rule for {unknown}"
        )

    install = Path(install)
    cache = Cache(cache_root)
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="notices-") as temporary:
        scratch = Path(temporary)
        for name in ("cpython", *names):
            entry = locked[name]
            try:
                extracted = safe_extract(cache.require(entry), scratch / name)
            except InputError as error:
                raise NoticeError(f"{name}: locked source is unavailable: {error}") from error
            root = _single_root(name, extracted)
            if name == "cpython":
                _copy_notice(root / CPYTHON_THIRD_PARTY_RECORD, install,
                             LICENSE_DIRECTORY / _cpython_record_name(entry), written)
                continue
            for source in NATIVE_NOTICES[name]:
                _copy_notice(root / source.removesuffix(HEADER_NOTICE_SUFFIX), install,
                             _notice_destination(entry, source), written,
                             header_only=source.endswith(HEADER_NOTICE_SUFFIX))
    record = install / SOURCES_RECORD
    _refuse_existing(record)
    record.write_text(_sources_record(
        locked, names, source_revision, rtls_revision, rtls_source_sha256
    ), encoding="utf-8")
    record.chmod(0o644)
    written.append(SOURCES_RECORD)
    return {
        "components": list(names),
        "files": sorted(path.as_posix() for path in written),
    }


def verify_notices(install: Path, target: Target, lock_path: Path) -> dict[str, object]:
    locked = {entry.name: entry for entry in load_lock(lock_path)}
    install = Path(install)
    missing = []
    for relative in expected_notice_paths(target, locked):
        path = install / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            missing.append(relative.as_posix())
    record = install / SOURCES_RECORD
    text = record.read_text(encoding="utf-8") if record.is_file() else ""
    unlisted = [
        name
        for name in ("cpython", *bundled_native_names(target))
        if locked[name].sha256 not in text
    ]
    return {
        "ok": not missing and not unlisted,
        "missing_notices": missing,
        "components_missing_from_sources_record": unlisted,
    }


def _cpython_record_name(entry: Input) -> str:
    return f"cpython-{entry.version}-third-party-licenses.rst"


def _notice_destination(entry: Input, source: str) -> Path:
    name = Path(source.removesuffix(HEADER_NOTICE_SUFFIX)).name
    return NATIVE_DIRECTORY / f"{entry.name}-{entry.version}" / name


def _single_root(name: str, extracted: Path) -> Path:
    children = [child for child in extracted.iterdir()]
    if len(children) != 1 or not children[0].is_dir():
        raise NoticeError(f"{name}: source archive must have exactly one top-level directory")
    return children[0]


def _refuse_existing(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise NoticeError(f"refusing to overwrite existing notice: {path}")


def _copy_notice(
    source: Path,
    install: Path,
    relative: Path,
    written: list[Path],
    *,
    header_only: bool = False,
) -> None:
    if source.is_symlink() or not source.is_file():
        raise NoticeError(f"locked source has no notice file {source.name}")
    destination = install / relative
    _refuse_existing(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if header_only:
        text = source.read_text(encoding="utf-8", errors="strict")
        end = text.find("*/")
        if end < 0:
            raise NoticeError(f"{source.name}: no leading comment block to copy")
        destination.write_text(text[: end + 2] + "\n", encoding="utf-8")
    else:
        shutil.copyfile(source, destination)
    destination.chmod(0o644)
    written.append(relative)


def _sources_record(
    locked: dict[str, Input],
    names: tuple[str, ...],
    source_revision: str,
    rtls_revision: str,
    rtls_source_sha256: str,
) -> str:
    lines = [
        "# Bundled component sources",
        "",
        (
            "Every component below is built from the exact archive named here. "
            "Download the URL and check the SHA-256 to obtain the source "
            "that went into this distribution. License texts are in "
            "`native/`; CPython's own license is `lib/python*/LICENSE.txt` and its "
            "third-party acknowledgements are in the `cpython-*-third-party-licenses.rst` "
            "file next to this record. Rust dependencies are recorded in `rust/`."
        ),
        "",
        "| Component | Version | License | Source archive | SHA-256 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in ("cpython", *names):
        entry = locked[name]
        lines.append(
            f"| {entry.name} | {entry.version} | {entry.license} | "
            f"<{entry.url}> | `{entry.sha256}` |"
        )
    offers = [(name, SOURCE_OFFERS[name]) for name in names if name in SOURCE_OFFERS]
    if offers:
        lines.extend(("", "## Source offers", ""))
        for name, text in offers:
            lines.extend((f"### {name} {locked[name].version}", "", text, ""))
    lines.extend((
        "",
        "## Patches and build recipes",
        "",
        (
            f"This distribution was built from revision `{source_revision}` of "
            f"<{REPOSITORY_URL}>. At that revision, `patches/cpython/` and "
            "`backend/cpython-linux-patches/` hold every patch applied to CPython, "
            "`backend/rtls-graviola.patch` and the other `backend/*.patch` files "
            "hold the patches applied to the Rust TLS sources, and `build/`, "
            "`buildsys/`, and `backend/` hold the build recipes. Sources are "
            "available at no charge."
        ),
        "",
        "## Rust components",
        "",
        (
            f"TLS runs on the vendored RTLS source <{RTLS_URL}> at revision "
            f"`{rtls_revision}` (source tree SHA-256 `{rtls_source_sha256}`), with "
            "the patches named above. The hashing backend source is "
            "`backend/hashing/` in the repository revision above. Crates come from "
            "crates.io at the versions and checksums in the RTLS "
            "`rust/Cargo.lock` (as patched) and `backend/hashing/Cargo.lock`; "
            "`rust/THIRD_PARTY_LICENSES.md` lists every active package with its "
            "source identity and notices."
        ),
        "",
    ))
    return "\n".join(lines)
