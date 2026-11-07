"""Re-resolve the pinned Alpine APK closures for both Linux architectures.

The locked base image of each architecture is exported without running it, and
the native x86_64 apk installs each layer's direct packages into that root with
scripts disabled, so foreign-architecture closures resolve on one host. The
closure is the difference between the installed databases before and after a
layer, which is exactly what the image builds assert.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64"}
BACKEND_LOCKS = {
    "aarch64": REPO / "backend" / "linux-bootstrap.lock.json",
    "x86_64": REPO / "backend" / "linux-bootstrap-x86_64.json",
}
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Installed:
    name: str
    version: str
    arch: str
    license: str


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def export_rootfs(platform: str, digest: str, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    container = run(
        ["docker", "create", "--platform", platform, f"alpine@{digest}", "/bin/true"],
        capture_output=True,
    ).stdout.strip()
    try:
        exported = subprocess.Popen(["docker", "export", container], stdout=subprocess.PIPE)
        run(["tar", "-x", "-C", str(destination)], stdin=exported.stdout)
        if exported.wait() != 0:
            raise RuntimeError(f"docker export failed for {platform}")
    finally:
        run(["docker", "rm", container], capture_output=True)


def apk(root: Path, arch: str, repositories: tuple[str, ...], amd64_digest: str, *arguments: str) -> None:
    repository_flags = [flag for repository in repositories for flag in ("-X", repository)]
    run([
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "--mount", f"type=bind,source={root},target=/r",
        f"alpine@{amd64_digest}",
        "apk", "--root", "/r", "--arch", arch, "--repositories-file", "/dev/null",
        "--keys-dir", "/r/etc/apk/keys", "--no-scripts", "--no-cache", "--quiet",
        *repository_flags, *arguments,
    ])


def installed(root: Path) -> dict[str, Installed]:
    packages: dict[str, Installed] = {}
    record: dict[str, str] = {}
    for line in [*(root / "lib" / "apk" / "db" / "installed").read_text().splitlines(), ""]:
        if line:
            key, _, value = line.partition(":")
            record.setdefault(key, value)
            continue
        if record:
            packages[record["P"]] = Installed(record["P"], record["V"], record["A"], record.get("L", ""))
            record = {}
    return packages


def layer(before: dict[str, Installed], after: dict[str, Installed]) -> list[Installed]:
    removed = sorted(set(before) - set(after))
    if removed:
        raise RuntimeError(f"layer removed installed packages: {removed}")
    return sorted(
        (package for name, package in after.items() if before.get(name) != package),
        key=lambda package: package.name,
    )


def pin(package: Installed, arch: str, repositories: tuple[str, ...], cache: Path) -> dict:
    filename = f"{package.name}-{package.version}.apk"
    failures = []
    for repository in repositories:
        url = f"{repository}/{arch}/{filename}"
        digest = hashlib.sha256()
        size = 0
        staged = cache / f".{filename}.part"
        try:
            with urllib.request.urlopen(url, timeout=120) as response, staged.open("wb") as output:
                while chunk := response.read(_CHUNK):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except urllib.error.HTTPError as error:
            staged.unlink(missing_ok=True)
            failures.append(f"{url}: {error.code}")
            continue
        sha256 = digest.hexdigest()
        staged.replace(cache / f"{sha256}.blob")
        return {"url": url, "sha256": sha256, "size": size}
    raise RuntimeError(f"{filename} is in no pinned repository: {failures}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, default=REPO / ".work" / "tmp" / "relock")
    args = parser.parse_args(argv)
    lock_path = REPO / "bootstrap.lock.json"
    document = json.loads(lock_path.read_text())
    section = document["linux_toolchain"]
    repositories = tuple(section["repositories"])
    images = section["images"]
    amd64_digest = images["amd64"]["digest"]
    cache = REPO / ".cache" / "bootstrap" / "objects"
    cache.mkdir(parents=True, exist_ok=True)
    toolchain_direct = [item["name"] for item in section["direct_packages"]]
    toolchain_versions: dict[str, set[str]] = {}
    closures = {}
    for arch, docker_arch in ARCHITECTURES.items():
        image = images[docker_arch]
        root = (args.scratch / f"root-{arch}").resolve()
        export_rootfs(image["platform"], image["digest"], root)
        base = installed(root)
        apk(root, arch, repositories, amd64_digest, "add", *toolchain_direct)
        toolchain = installed(root)
        for name in toolchain_direct:
            toolchain_versions.setdefault(name, set()).add(toolchain[name].version)
        closures[arch] = [
            {"name": package.name, "version": package.version, "arch": package.arch,
             **pin(package, arch, repositories, cache)}
            for package in layer(base, toolchain)
        ]
        backend_path = BACKEND_LOCKS[arch]
        backend = json.loads(backend_path.read_text())
        backend_direct = backend["direct_packages"]
        apk(root, arch, repositories, amd64_digest, "add", *backend_direct)
        backend_installed = installed(root)
        backend["apk_closure"] = [
            {"name": package.name, "version": package.version, "architecture": package.arch,
             **pin(package, arch, repositories, cache), "license": package.license}
            for package in layer(toolchain, backend_installed)
        ]
        backend_path.write_text(json.dumps(backend, indent=2) + "\n")
        print(f"{arch}: {len(closures[arch])} toolchain and {len(backend['apk_closure'])} backend packages")
    divergent = {name: sorted(versions) for name, versions in toolchain_versions.items() if len(versions) != 1}
    if divergent:
        raise RuntimeError(f"direct packages resolve differently per architecture: {divergent}")
    section["direct_packages"] = [
        {"name": name, "version": next(iter(toolchain_versions[name]))} for name in toolchain_direct
    ]
    section["apk_closure"] = closures
    lock_path.write_text(json.dumps(document, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
