"""Artifact construction, qualification records, and transactional publication."""

from __future__ import annotations

import gzip
import hashlib
import json
import lzma
import os
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from buildlib.hashing import digest_file
from buildlib.release import ReleaseError
from buildlib.release import create_archive as create_release_archive

from .deporder import dependency_order
from .inputs import canonical_json, locked_inputs, pin_identity
from .notices import bundled_native_names
from .product import Product
from .targets import Target

SOURCE_DATE_EPOCH = 1704067200


class PackageError(Exception):
    """A package artifact could not be prepared or published."""


def copy_product_tree(source: Path, destination: Path) -> Path:
    source = Path(source)
    destination = Path(destination)
    links: list[tuple[Path, Path]] = []
    directories: list[tuple[Path, Path]] = []

    def defer_links(directory, names):
        directory = Path(directory)
        directories.append((directory, destination / directory.relative_to(source)))
        ignored = []
        for name in names:
            path = directory / name
            if path.is_symlink():
                links.append((path, destination / path.relative_to(source)))
                ignored.append(name)
        return ignored

    shutil.copytree(source, destination, symlinks=True, ignore=defer_links)
    for src, dst in links:
        dst.symlink_to(src.readlink())
    for src, dst in links:
        shutil.copystat(src, dst, follow_symlinks=False)
    for src, dst in reversed(directories):
        shutil.copystat(src, dst, follow_symlinks=False)
    return destination


@dataclass(frozen=True)
class ArchiveFacts:
    sha256: str
    size_bytes: int
    payload_sha256: str
    payload_size_bytes: int


def _regular_mode(mode: int) -> int:
    return 0o755 if mode & 0o111 else 0o644


def _payload_facts(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with gzip.open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def create_archive(install: Path, archive_path: Path) -> ArchiveFacts:
    install = Path(install)
    archive_path = Path(archive_path)
    if not install.is_dir():
        raise PackageError(f"install tree does not exist: {install}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="archive-", dir=archive_path.parent) as scratch:
        try:
            tarball = create_release_archive(
                install, Path(scratch) / "payload.tar.xz", mtime=SOURCE_DATE_EPOCH, prefix="python"
            )
        except ReleaseError as error:
            raise PackageError(str(error)) from error
        with (
            lzma.open(tarball, "rb") as reader,
            archive_path.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=SOURCE_DATE_EPOCH) as compressed,
        ):
            shutil.copyfileobj(reader, compressed, 1 << 20)
    sha256, size_bytes = digest_file(archive_path)
    payload_sha256, payload_size_bytes = _payload_facts(archive_path)
    return ArchiveFacts(sha256, size_bytes, payload_sha256, payload_size_bytes)


def _tree_inventory(root: Path) -> dict[str, tuple]:
    inventory: dict[str, tuple] = {}
    for path in [root, *root.rglob("*")]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            inventory[relative] = ("directory", 0o755)
        elif stat.S_ISLNK(metadata.st_mode):
            inventory[relative] = ("symlink", 0o777, os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            digest, size = digest_file(path)
            inventory[relative] = ("file", _regular_mode(metadata.st_mode), size, digest)
        else:
            raise PackageError(f"unsupported extracted payload entry: {path}")
    return inventory


def payload_size_report(root: Path, *, top_n: int = 25) -> dict:
    """Compact size attribution for the final payload.

    Evidence for the slimming loop: total installed bytes and file count,
    the largest payload entries, the largest native extensions, and the
    largest standard-library directories. Deterministic ordering; sizes are
    uncompressed on-disk bytes, not archive bytes.
    """
    root = Path(root)
    entries: list[tuple[str, int]] = []
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                entries.append(
                    (path.relative_to(root).as_posix(), path.stat().st_size)
                )
        except OSError:
            continue
    entries.sort(key=lambda item: (item[1], item[0]))
    largest = [
        {"path": path, "size_bytes": size}
        for path, size in entries[-top_n:][::-1]
    ]
    extensions = sorted(
        (
            {"path": path, "size_bytes": size}
            for path, size in entries
            if "/lib-dynload/" in path and path.endswith((".so", ".dylib"))
        ),
        key=lambda item: (item["size_bytes"], item["path"]),
        reverse=True,
    )[:10]
    stdlib_roots = sorted(
        path for path in (root / "lib").glob("python3.*")
        if path.is_dir() and not path.is_symlink()
    )
    directories: list[dict] = []
    if stdlib_roots:
        stdlib_root = stdlib_roots[0]
        for child in sorted(stdlib_root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                total = sum(
                    size for path, size in entries
                    if path.startswith(child.relative_to(root).as_posix() + "/")
                )
                directories.append(
                    {"path": child.relative_to(root).as_posix(), "size_bytes": total}
                )
    directories.sort(key=lambda item: (item["size_bytes"], item["path"]), reverse=True)
    return {
        "total_files": len(entries),
        "total_bytes": sum(size for _, size in entries),
        "largest_files": largest,
        "largest_extensions": extensions,
        "largest_stdlib_dirs": directories[:10],
    }


def extract_package_archive(archive_path: Path, destination: Path) -> Path:
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise PackageError(f"extraction destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-extract-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        with tarfile.open(archive_path, "r:*") as archive:
            members = archive.getmembers()
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                name = member.name.rstrip("/")
                normalized = Path(name)
                if (
                    not name
                    or normalized.is_absolute()
                    or ".." in normalized.parts
                    or name != normalized.as_posix()
                    or name in by_name
                ):
                    raise PackageError(f"unsafe or duplicate archive path: {member.name!r}")
                if not (member.isdir() or member.isreg() or member.issym()):
                    raise PackageError(f"unsupported archive member: {member.name!r}")
                by_name[name] = member
            root = by_name.get("python")
            if root is None or not root.isdir():
                raise PackageError("archive does not contain a python directory")
            for name, member in by_name.items():
                parent = Path(name).parent
                while parent != Path("."):
                    parent_member = by_name.get(parent.as_posix())
                    if parent_member is None or not parent_member.isdir():
                        raise PackageError(f"archive path has a non-directory parent: {name}")
                    parent = parent.parent
                if member.issym():
                    link = Path(member.linkname)
                    if link.is_absolute():
                        raise PackageError(f"absolute archive symlink: {name} -> {member.linkname}")
                    resolved = Path(posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), member.linkname)
                    ))
                    if resolved.parts[:1] != ("python",) or ".." in resolved.parts:
                        raise PackageError(f"archive symlink escapes payload: {name} -> {member.linkname}")
                if any(
                    Path(name).is_relative_to(Path(parent_name))
                    for parent_name, parent_member in by_name.items()
                    if parent_name != name and parent_member.issym()
                ):
                    raise PackageError(f"archive entry is nested under a symlink: {name}")

            for member in members:
                name = member.name.rstrip("/")
                target = staged.joinpath(*Path(name).parts)
                if member.isdir():
                    target.mkdir(mode=member.mode & 0o777)
                    target.chmod(member.mode & 0o777)
                elif member.issym():
                    target.symlink_to(member.linkname)
                else:
                    source = archive.extractfile(member)
                    if source is None:
                        raise PackageError(f"archive file has no body: {name}")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode & 0o777)
        os.replace(staged, destination)
    return destination


def verify_archive_extraction(
    install: Path, archive_path: Path, extraction_parent: Path
) -> Path:
    extracted_root = Path(extraction_parent) / "extracted"
    extract_package_archive(Path(archive_path), extracted_root)
    extracted_install = extracted_root / "python"
    if not extracted_install.is_dir() or {path.name for path in extracted_root.iterdir()} != {"python"}:
        raise PackageError("archive does not contain exactly one top-level python directory")
    expected = _tree_inventory(Path(install))
    found = _tree_inventory(extracted_install)
    if expected != found:
        expected_paths = set(expected)
        found_paths = set(found)
        differences = sorted(expected_paths ^ found_paths)
        if not differences:
            differences = sorted(
                path for path in expected_paths if expected[path] != found[path]
            )
        raise PackageError(
            "archive extraction differs from the prepared install tree: "
            + ", ".join(differences[:20])
        )
    return extracted_install


def qualify(
    validation: dict,
    compatibility: dict,
    *,
    additional_checks: Mapping[str, dict] | None = None,
) -> dict:
    checks: dict[str, dict] = {}

    def add_validation(prefix: str, result: dict) -> None:
        result_checks = result.get("checks")
        if not isinstance(result_checks, dict):
            raise PackageError(f"{prefix} returned no check mapping")
        failures = set(result.get("failed", ()))
        for name, value in result_checks.items():
            if not isinstance(value, dict):
                value = {"evidence": value}
            ok = value.get("ok") is True and name not in failures
            checks[f"{prefix}.{name}"] = {
                **value,
                "status": "passed" if ok else "failed",
            }
            if not ok:
                failures.discard(name)
        for name in sorted(failures):
            key = f"{prefix}.{name}"
            checks.setdefault(key, {"status": "failed", "error": "reported failed"})
        if result.get("ok") is False and not any(
            value["status"] == "failed" and key.startswith(prefix + ".")
            for key, value in checks.items()
        ):
            checks[f"{prefix}.aggregate"] = {
                "status": "failed", "error": "validation aggregate reported failure"
            }

    def add_compatibility(prefix: str, result: dict) -> None:
        result_checks = result.get("checks")
        if not isinstance(result_checks, dict):
            raise PackageError(f"{prefix} returned no check mapping")
        failed_before = any(value.get("status") == "failed" for value in result_checks.values())
        for name, value in result_checks.items():
            if not isinstance(value, dict):
                raise PackageError(f"{prefix}.{name} returned a malformed check")
            status = value.get("status")
            if status not in {"passed", "skipped", "failed"}:
                status = "failed" if value.get("ok") is not True else "passed"
            checks[f"{prefix}.{name}"] = {**value, "status": status}
        if result.get("ok") is False and not failed_before:
            checks[f"{prefix}.aggregate"] = {
                "status": "failed", "error": "compatibility aggregate reported failure"
            }

    add_validation("validation", validation)
    add_compatibility("compatibility", compatibility)
    for name, value in (additional_checks or {}).items():
        if not isinstance(value, dict):
            raise PackageError(f"additional check {name} is malformed")
        ok = value.get("ok") is True
        checks[name] = {**value, "status": "passed" if ok else "failed"}
    return {
        "ok": all(check["status"] != "failed" for check in checks.values()),
        "checks": checks,
    }


def gil_enabled(qualification: dict) -> bool:
    checks = qualification.get("checks", {})
    for name, value in checks.items():
        if name.endswith("version_and_abi") and "gil_enabled" in value:
            return value["gil_enabled"] is True
        if name.endswith("gil_and_hash_policy"):
            evidence = value.get("evidence", {})
            if isinstance(evidence, dict) and "gil_enabled" in evidence:
                return evidence["gil_enabled"] is True
    raise PackageError("qualification did not report the interpreter GIL state")


def _component_manifest(
    lock_path: Path, target: Target, qualification: dict
) -> dict:
    entries = {entry.name: entry for entry in locked_inputs(lock_path)}
    bundled = []
    for name in ("cpython", "certifi-ca", *bundled_native_names(target)):
        entry = entries.get(name)
        if entry is None:
            continue
        bundled.append({
            "name": entry.name,
            "version": entry.version,
            "license": entry.license,
            "purpose": entry.purpose,
            "linkage": "payload" if name in {"cpython", "certifi-ca"} else "static",
        })
    if target.is_macos:
        platform_provided = [
            {"name": "zlib", "linkage": "dynamic", "source": "/usr/lib"},
            {"name": "libedit", "linkage": "dynamic", "source": "/usr/lib"},
            {"name": "ncurses/panel", "linkage": "dynamic", "source": "/usr/lib"},
            {"name": "ndbm", "linkage": "platform", "purpose": "dbm backend"},
        ]
    else:
        elf_check = qualification.get("checks", {}).get("validation.elf_linkage")
        if not isinstance(elf_check, dict):
            raise PackageError("Linux artifact has no verified ELF runtime dependency record")
        platform_libraries = elf_check.get("platform_libraries")
        if not isinstance(platform_libraries, list) or any(
            not isinstance(name, str) for name in platform_libraries
        ):
            raise PackageError("Linux artifact has no valid platform library list")
        platform_provided = [
            {
                "name": target.musl_loader,
                "linkage": "interpreter",
                "source": "Alpine runtime",
            },
            *(
                {
                    "name": name,
                    "linkage": "dynamic",
                    "source": "Alpine runtime",
                }
                for name in sorted(set(platform_libraries))
            ),
        ]
    return {
        "bundled": bundled,
        "platform_provided": platform_provided,
        "excluded_by_scope": [
            "pip", "ensurepip", "venv", "_tkinter", "tkinter", "idlelib",
            "turtle", "_gdbm", "test", "generated bytecode",
            "turtledemo", "import test fixtures",
            "native test C-API extensions",
            "sampling-profiler static web assets",
        ],
        "sqlite_features": [
            "FTS3", "FTS4", "FTS5", "enhanced FTS3 query syntax", "geopoly",
            "rtree", "dbstat", "loadable extensions",
        ],
    }


def build_manifest(
    *,
    product: Product,
    target: Target,
    source_revision: str,
    recipe_identity: str,
    bootstrap_identity: str,
    llvm_version: str,
    archive_filename: str,
    archive: ArchiveFacts,
    qualification: dict,
    lock_path: Path,
    build_record: dict,
    package_patches_directory: Path,
) -> dict:
    if not qualification.get("ok"):
        raise PackageError("refusing to manifest an unqualified artifact")
    gil = gil_enabled(qualification)
    if not gil:
        raise PackageError("qualified product does not have the GIL enabled")
    package_patches = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(Path(package_patches_directory).glob("*.patch"))
    ]
    bundle = next(
        (entry for entry in locked_inputs(lock_path) if entry.name == "certifi-ca"),
        None,
    )
    if bundle is None:
        raise PackageError("source lock has no certifi-ca trust bundle")
    return {
        "schema_version": 1,
        "product": {
            "version": product.version_string,
            "abi_tag": product.abi_tag,
            "gil_enabled": gil,
        },
        "target": {
            "triple": target.triple,
            "family": target.family,
            "machine": target.machine,
            "uv": {"arch": target.uv_arch, "os": target.uv_os, "libc": target.uv_libc},
        },
        "source_revision": source_revision,
        "build": {
            "source": build_record["source"],
            "patches": build_record["patches"],
            "dependencies": build_record["dependencies"],
            "toolchain_observed": build_record["toolchain_observed"],
            "optimization": build_record["optimization"],
            "effective_flags": build_record["effective_flags"],
            "commands": build_record["commands"],
            "build_mode": build_record["build_mode"],
            "isolation": build_record["isolation"],
        },
        "packaging": {
            "patches": package_patches,
            "trust_bundle": {
                "name": bundle.name,
                "version": bundle.version,
                "sha256": bundle.sha256,
                "size": bundle.size,
                "license": bundle.license,
                "identity": pin_identity(bundle),
            },
        },
        "recipe_identity": recipe_identity,
        "bootstrap_identity": bootstrap_identity,
        "toolchain": {"llvm_version": llvm_version},
        "payload": {
            "sha256": archive.payload_sha256,
            "size_bytes": archive.payload_size_bytes,
        },
        "archive": {
            "filename": archive_filename,
            "sha256": archive.sha256,
            "size_bytes": archive.size_bytes,
        },
        "qualification": qualification,
        "components": _component_manifest(lock_path, target, qualification),
    }


def write_manifest(path: Path, manifest: dict) -> None:
    Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def publish_files(prepared: Mapping[Path, Path]) -> None:
    if not prepared:
        raise PackageError("no package outputs to publish")
    destinations = list(prepared)
    parents = {path.parent.resolve() for path in destinations}
    if len(parents) != 1:
        raise PackageError("package outputs must share a destination directory")
    parent = destinations[0].parent
    parent.mkdir(parents=True, exist_ok=True)
    for destination, staged in prepared.items():
        if not staged.is_file() or staged.is_symlink():
            raise PackageError(f"prepared package output is not a regular file: {staged}")
        if destination.exists() and not destination.is_file():
            raise PackageError(f"package output is not a regular file: {destination}")
        if destination.is_symlink():
            raise PackageError(f"package output may not be a symlink: {destination}")

    with tempfile.TemporaryDirectory(prefix=".package-publish-", dir=parent) as temporary:
        transaction_root = Path(temporary)
        ready: dict[Path, Path] = {}
        backups: dict[Path, Path] = {}
        published: set[Path] = set()
        try:
            for index, (destination, staged) in enumerate(prepared.items()):
                candidate = transaction_root / f"{index}.prepared"
                shutil.copyfile(staged, candidate)
                with candidate.open("rb") as stream:
                    os.fsync(stream.fileno())
                ready[destination] = candidate
            for index, destination in enumerate(destinations):
                if destination.exists():
                    backup = transaction_root / f"{index}.backup"
                    os.replace(destination, backup)
                    backups[destination] = backup
            for destination in destinations:
                os.replace(ready[destination], destination)
                published.add(destination)
        except BaseException as publication_error:
            rollback_errors = []
            for destination in reversed(destinations):
                try:
                    if destination in published:
                        destination.unlink(missing_ok=True)
                    backup = backups.get(destination)
                    if backup is not None and backup.exists():
                        os.replace(backup, destination)
                except OSError as rollback_error:
                    rollback_errors.append(f"{destination}: {rollback_error}")
            if rollback_errors:
                raise PackageError(
                    "package publication failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from publication_error
            raise


def archive_path_for(target: Target, product: Product, revision: str) -> str:
    if not revision:
        raise PackageError("archive revision is empty")
    return product.local_archive_name(target.triple, revision)


def validate_build_record(
    record_path: Path,
    *,
    product: Product,
    target: Target,
    lock_path: Path,
    bootstrap_lock_path: Path,
    patches_directory: Path,
) -> dict:
    try:
        record = json.loads(Path(record_path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PackageError(f"CPython staged build record is missing or invalid: {error}") from error
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise PackageError("CPython staged build record has an unsupported schema")
    if record.get("target") != target.triple:
        raise PackageError("CPython staged build record targets a different platform")
    if record.get("product") != {
        "version": product.version_string,
        "abi_tag": product.abi_tag,
    }:
        raise PackageError("CPython staged build record names a different product")

    pins = [entry for entry in locked_inputs(lock_path) if entry.name == "cpython"]
    if len(pins) != 1:
        raise PackageError("source lock must contain exactly one CPython pin")
    pin = pins[0]
    source = record.get("source")
    if not isinstance(source, dict) or source != {
        "name": pin.name,
        "version": pin.version,
        "sha256": pin.sha256,
    }:
        raise PackageError("CPython staged build record does not match the locked source")

    pins_by_name = {entry.name: entry for entry in locked_inputs(lock_path)}
    dependency_names = tuple(
        name for name in dependency_order(target) if name != "pkgconf"
    )
    expected_dependencies = []
    for name in dependency_names:
        entry = pins_by_name.get(name)
        if entry is None:
            raise PackageError(f"source lock has no required dependency pin for {name}")
        expected_dependencies.append({
            "name": entry.name,
            "version": entry.version,
            "sha256": entry.sha256,
            "identity": pin_identity(entry),
        })
    if record.get("dependencies") != expected_dependencies:
        raise PackageError("CPython staged build record has stale dependency inputs")

    expected_patches = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(Path(patches_directory).glob("*.patch"))
    ]
    if record.get("patches") != expected_patches:
        raise PackageError("CPython staged build record has stale patch inputs")

    try:
        bootstrap_bytes = Path(bootstrap_lock_path).read_bytes()
        bootstrap = json.loads(bootstrap_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise PackageError(f"bootstrap lock is missing or invalid: {error}") from error
    bootstrap_identity = hashlib.sha256(bootstrap_bytes).hexdigest()
    if record.get("bootstrap_identity") != bootstrap_identity:
        raise PackageError("CPython staged build record has a stale bootstrap identity")

    toolchain = record.get("toolchain")
    observed = record.get("toolchain_observed")
    if not isinstance(toolchain, dict) or toolchain.get("family") != target.family:
        raise PackageError("CPython staged build record has no matching toolchain identity")
    observed_commands = observed.get("commands") if isinstance(observed, dict) else None
    if not isinstance(observed_commands, dict):
        raise PackageError("CPython staged build record has no observed tool commands")
    for command_name, command in observed_commands.items():
        if (
            not isinstance(command, dict)
            or not isinstance(command.get("path"), str)
            or not command["path"]
            or not isinstance(command.get("version"), str)
            or not command["version"]
        ):
            raise PackageError(
                f"CPython staged build record has incomplete observed tool {command_name}"
            )
    if toolchain.get("cpu_baseline") != target.cpu_baseline_cflag:
        raise PackageError("CPython staged build record has a stale CPU baseline")
    if target.is_macos:
        expected_llvm = bootstrap.get("macos_toolchain", {}).get("llvm", {}).get("version")
        if toolchain.get("llvm_version") != expected_llvm:
            raise PackageError("CPython staged build record has a stale LLVM version")
    else:
        commands = toolchain.get("commands")
        compiler = commands.get("cc") if isinstance(commands, dict) else None
        expected_llvm = bootstrap.get("linux_toolchain", {}).get("llvm", {}).get("version")
        if (
            expected_llvm is None
            or not isinstance(compiler, dict)
            or expected_llvm not in str(compiler.get("version", ""))
        ):
            raise PackageError("CPython staged build record has a stale LLVM version")

    optimization = record.get("optimization")
    environment = record.get("environment")
    effective_flags = record.get("effective_flags")
    commands = record.get("commands")
    if (
        not isinstance(optimization, dict)
        or not isinstance(optimization.get("configure_args"), list)
        or not all(isinstance(item, str) for item in optimization["configure_args"])
        or optimization.get("lto") != "thin"
        or optimization.get("pgo") is not target.pgo
        or not isinstance(environment, dict)
        or not isinstance(effective_flags, dict)
        or not isinstance(commands, dict)
        or any(not isinstance(commands.get(name), list) for name in ("configure", "build", "install"))
    ):
        raise PackageError("CPython staged build record has incomplete recipe evidence")
    recipe_material = {
        "target": target.triple,
        "product": product.version_string,
        "source": source,
        "patches": expected_patches,
        "dependencies": expected_dependencies,
        "configure_args": optimization["configure_args"],
        "environment": environment,
        "effective_flags": effective_flags,
        "toolchain": toolchain,
        "bootstrap_identity": bootstrap_identity,
    }
    expected_recipe_identity = hashlib.sha256(canonical_json(recipe_material).encode()).hexdigest()
    recipe_identity = record.get("recipe_identity")
    if (
        not isinstance(recipe_identity, str)
        or not re.fullmatch(r"[0-9a-f]{64}", recipe_identity)
        or recipe_identity != expected_recipe_identity
    ):
        raise PackageError("CPython staged build record has a mismatched recipe identity")
    if record.get("build_mode") != "sealed":
        raise PackageError("packaging requires a sealed CPython build")
    source_revision = record.get("source_revision")
    if not isinstance(source_revision, str) or not re.fullmatch(
        r"[0-9a-f]{40,64}", source_revision
    ):
        raise PackageError("CPython staged build record has an invalid source revision")
    if record.get("source_dirty") is not False:
        raise PackageError("packaging requires a clean source revision")
    isolation = record.get("isolation")
    if (
        not isinstance(isolation, dict)
        or not isinstance(isolation.get("mechanism"), str)
        or isolation.get("network") != "disabled"
    ):
        raise PackageError("CPython staged build record has no isolation evidence")
    if target.is_macos:
        if isolation.get("mechanism") != "sandbox-exec":
            raise PackageError("macOS packaging requires sandbox-exec build evidence")
        for name in ("network_boundary_before", "network_boundary_after"):
            evidence = isolation.get(name)
            if not isinstance(evidence, dict) or evidence.get("ok") is not True:
                raise PackageError(f"macOS build has no successful {name} evidence")
    elif isolation.get("mechanism") != "buildkit-network-none":
        raise PackageError("Linux packaging requires BuildKit network-disabled evidence")
    return record
