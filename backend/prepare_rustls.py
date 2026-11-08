from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

RUSTLS_VERSION = "0.23.46"
RUSTLS_ARCHIVE_SHA256 = "48e13bd8c0e9365c43cfa5c9e8f9ad49d3c8444926c9aac819e0e4dc503c8fdf"
RUSTLS_ARCHIVE_URL = f"https://static.crates.io/crates/rustls/rustls-{RUSTLS_VERSION}.crate"


def cargo_lock_version(checkout: Path) -> str:
    lock = (checkout / "rust" / "Cargo.lock").read_text()
    packages = []
    for block in re.split(r"(?m)^\[\[package\]\]\s*$", lock)[1:]:
        name = re.search(r'(?m)^name = "([^"]+)"$', block)
        version = re.search(r'(?m)^version = "([^"]+)"$', block)
        if name and version and name.group(1) == "rustls":
            packages.append(version.group(1))
    if len(packages) != 1:
        raise RuntimeError("expected exactly one locked rustls package")
    return packages[0]


def verify_archive(archive: Path) -> None:
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != RUSTLS_ARCHIVE_SHA256:
        raise RuntimeError(f"rustls {RUSTLS_VERSION} archive checksum mismatch")


def fetch_archive(archive: Path) -> None:
    if archive.exists():
        verify_archive(archive)
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        staged = Path(temporary) / f"rustls-{RUSTLS_VERSION}.crate"
        with (
            urllib.request.urlopen(RUSTLS_ARCHIVE_URL, timeout=60) as response,
            staged.open("wb") as output,
        ):
            while block := response.read(1024 * 1024):
                output.write(block)
        verify_archive(staged)
        os.replace(staged, archive)


def extract_archive(archive_path: Path, target: Path, version: str) -> None:
    if target.exists():
        raise RuntimeError(f"rustls target already exists: {target}")
    prefix = f"rustls-{version}/"
    target.mkdir(parents=True)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.startswith(prefix):
                    raise RuntimeError("rustls archive contains an unsupported member")
                relative = PurePosixPath(member.name[len(prefix) :])
                if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("rustls archive contains an unsafe path")
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError("rustls archive member could not be read")
                with source, destination.open("xb") as output:
                    output.write(source.read())
                destination.chmod(member.mode & 0o755)
    except BaseException:
        for path in sorted(target.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        target.rmdir()
        raise


def prepare(archive: Path, checkout: Path) -> None:
    checkout = checkout.resolve()
    archive = archive.resolve()
    if cargo_lock_version(checkout) != RUSTLS_VERSION:
        raise RuntimeError(f"expected rustls {RUSTLS_VERSION}")
    verify_archive(archive)
    target = checkout / "rustls"
    if target.exists():
        raise RuntimeError(f"rustls target already exists: {target}")
    repo_root = Path(__file__).resolve().parents[1]
    patch = repo_root / "backend" / "rustls-session-api.patch"
    with tempfile.TemporaryDirectory(dir=checkout, prefix=".rustls-source-") as temporary:
        source = Path(temporary) / "rustls"
        extract_archive(archive, source, RUSTLS_VERSION)
        patch_environment = os.environ.copy()
        if patch_environment.get("TMPDIR"):
            patch_environment["TMPDIR"] = str(Path(patch_environment["TMPDIR"]).resolve())
        result = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1"],
            cwd=source,
            input=patch.read_bytes(),
            env=patch_environment,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("failed to apply the rustls session API patch")
        manifest = (source / "Cargo.toml").read_text()
        package = re.search(r"(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)", manifest)
        version = re.search(r'(?m)^version = "([^"]+)"$', package.group(1)) if package else None
        if version is None or version.group(1) != RUSTLS_VERSION:
            raise RuntimeError("extracted rustls version does not match the lock")
        os.replace(source, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    download = subcommands.add_parser("fetch")
    download.add_argument("archive", type=Path)
    unpack = subcommands.add_parser("prepare")
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("checkout", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch_archive(args.archive.resolve())
    else:
        prepare(args.archive, args.checkout)


if __name__ == "__main__":
    main()
