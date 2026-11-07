from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from buildsys.comparison import compare_completed_artifact
from buildsys.deporder import dependency_order
from buildsys.generations import active_generation, publish_generation
from buildsys.inputs import canonical_json, load_lock
from buildsys.package import (
    ArchiveFacts,
    PackageError,
    archive_path_for,
    build_manifest,
    copy_product_tree,
    create_archive,
    publish_files,
    qualify,
    validate_build_record,
    verify_archive_extraction,
)
from buildsys.product import PRODUCT, Product
from buildsys.targets import target_for_triple

REPO = Path(__file__).resolve().parents[1]
SOURCE_LOCK = REPO / "sources.lock.json"
BUILD_RECORD = {
    "source": {"name": "cpython", "version": "3.15.1", "sha256": "e" * 64},
    "patches": [],
    "dependencies": [],
    "optimization": {"lto": "thin", "pgo": False},
    "toolchain_observed": {
        "commands": {
            "cc": {"path": "/usr/bin/clang", "version": "clang version 23.1.5"},
        },
    },
    "effective_flags": {"CFLAGS": "-O3"},
    "commands": {"configure": ["configure"], "build": ["make"], "install": ["make", "install"]},
    "build_mode": "sealed",
    "isolation": {"mechanism": "buildkit-network-none", "network": "disabled"},
}


class ArchiveTests(unittest.TestCase):
    def test_product_copy_materializes_referents_before_link_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "00-alias").symlink_to("99-target")
            (source / "01-chain").symlink_to("00-alias")
            (source / "99-target").write_bytes(b"product contents")
            (source / "99-target").chmod(0o755)
            os.utime(source / "00-alias", (1704067200, 1704067200), follow_symlinks=False)
            os.utime(source, (1704067200, 1704067200))
            original = shutil.copystat

            def copy_metadata(src, dst, *, follow_symlinks=True):
                if Path(dst).is_symlink() and not Path(dst).exists():
                    raise FileNotFoundError("no-follow xattrs require a materialized referent")
                return original(src, dst, follow_symlinks=follow_symlinks)

            destination = root / "destination"
            with patch("shutil.copystat", side_effect=copy_metadata):
                copy_product_tree(source, destination)
            self.assertEqual((destination / "00-alias").readlink(), Path("99-target"))
            self.assertEqual((destination / "01-chain").readlink(), Path("00-alias"))
            self.assertEqual((destination / "01-chain").read_bytes(), b"product contents")
            self.assertEqual((destination / "99-target").stat().st_mode, (source / "99-target").stat().st_mode)
            self.assertEqual((destination / "00-alias").lstat().st_mtime_ns, (source / "00-alias").lstat().st_mtime_ns)
            self.assertEqual(destination.stat().st_mtime_ns, source.stat().st_mtime_ns)

    def test_archive_is_reproducible_and_extraction_preserves_modes_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            executable = install / "bin" / "python"
            library = install / "lib" / "module.py"
            executable.parent.mkdir(parents=True)
            library.parent.mkdir(parents=True)
            executable.write_bytes(b"#!/bin/sh\nexit 0\n")
            executable.chmod(0o751)
            library.write_bytes(b"answer = 42\n")
            library.chmod(0o600)
            link = install / "lib" / "python"
            link.symlink_to("../bin/python")

            first = root / "first-output" / "first.tar.gz"
            second = root / "different-output" / "second.tar.gz"
            first_facts = create_archive(install, first)
            os.utime(executable, (100, 100))
            os.utime(install, (200, 200))
            second_facts = create_archive(install, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_facts, second_facts)
            header = first.read_bytes()[:10]
            self.assertEqual(header[3], 0)
            self.assertEqual(int.from_bytes(header[4:8], "little"), 1704067200)
            self.assertEqual(
                first_facts.sha256,
                hashlib.sha256(first.read_bytes()).hexdigest(),
            )

            extracted = verify_archive_extraction(
                install, first, root / "verification"
            )
            self.assertEqual((extracted / "bin" / "python").stat().st_mode & 0o777, 0o755)
            self.assertEqual((extracted / "lib" / "module.py").stat().st_mode & 0o777, 0o644)
            self.assertTrue((extracted / "lib" / "python").is_symlink())
            self.assertEqual(os.readlink(extracted / "lib" / "python"), "../bin/python")
            with tarfile.open(first, "r:gz") as archive:
                members = {member.name: member for member in archive.getmembers()}
            self.assertEqual(members["python/bin/python"].mode, 0o755)
            self.assertEqual(members["python/lib/module.py"].mode, 0o644)
            self.assertEqual(members["python/lib/python"].linkname, "../bin/python")
            self.assertTrue(all(member.mtime == 1704067200 for member in members.values()))


class ManifestTests(unittest.TestCase):
    def test_manifest_uses_locked_product_target_and_reproducible_identities(self) -> None:
        target = target_for_triple("aarch64-unknown-linux-musl")
        qualification = {
            "ok": True,
            "checks": {
                "validation.version_and_abi": {"gil_enabled": True},
                "validation.elf_linkage": {
                    "platform_libraries": ["libc.musl-aarch64.so.1"],
                },
            },
        }
        archive = ArchiveFacts("a" * 64, 1234, "b" * 64, 5678)
        arguments = {
            "product": Product((3, 15, 1)),
            "target": target,
            "source_revision": "f" * 40,
            "recipe_identity": "c" * 64,
            "bootstrap_identity": "d" * 64,
            "llvm_version": "23.1.5",
            "archive_filename": archive_path_for(
                target, Product((3, 15, 1)), "r2"
            ),
            "archive": archive,
            "qualification": qualification,
            "lock_path": SOURCE_LOCK,
            "build_record": BUILD_RECORD,
            "package_patches_directory": REPO / "patches" / "package",
        }
        first = build_manifest(**arguments)
        second = build_manifest(**arguments)

        self.assertEqual(first, second)
        self.assertEqual(first["product"], {
            "version": "3.15.1", "abi_tag": "cp315", "gil_enabled": True,
        })
        self.assertEqual(first["target"]["uv"], {
            "arch": "aarch64", "os": "linux", "libc": "musl",
        })
        self.assertEqual(first["archive"]["filename"],
                         "cpython-3.15.1-aarch64-unknown-linux-musl-r2.tar.gz")
        self.assertEqual(first["payload"]["sha256"], "b" * 64)
        self.assertEqual(first["payload"]["size_bytes"], 5678)
        self.assertTrue(first["components"]["bundled"])
        self.assertTrue(all("license" in entry for entry in first["components"]["bundled"]))
        self.assertEqual(first["components"]["platform_provided"], [
            {
                "name": target.musl_loader,
                "linkage": "interpreter",
                "source": "Alpine runtime",
            },
            {
                "name": "libc.musl-aarch64.so.1",
                "linkage": "dynamic",
                "source": "Alpine runtime",
            },
        ])
        self.assertNotIn("reference", first)
        self.assertNotIn("parity", first)
        self.assertNotIn("benchmarks", first)

    def test_qualification_keeps_each_failure_and_rejects_aggregate_failure(self) -> None:
        result = qualify(
            {
                "ok": False,
                "failed": ["abi", "layout"],
                "checks": {"abi": {"ok": False, "error": "wrong tag"}},
            },
            {
                "ok": False,
                "checks": {"runtime": {"status": "failed", "error": "no TLS"}},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["validation.abi"]["status"], "failed")
        self.assertEqual(result["checks"]["validation.layout"]["status"], "failed")
        self.assertEqual(result["checks"]["compatibility.runtime"]["status"], "failed")

    def test_manifest_rejects_non_gil_or_unreported_gil_state(self) -> None:
        base = {
            "product": Product((3, 15, 1)),
            "target": target_for_triple("aarch64-unknown-linux-musl"),
            "source_revision": "f" * 40,
            "recipe_identity": "c" * 64,
            "bootstrap_identity": "d" * 64,
            "llvm_version": "23.1.5",
            "archive_filename": "artifact.tar.gz",
            "archive": ArchiveFacts("a" * 64, 1, "b" * 64, 2),
            "lock_path": SOURCE_LOCK,
            "build_record": BUILD_RECORD,
            "package_patches_directory": REPO / "patches" / "package",
        }
        for qualification in (
            {"ok": True, "checks": {"validation.version_and_abi": {"gil_enabled": False}}},
            {"ok": True, "checks": {}},
        ):
            with self.subTest(qualification=qualification), self.assertRaises(PackageError):
                build_manifest(**base, qualification=qualification)

    def test_build_record_binds_locked_inputs_target_and_installed_recipe(self) -> None:
        target = target_for_triple("aarch64-unknown-linux-musl")
        entries = load_lock(SOURCE_LOCK)
        source = next(entry for entry in entries if entry.name == "cpython")
        pins_by_name = {entry.name: entry for entry in entries}
        dependencies = [
            {
                "name": pins_by_name[name].name,
                "version": pins_by_name[name].version,
                "sha256": pins_by_name[name].sha256,
                "identity": pins_by_name[name].identity(),
            }
            for name in dependency_order(target)
            if name != "pkgconf"
        ]
        patches = [
            {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted((REPO / "patches" / "cpython").glob("*.patch"))
        ]
        toolchain = {
            "family": target.family,
            "cpu_baseline": target.cpu_baseline_cflag,
            "commands": {"cc": {"version": "clang version 23.1.5"}},
        }
        optimization = {
            "configure_args": ["--enable-shared"], "lto": "thin", "pgo": False,
        }
        environment = {"CC": "clang"}
        effective_flags = {
            "CFLAGS": "-O3", "CFLAGS_NODIST": "-O3", "PY_CFLAGS": "-O3",
            "PY_CFLAGS_NODIST": "-O3", "LDFLAGS": "", "LTOFLAGS": "",
            "CONFIG_ARGS": "--enable-shared",
        }
        recipe_material = {
            "target": target.triple,
            "product": PRODUCT.version_string,
            "source": {"name": source.name, "version": source.version, "sha256": source.sha256},
            "patches": patches,
            "dependencies": dependencies,
            "configure_args": optimization["configure_args"],
            "environment": environment,
            "effective_flags": effective_flags,
            "toolchain": toolchain,
            "bootstrap_identity": hashlib.sha256(
                (REPO / "bootstrap.lock.json").read_bytes()
            ).hexdigest(),
        }
        bootstrap_bytes = (REPO / "bootstrap.lock.json").read_bytes()
        record = {
            "schema_version": 1,
            "target": target.triple,
            "product": {"version": PRODUCT.version_string, "abi_tag": PRODUCT.abi_tag},
            "source_revision": "f" * 40,
            "source_dirty": False,
            "build_mode": "sealed",
            "isolation": {
                "mechanism": "buildkit-network-none",
                "network": "disabled",
            },
            "source": {"name": source.name, "version": source.version, "sha256": source.sha256},
            "patches": patches,
            "dependencies": dependencies,
            "recipe_identity": hashlib.sha256(canonical_json(recipe_material).encode()).hexdigest(),
            "bootstrap_identity": hashlib.sha256(bootstrap_bytes).hexdigest(),
            "toolchain": toolchain,
            "toolchain_observed": {
                "commands": {
                    "cc": {"path": "/usr/bin/clang", "version": "clang version 23.1.5"},
                },
            },
            "optimization": optimization,
            "environment": environment,
            "effective_flags": effective_flags,
            "commands": {name: ["tool"] for name in ("configure", "build", "install")},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "build-record.json"
            path.write_text(json.dumps(record))
            validated = validate_build_record(
                path,
                product=PRODUCT,
                target=target,
                lock_path=SOURCE_LOCK,
                bootstrap_lock_path=REPO / "bootstrap.lock.json",
                patches_directory=REPO / "patches" / "cpython",
            )
            self.assertEqual(validated["recipe_identity"], record["recipe_identity"])

            for field, replacement in (
                ("target", "x86_64-unknown-linux-musl"),
                ("source", {**record["source"], "version": "stale"}),
                ("recipe_identity", "0" * 64),
                ("build_mode", "development"),
                ("source_dirty", True),
                ("isolation", {**record["isolation"], "network": "enabled"}),
                ("toolchain", {**toolchain, "cpu_baseline": "-march=native"}),
            ):
                stale = {**record, field: replacement}
                path.write_text(json.dumps(stale))
                with self.subTest(field=field), self.assertRaises(PackageError):
                    validate_build_record(
                        path,
                        product=PRODUCT,
                        target=target,
                        lock_path=SOURCE_LOCK,
                        bootstrap_lock_path=REPO / "bootstrap.lock.json",
                        patches_directory=REPO / "patches" / "cpython",
                    )


class StagedBuildVerificationTests(unittest.TestCase):
    def test_failed_interpreter_cannot_supply_effective_flag_evidence(self) -> None:
        driver = runpy.run_path(str(REPO / "build" / "package.py"))
        verifier = driver["_verify_install_matches_record"]
        probe = Mock(
            returncode=1,
            stdout='{"CFLAGS":"-O3"}\n',
            stderr="interpreter startup failed",
        )
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            verifier.__globals__,
            {"run_product_process": Mock(return_value=probe)},
        ), self.assertRaisesRegex(
            driver["PackagingError"], "interpreter could not report"
        ):
            verifier(Path(temporary), {"effective_flags": {"CFLAGS": "-O3"}})

    def test_relocated_prefix_is_the_only_allowed_configuration_difference(self) -> None:
        driver = runpy.run_path(str(REPO / "build" / "package.py"))
        verifier = driver["_verify_install_matches_record"]
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary) / "copy with spaces"
            recorded = {
                "CFLAGS": "-O3",
                "CFLAGS_NODIST": "-O3",
                "PY_CFLAGS": "-O3",
                "PY_CFLAGS_NODIST": "-O3",
                "LDFLAGS": "-flto=thin",
                "LTOFLAGS": "-flto=thin",
                "CONFIG_ARGS": "'--prefix=/install' '--with-lto=thin' 'PANEL_LIBS=\"",
            }
            relocated = {
                **recorded,
                "CONFIG_ARGS": (
                    f"'--prefix={install.resolve()}' '--with-lto=thin' 'PANEL_LIBS=\""
                ),
            }
            probe = Mock(
                returncode=0,
                stdout=json.dumps(relocated) + "\n",
                stderr="",
            )
            with patch.dict(
                verifier.__globals__,
                {"run_product_process": Mock(return_value=probe)},
            ):
                verifier(install, {"effective_flags": recorded})

    def test_relocated_prefix_does_not_hide_other_configuration_changes(self) -> None:
        driver = runpy.run_path(str(REPO / "build" / "package.py"))
        verifier = driver["_verify_install_matches_record"]
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary) / "copy with spaces"
            recorded = {
                "CFLAGS": "-O3",
                "CFLAGS_NODIST": "-O3",
                "PY_CFLAGS": "-O3",
                "PY_CFLAGS_NODIST": "-O3",
                "LDFLAGS": "-flto=thin",
                "LTOFLAGS": "-flto=thin",
                "CONFIG_ARGS": "'--prefix=/install' '--with-lto=thin'",
            }
            probes = (
                {
                    **recorded,
                    "CONFIG_ARGS": "'--prefix=/unexpected' '--with-lto=thin' 'PANEL_LIBS=\"",
                },
                {
                    **recorded,
                    "CFLAGS": "-O2",
                    "CONFIG_ARGS": f"'--prefix={install.resolve()}' '--with-lto=thin' 'PANEL_LIBS=\"",
                },
            )
            for actual in probes:
                probe = Mock(
                    returncode=0,
                    stdout=json.dumps(actual) + "\n",
                    stderr="",
                )
                with self.subTest(actual=actual), patch.dict(
                    verifier.__globals__,
                    {"run_product_process": Mock(return_value=probe)},
                ), self.assertRaisesRegex(
                    driver["PackagingError"], "configuration does not match"
                ):
                    verifier(install, {"effective_flags": recorded})


class PublicationTests(unittest.TestCase):
    def test_publication_failure_restores_all_previous_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            staged_archive = root / "new.tar.gz"
            staged_manifest = root / "new.json"
            staged_archive.write_bytes(b"new archive")
            staged_manifest.write_bytes(b"new manifest")
            archive = dist / "product.tar.gz"
            manifest = dist / "artifact.json"
            archive.write_bytes(b"accepted archive")
            manifest.write_bytes(b"accepted manifest")
            original_replace = os.replace
            calls = 0

            def fail_during_publication(source, destination):
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("injected publication failure")
                return original_replace(source, destination)

            with patch("buildsys.package.os.replace", side_effect=fail_during_publication), self.assertRaises(OSError):
                publish_files({archive: staged_archive, manifest: staged_manifest})

            self.assertEqual(archive.read_bytes(), b"accepted archive")
            self.assertEqual(manifest.read_bytes(), b"accepted manifest")
            self.assertEqual(sorted(path.name for path in dist.iterdir()), [
                "artifact.json", "product.tar.gz",
            ])

    def test_publication_failure_leaves_no_partial_new_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            staged_archive = root / "new.tar.gz"
            staged_manifest = root / "new.json"
            staged_archive.write_bytes(b"new archive")
            staged_manifest.write_bytes(b"new manifest")
            original_replace = os.replace
            calls = 0

            def fail_during_publication(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected publication failure")
                return original_replace(source, destination)

            with patch("buildsys.package.os.replace", side_effect=fail_during_publication), self.assertRaises(OSError):
                publish_files({
                    dist / "product.tar.gz": staged_archive,
                    dist / "artifact.json": staged_manifest,
                })

            self.assertTrue(dist.is_dir())
            self.assertEqual(list(dist.iterdir()), [])

    def test_package_publication_leaves_comparison_outputs_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            parity = dist / "parity.json"
            benchmarks = dist / "benchmarks.json"
            parity.write_text("old parity")
            benchmarks.write_text("old benchmark")
            archive = root / "archive.tar.gz"
            manifest = root / "artifact.json"
            archive.write_bytes(b"archive")
            manifest.write_text(json.dumps({"archive": {"filename": archive.name}}))

            publish_files({dist / archive.name: archive, dist / "artifact.json": manifest})

            self.assertEqual(parity.read_text(), "old parity")
            self.assertEqual(benchmarks.read_text(), "old benchmark")
            written_manifest = json.loads((dist / "artifact.json").read_text())
            self.assertNotIn("reference", written_manifest)
            self.assertNotIn("parity", written_manifest)
            self.assertNotIn("benchmarks", written_manifest)

    def test_comparison_uses_completed_archive_and_marks_absent_reference_untested(self) -> None:
        target = target_for_triple("aarch64-unknown-linux-musl")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            install = root / "install"
            executable = install / "bin" / PRODUCT.interpreter_name
            executable.parent.mkdir(parents=True)
            executable.write_text("not executed without a reference")
            executable.chmod(0o755)
            archive_name = PRODUCT.local_archive_name(target.triple, "r2")
            staged_archive = root / archive_name
            facts = create_archive(install, staged_archive)
            manifest = build_manifest(
                product=PRODUCT,
                target=target,
                source_revision="e" * 40,
                recipe_identity="c" * 64,
                bootstrap_identity="d" * 64,
                llvm_version="22.1.3",
                archive_filename=archive_name,
                archive=facts,
                qualification={
                    "ok": True,
                    "checks": {
                        "validation.version_and_abi": {"gil_enabled": True},
                        "validation.elf_linkage": {"platform_libraries": []},
                    },
                },
                lock_path=SOURCE_LOCK,
                build_record=BUILD_RECORD,
                package_patches_directory=REPO / "patches" / "package",
            )
            staged_manifest = root / "artifact.json"
            staged_manifest.write_text(json.dumps(manifest))
            publish_generation(dist, {
                archive_name: staged_archive,
                "artifact.json": staged_manifest,
            })
            published = active_generation(dist)
            benchmarks = dist / "benchmarks.json"
            benchmarks.write_text("existing benchmark report")
            previous_manifest = (published / "artifact.json").read_bytes()
            previous_archive = (published / archive_name).read_bytes()

            report_path = compare_completed_artifact(
                dist,
                target,
                lock_path=SOURCE_LOCK,
                cache_root=root / "empty-cache",
                scratch_root=root / "scratch",
            )

            report = json.loads((dist / "parity.json").read_text())
            statuses = {row["area"]: row["status"] for row in report["rows"]}
            self.assertEqual(statuses["artifact_integrity"], "match")
            self.assertEqual(statuses["reference_comparison"], "untested")
            self.assertEqual(report["reference"]["status"], "untested")
            self.assertEqual(
                sum(status == "match" for status in statuses.values()), 1
            )
            self.assertTrue(report_path.is_file())
            self.assertEqual(benchmarks.read_text(), "existing benchmark report")
            self.assertEqual((published / "artifact.json").read_bytes(), previous_manifest)
            self.assertEqual((published / archive_name).read_bytes(), previous_archive)


if __name__ == "__main__":
    unittest.main()
