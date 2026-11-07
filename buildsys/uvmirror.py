"""Validate qualified artifacts and assemble uv download metadata."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from .generations import GenerationError, active_generation
from .product import PRODUCT
from .targets import TARGETS

VERSION = PRODUCT.version_string
TRIPLES = {
    triple: {
        "key_arch": target.uv_arch,
        "os": target.uv_os,
        "libc": target.uv_libc,
    }
    for triple, target in TARGETS.items()
}
CANONICAL_PREFIX = "https://github.com/astral-sh/python-build-standalone/releases/download"
_BOOTSTRAP_LOCK = json.loads((Path(__file__).resolve().parents[1] / "bootstrap.lock.json").read_text())
LLVM_VERSIONS = {
    triple: _BOOTSTRAP_LOCK["macos_toolchain" if target.is_macos else "linux_toolchain"]["llvm"]["version"]
    for triple, target in TARGETS.items()
}
RELEASE_BUDGETS = Path(__file__).resolve().parents[1] / "release-budgets.json"


class UvMirrorError(Exception):
    """Release assets failed identity, qualification, or size validation."""


def asset_name(tag: str, triple: str) -> str:
    if triple not in TRIPLES:
        raise UvMirrorError(f"unsupported release target {triple!r}")
    return PRODUCT.mirror_archive_name(tag, triple)


def metadata_key(triple: str) -> str:
    try:
        info = TRIPLES[triple]
    except KeyError:
        raise UvMirrorError(f"unsupported release target {triple!r}") from None
    return f"cpython-{VERSION}-{info['os']}-{info['key_arch']}-{info['libc']}"


def metadata_entry(triple: str, tag: str, url_base: str, sha256: str) -> dict:
    info = TRIPLES[triple]
    name = asset_name(tag, triple)
    return {
        "name": "cpython",
        "arch": {"family": info["key_arch"], "variant": None},
        "os": info["os"],
        "libc": info["libc"],
        "major": PRODUCT.version[0],
        "minor": PRODUCT.version[1],
        "patch": PRODUCT.version[2],
        "prerelease": "",
        "url": f"{url_base.rstrip('/')}/{tag}/{quote(name, safe='')}",
        "sha256": sha256,
        "variant": None,
        "build": tag,
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_facts(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except (OSError, EOFError) as error:
        raise UvMirrorError(f"cannot read artifact payload {path}: {error}") from error
    return digest.hexdigest(), size


def _expected_revision(expected_revision: str | None) -> str:
    revision = expected_revision or os.environ.get("GITHUB_SHA")
    if not revision:
        try:
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise UvMirrorError(f"cannot determine artifact source revision: {error}") from error
    if re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        raise UvMirrorError(f"invalid expected source revision {revision!r}")
    return revision


def _load_manifest(dist: Path, triple: str, expected_revision: str) -> tuple[Path, dict]:
    try:
        directory = active_generation(Path(dist) / triple)
    except GenerationError as error:
        raise UvMirrorError(f"{triple}: no complete published artifact: {error}") from error
    return _load_manifest_from(directory, triple, expected_revision)


def _load_manifest_from(
    directory: Path, triple: str, expected_revision: str
) -> tuple[Path, dict]:
    target = TARGETS[triple]
    manifest_path = directory / "artifact.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise UvMirrorError(f"{triple}: artifact manifest is missing or invalid: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise UvMirrorError(f"{triple}: unsupported artifact manifest schema")
    if manifest.get("product") != {
        "version": PRODUCT.version_string,
        "abi_tag": PRODUCT.abi_tag,
        "gil_enabled": True,
    }:
        raise UvMirrorError(f"{triple}: artifact has a different product identity")
    if manifest.get("target") != {
        "triple": triple,
        "family": target.family,
        "machine": target.machine,
        "uv": {"arch": target.uv_arch, "os": target.uv_os, "libc": target.uv_libc},
    }:
        raise UvMirrorError(f"{triple}: artifact target metadata does not match the target table")
    if manifest.get("source_revision") != expected_revision:
        raise UvMirrorError(
            f"{triple}: artifact revision {manifest.get('source_revision')!r} "
            f"does not match built revision {expected_revision}"
        )
    if manifest.get("toolchain", {}).get("llvm_version") != LLVM_VERSIONS[triple]:
        raise UvMirrorError(f"{triple}: artifact does not use locked LLVM {LLVM_VERSIONS[triple]}")
    qualification = manifest.get("qualification")
    if not isinstance(qualification, dict) or qualification.get("ok") is not True:
        raise UvMirrorError(f"{triple}: artifact qualification did not pass")
    checks = qualification.get("checks")
    if not isinstance(checks, dict) or not checks or any(
        not isinstance(check, dict) or check.get("status") not in {"passed", "skipped"}
        for check in checks.values()
    ):
        raise UvMirrorError(f"{triple}: artifact contains a failed or malformed qualification check")
    archive = manifest.get("archive")
    if not isinstance(archive, dict):
        raise UvMirrorError(f"{triple}: artifact manifest has no archive identity")
    filename = archive.get("filename")
    if not isinstance(filename, str) or not filename.startswith(
        f"cpython-{VERSION}-{triple}-"
    ) or not filename.endswith(".tar.gz") or Path(filename).name != filename:
        raise UvMirrorError(f"{triple}: artifact manifest has an invalid archive filename")
    archive_path = directory / filename
    if archive_path.is_symlink() or not archive_path.is_file():
        raise UvMirrorError(f"{triple}: packaged archive is missing: {archive_path}")
    actual_hash = sha256_of(archive_path)
    actual_size = archive_path.stat().st_size
    if archive.get("sha256") != actual_hash or archive.get("size_bytes") != actual_size:
        raise UvMirrorError(f"{triple}: artifact archive digest or size does not match its manifest")
    payload_hash, payload_size = _payload_facts(archive_path)
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or payload.get("sha256") != payload_hash:
        raise UvMirrorError(f"{triple}: artifact payload digest does not match its manifest")
    if payload.get("size_bytes") != payload_size:
        raise UvMirrorError(f"{triple}: artifact payload size does not match its manifest")
    return archive_path, manifest


def validate_artifact(
    directory: Path, triple: str, expected_revision: str
) -> tuple[Path, dict]:
    if triple not in TARGETS:
        raise UvMirrorError(f"unsupported artifact target {triple!r}")
    if re.fullmatch(r"[0-9a-f]{40,64}", expected_revision) is None:
        raise UvMirrorError(f"invalid expected source revision {expected_revision!r}")
    return _load_manifest_from(Path(directory), triple, expected_revision)


def record_runtime_container(
    directory: Path,
    triple: str,
    expected_revision: str,
    execution: str,
) -> dict:
    if execution not in {"native", "emulated"}:
        raise UvMirrorError(f"invalid runtime execution mode {execution!r}")
    package_directory = Path(directory)
    archive, manifest = validate_artifact(
        package_directory, triple, expected_revision
    )
    try:
        runtime = json.loads((package_directory / "runtime-smoke.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise UvMirrorError(f"{triple}: runtime smoke result is missing or invalid: {error}") from error
    if (
        runtime.get("ok") is not True
        or runtime.get("target") != triple
        or runtime.get("archive") != archive.name
        or runtime.get("archive_sha256") != manifest["archive"]["sha256"]
        or runtime.get("python") != manifest["product"]["version"]
    ):
        raise UvMirrorError(f"{triple}: runtime smoke result does not match its artifact")
    checks = manifest["qualification"]["checks"]
    checks["runtime_container"] = {
        "status": "passed",
        "execution": execution,
        "python": runtime["python"],
        "target": triple,
        "archive_sha256": runtime["archive_sha256"],
    }
    manifest["qualification"]["ok"] = all(
        check.get("status") in {"passed", "skipped"}
        for check in checks.values()
    )
    if not manifest["qualification"]["ok"]:
        raise UvMirrorError(f"{triple}: runtime smoke did not preserve qualification")
    from .package import write_manifest

    write_manifest(package_directory / "artifact.json", manifest)
    return manifest


def _load_budgets(path: Path) -> dict[str, dict[str, int]]:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise UvMirrorError(f"release size budgets are missing or invalid: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise UvMirrorError("release size budget file has an unsupported schema")
    targets = document.get("targets")
    if not isinstance(targets, dict):
        raise UvMirrorError("release size budget file has an unsupported schema")
    result = {}
    for triple in TARGETS:
        entry = targets.get(triple)
        if not isinstance(entry, dict):
            raise UvMirrorError(f"{triple}: no reviewed compressed and unpacked size budget")
        compressed = entry.get("archive_size_bytes")
        unpacked = entry.get("payload_size_bytes")
        if any(type(value) is not int or value < 1 for value in (compressed, unpacked)):
            raise UvMirrorError(f"{triple}: size budgets must be positive integers")
        if not isinstance(entry.get("rationale"), str) or not entry["rationale"].strip():
            raise UvMirrorError(f"{triple}: size budget has no rationale")
        result[triple] = {
            "archive_size_bytes": compressed,
            "payload_size_bytes": unpacked,
        }
    if set(targets) != set(TARGETS):
        raise UvMirrorError("release size budgets do not match the supported target set")
    return result


def _metadata(tag: str, url_base: str, hashes: dict[str, str]) -> dict:
    return {
        metadata_key(triple): metadata_entry(
            triple, tag, url_base, hashes[asset_name(tag, triple)]
        )
        for triple in TARGETS
    }


def assemble(
    tag: str,
    repo: str,
    dist: Path,
    out: Path,
    *,
    expected_revision: str | None = None,
    budgets_path: Path = RELEASE_BUDGETS,
) -> dict:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag) is None:
        raise UvMirrorError(f"refusing unsafe release tag {tag!r}")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is None:
        raise UvMirrorError(f"invalid GitHub repository {repo!r}")
    revision = _expected_revision(expected_revision)
    budgets = _load_budgets(budgets_path)
    prepared: dict[str, tuple[Path, Path, dict]] = {}
    for triple in TARGETS:
        source, manifest = _load_manifest(dist, triple, revision)
        if TARGETS[triple].family == "linux-musl":
            runtime = manifest.get("qualification", {}).get("checks", {}).get(
                "runtime_container", {}
            )
            if (
                not isinstance(runtime, dict)
                or runtime.get("status") != "passed"
                or runtime.get("execution") != "native"
            ):
                raise UvMirrorError(
                    f"{triple}: release assembly requires native runtime-container qualification"
                )
        limits = budgets[triple]
        if source.stat().st_size > limits["archive_size_bytes"]:
            raise UvMirrorError(f"{triple}: compressed archive exceeds its reviewed size budget")
        if manifest["payload"]["size_bytes"] > limits["payload_size_bytes"]:
            raise UvMirrorError(f"{triple}: unpacked payload exceeds its reviewed size budget")
        _target = TARGETS[triple]
        if manifest.get("toolchain", {}).get("llvm_version") != LLVM_VERSIONS[triple]:
            raise UvMirrorError(f"{triple}: toolchain identity changed")
        prepared[triple] = (source, source.parent / "artifact.json", manifest)

    output = Path(out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise UvMirrorError(f"refusing to overwrite existing release directory {output}")
    hashes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for triple, (source, manifest_path, manifest) in prepared.items():
            name = asset_name(tag, triple)
            archive = staging / name
            shutil.copyfile(source, archive)
            hashes[name] = sha256_of(archive)
            shutil.copyfile(manifest_path, staging / f"artifact-{triple}.json")
            manifest_hash = sha256_of(staging / f"artifact-{triple}.json")
            hashes[f"artifact-{triple}.json"] = manifest_hash
        (staging / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
        )
        release_base = f"https://github.com/{repo}/releases/download"
        (staging / "download-metadata.json").write_text(
            json.dumps(_metadata(tag, release_base, hashes), indent=2) + "\n"
        )
        (staging / "smoke-metadata.json").write_text(
            json.dumps(_metadata(tag, CANONICAL_PREFIX, hashes), indent=2) + "\n"
        )
        report = {
            "tag": tag,
            "source_revision": revision,
            "assets": sorted(hashes),
            "out": str(output),
        }
        (staging / "assembly.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        os.replace(staging, output)
    return report
