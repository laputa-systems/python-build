"""Mirror every pinned build input into the content-addressed release.

Each object is stored as a release asset named by its SHA-256, so a lock pin
stays fetchable after its upstream host drops it. The bytes are fetched and
verified against the lock before upload.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from buildsys.inputs import (
    INPUT_MIRROR_RELEASE,
    INPUT_MIRROR_REPOSITORY,
    Cache,
    Input,
    InputError,
)

MIRRORED_LOCKS = (
    "sources.lock.json",
    "bootstrap.lock.json",
    "backend/linux-bootstrap.lock.json",
    "backend/linux-bootstrap-x86_64.json",
)


@dataclass(frozen=True)
class Pin:
    url: str
    sha256: str
    size: int | None
    lock: str


def pins_in(value: object, lock: str) -> list[Pin]:
    if isinstance(value, dict):
        found = []
        if isinstance(value.get("url"), str) and isinstance(value.get("sha256"), str):
            size = value.get("size")
            found.append(Pin(value["url"], value["sha256"], size if isinstance(size, int) else None, lock))
        for item in value.values():
            found.extend(pins_in(item, lock))
        return found
    if isinstance(value, list):
        return [pin for item in value for pin in pins_in(item, lock)]
    return []


def locked_pins(root: Path) -> dict[str, Pin]:
    pins: dict[str, Pin] = {}
    for relative in MIRRORED_LOCKS:
        for pin in pins_in(json.loads((root / relative).read_text()), relative):
            previous = pins.get(pin.sha256)
            if previous is not None and previous.size != pin.size:
                raise InputError(f"{pin.sha256} is pinned with different sizes in {previous.lock} and {relative}")
            pins.setdefault(pin.sha256, pin)
    return pins


def mirrored_assets() -> set[str]:
    result = subprocess.run(
        ["gh", "release", "view", INPUT_MIRROR_RELEASE, "--repo", INPUT_MIRROR_REPOSITORY,
         "--json", "assets", "--jq", ".assets[].name"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        if "release not found" in result.stderr:
            return set()
        raise RuntimeError(f"cannot list mirror assets: {result.stderr.strip()}")
    return set(result.stdout.split())


def ensure_release() -> None:
    view = subprocess.run(
        ["gh", "release", "view", INPUT_MIRROR_RELEASE, "--repo", INPUT_MIRROR_REPOSITORY],
        capture_output=True, text=True, check=False,
    )
    if view.returncode == 0:
        return
    subprocess.run(
        ["gh", "release", "create", INPUT_MIRROR_RELEASE, "--repo", INPUT_MIRROR_REPOSITORY,
         "--prerelease", "--title", "Pinned build inputs",
         "--notes", ("Content-addressed copies of every pinned build input. "
                     "Each asset is named by the SHA-256 recorded in the repository locks; "
                     "builds verify the bytes against the lock before use.")],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report unmirrored pins without uploading")
    args = parser.parse_args(argv)
    pins = locked_pins(REPO)
    present = mirrored_assets()
    missing = sorted(sha for sha in pins if sha not in present)
    print(f"{len(pins)} pinned inputs, {len(pins) - len(missing)} mirrored, {len(missing)} missing")
    if args.check:
        for sha in missing:
            print(f"missing {sha} {pins[sha].url}")
        return 1 if missing else 0
    if not missing:
        return 0
    ensure_release()
    cache = Cache(REPO / ".cache" / "mirror")
    staging = REPO / ".work" / "tmp" / "mirror-upload"
    staging.mkdir(parents=True, exist_ok=True)
    for sha in missing:
        pin = pins[sha]
        entry = Input(
            name=Path(pin.url).name or sha, version="pinned", urls=(pin.url,),
            sha256=pin.sha256, size=pin.size, role="build-source",
        )
        path = cache.fetch(entry, timeout=600.0)
        asset = staging / sha
        shutil.copyfile(path, asset)
        try:
            subprocess.run(
                ["gh", "release", "upload", INPUT_MIRROR_RELEASE, str(asset),
                 "--repo", INPUT_MIRROR_REPOSITORY],
                check=True,
            )
        finally:
            asset.unlink(missing_ok=True)
        print(f"mirrored {sha} {pin.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
