"""Fetch locked source inputs and provision the official macOS toolchain."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from buildsys.bootstrap import (
    BootstrapError,
    load_linux_bootstrap,
    load_macos_toolchain,
)
from buildsys.inputs import Cache, InputError, locked_inputs
from buildsys.llvm import (
    extract_llvm_archive,
    verify_llvm_attestation_metadata,
)
from buildsys.targets import native_target, target_for_triple
from buildsys.wheel import INPUT_NAME as BUILDLIB_INPUT
from buildsys.wheel import VIEW_RELATIVE_PATH as BUILDLIB_VIEW


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch verified build inputs")
    parser.add_argument("--target", default=native_target().triple)
    args = parser.parse_args(argv)
    target = target_for_triple(args.target)
    source_cache = Cache(REPO / ".cache")
    try:
        inputs = locked_inputs(REPO / "sources.lock.json")
        for entry in inputs:
            if entry.role == "reference":
                continue
            try:
                path = source_cache.require(entry)
                print(f"cached  {entry.name}")
            except InputError:
                print(f"fetch   {entry.name} ...", flush=True)
                path = source_cache.fetch(entry)
                print(f"ok      {entry.name} -> {path}")

        if target.is_macos:
            locked = load_macos_toolchain(REPO / "bootstrap.lock.json")
            llvm_cache = Cache(REPO / ".cache" / "llvm")
            try:
                archive = llvm_cache.require(locked.llvm_input())
                print("cached  llvm-macos-aarch64")
            except InputError:
                print("fetch   llvm-macos-aarch64 ...", flush=True)
                archive = llvm_cache.fetch(locked.llvm_input())
                print(f"ok      llvm-macos-aarch64 -> {archive}")
            try:
                provenance = llvm_cache.require(locked.llvm_attestation_input())
                print("cached  llvm-macos-aarch64-provenance")
            except InputError:
                print("fetch   llvm-macos-aarch64-provenance ...", flush=True)
                provenance = llvm_cache.fetch(locked.llvm_attestation_input())
                print(f"ok      llvm-macos-aarch64-provenance -> {provenance}")
            verify_llvm_attestation_metadata(
                provenance,
                archive_filename=Path(locked.llvm_archive_url).name,
                archive_sha256=locked.llvm_archive_sha256,
                release_tag=locked.llvm_release_tag,
                source_commit=locked.llvm_source_commit,
                workflow=locked.llvm_workflow,
                workflow_path=locked.llvm_workflow_path,
            )
            prefix = extract_llvm_archive(
                archive,
                locked.llvm_prefix,
                archive_root=locked.llvm_archive_root,
                sha256=locked.llvm_archive_sha256,
                size=locked.llvm_archive_size,
                version=locked.llvm_version,
            )
            print(f"ready   llvm-macos-aarch64 -> {prefix}")
        else:
            locked = load_linux_bootstrap(REPO / "bootstrap.lock.json")
            docker_arch = target.docker_platform.rsplit("/", 1)[-1]
            image = locked.images[docker_arch]
            if image.alpine_arch != target.alpine_arch:
                raise InputError(
                    f"bootstrap image architecture {image.alpine_arch} does not match "
                    f"target {target.alpine_arch}"
                )
            package_cache = Cache(REPO / ".cache" / "bootstrap")
            view = REPO / ".cache" / "bootstrap" / "apk" / docker_arch
            _publish_view(
                package_cache,
                view,
                [(package.input(target.triple), f"{package.sha256}.apk")
                 for package in locked.apk_closure[target.alpine_arch]],
            )
            toolchain_view = REPO / ".cache" / "bootstrap" / "toolchain" / docker_arch
            _publish_view(
                package_cache,
                toolchain_view,
                [(locked.llvm.archives[target.alpine_arch].input(target.triple),
                  locked.llvm.archives[target.alpine_arch].filename)],
            )
            wheel = next(entry for entry in inputs if entry.name == BUILDLIB_INPUT)
            _publish_view(source_cache, REPO / BUILDLIB_VIEW, [(wheel, f"{wheel.sha256}.whl")])
            print(f"ready   offline LLVM toolchain -> {toolchain_view}")
            print(f"ready   buildlib wheel -> {REPO / BUILDLIB_VIEW}")
            print(f"ready   offline Alpine APK closure -> {view}")
    except (BootstrapError, InputError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


def _publish_view(cache: Cache, view: Path, entries: list) -> None:
    view.mkdir(parents=True, exist_ok=True)
    expected_files = {filename for _, filename in entries}
    for old in view.iterdir():
        if old.name not in expected_files:
            old.unlink()
    for entry, filename in entries:
        try:
            path = cache.require(entry)
            print(f"cached  {entry.name}")
        except InputError:
            print(f"fetch   {entry.name} ...", flush=True)
            path = cache.fetch(entry)
            print(f"ok      {entry.name} -> {path}")
        destination = view / filename
        with tempfile.NamedTemporaryFile(dir=view, suffix=".part", delete=False) as handle:
            staged = Path(handle.name)
        try:
            shutil.copyfile(path, staged)
            os.chmod(staged, 0o444)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
