from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from buildsys.inputs import safe_extract
from buildsys.patches import apply_patch

VERSION = "0.103.17"
SHA256 = "e3a0760a887c14823fca5a1c970b01d47c328c696c0c64829d1df50e8f853fd5"
URL = f"https://static.crates.io/crates/rustls-webpki/rustls-webpki-{VERSION}.crate"


def verify(archive: Path) -> None:
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != SHA256:
        raise ValueError(f"rustls-webpki {VERSION} archive checksum mismatch")


def fetch(archive: Path) -> None:
    if archive.exists():
        verify(archive)
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        staged = Path(temporary) / "webpki.crate"
        with urllib.request.urlopen(URL, timeout=60) as response, staged.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        verify(staged)
        os.replace(staged, archive)


def prepare(archive: Path, checkout: Path) -> Path:
    verify(archive)
    destination = checkout / "webpki"
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(dir=checkout.parent) as temporary:
        extracted = safe_extract(archive, Path(temporary) / "source")
        source = extracted / f"rustls-webpki-{VERSION}"
        apply_patch(source, Path(__file__).with_name("webpki-policy.patch"))
        apply_patch(
            source,
            Path(__file__).with_name("webpki-crl-policy.patch"),
            strip=2,
        )
        apply_patch(source, Path(__file__).with_name("webpki-strict-policy.patch"))
        os.replace(source, destination)
    return destination


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
        fetch(args.archive.resolve())
    else:
        prepare(args.archive.resolve(), args.checkout.resolve())


if __name__ == "__main__":
    main()
