from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from buildsys.generations import active_generation, publish_generation
from buildsys.package import build_manifest, create_archive, write_manifest
from buildsys.product import PRODUCT
from buildsys.targets import TARGETS
from buildsys.uvmirror import (
    CANONICAL_PREFIX,
    LLVM_VERSIONS,
    UvMirrorError,
    assemble,
    asset_name,
    metadata_entry,
    metadata_key,
    validate_artifact,
)

REPO = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


class NamingTests(unittest.TestCase):
    def test_asset_name_uses_locked_product_identity(self) -> None:
        self.assertEqual(
            asset_name("20261022", "aarch64-apple-darwin"),
            PRODUCT.mirror_archive_name("20261022", "aarch64-apple-darwin"),
        )

    def test_metadata_keys_use_target_table(self) -> None:
        self.assertEqual(
            metadata_key("aarch64-apple-darwin"),
            f"cpython-{PRODUCT.version_string}-darwin-aarch64-none",
        )
        self.assertEqual(
            metadata_key("x86_64-unknown-linux-musl"),
            f"cpython-{PRODUCT.version_string}-linux-x86_64-musl",
        )
        self.assertEqual(
            metadata_key("aarch64-unknown-linux-musl"),
            f"cpython-{PRODUCT.version_string}-linux-aarch64-musl",
        )

    def test_metadata_entry_encodes_build_separator(self) -> None:
        entry = metadata_entry(
            "aarch64-apple-darwin", "20261022", "https://example.invalid/dl", "0" * 64
        )
        self.assertEqual(
            entry["url"],
            "https://example.invalid/dl/20261022/"
            + asset_name("20261022", "aarch64-apple-darwin").replace("+", "%2B"),
        )
        self.assertEqual((entry["major"], entry["minor"], entry["patch"]), PRODUCT.version)


class AssembleTests(unittest.TestCase):
    def _input_tree(self, root: Path) -> tuple[Path, Path]:
        dist = root / "dist"
        for triple, target in TARGETS.items():
            directory = root / "stage" / triple
            directory.mkdir(parents=True)
            install = root / f"install-{target.machine}-{target.family}"
            (install / "bin").mkdir(parents=True)
            (install / "bin" / PRODUCT.interpreter_name).write_bytes(b"qualified product fixture")
            archive_path = directory / PRODUCT.local_archive_name(triple, "r2")
            archive = create_archive(install, archive_path)
            qualification = {
                "ok": True,
                "checks": {
                    "validation.version_and_abi": {
                        "status": "passed",
                        "gil_enabled": True,
                    },
                    "validation.elf_linkage": {
                        "status": "passed",
                        "platform_libraries": ["libc.musl-aarch64.so.1"],
                    },
                    "runtime_container": {
                        "status": "passed",
                        "execution": "native",
                    },
                },
            }
            manifest = build_manifest(
                product=PRODUCT,
                target=target,
                source_revision=REVISION,
                recipe_identity="b" * 64,
                bootstrap_identity="c" * 64,
                llvm_version=LLVM_VERSIONS[triple],
                archive_filename=archive_path.name,
                archive=archive,
                qualification=qualification,
                lock_path=REPO / "sources.lock.json",
                build_record={
                    "source": {"name": "cpython", "version": PRODUCT.version_string, "sha256": "d" * 64},
                    "patches": [],
                    "dependencies": [],
                    "toolchain_observed": {
                        "commands": {
                            "cc": {
                                "path": "/usr/bin/clang",
                                "version": "clang version " + LLVM_VERSIONS[triple],
                            },
                        },
                    },
                    "optimization": {"lto": "thin", "pgo": False},
                    "effective_flags": {"CFLAGS": "-O3"},
                    "commands": {"configure": ["configure"], "build": ["make"], "install": ["make", "install"]},
                    "build_mode": "sealed",
                    "isolation": {"mechanism": "buildkit-network-none", "network": "disabled"},
                },
                package_patches_directory=REPO / "patches" / "package",
            )
            write_manifest(directory / "artifact.json", manifest)
            publish_generation(dist / triple, {path.name: path for path in directory.iterdir()})
        return dist, root / "release-budgets.json"

    def _replace_published(self, dist: Path, triple: str, name: str, content: bytes) -> None:
        published = active_generation(dist / triple)
        staged = dist.parent / "replace" / triple
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True)
        for path in published.iterdir():
            if path.name != "GENERATION.json":
                shutil.copyfile(path, staged / path.name)
        (staged / name).write_bytes(content)
        publish_generation(dist / triple, {path.name: path for path in staged.iterdir()})

    def _budgets(self, path: Path, *, compressed: int = 10_000_000) -> None:
        path.write_text(json.dumps({
            "schema_version": 1,
            "targets": {
                triple: {
                    "archive_size_bytes": compressed,
                    "payload_size_bytes": 10_000_000,
                    "rationale": "Fixture ceiling for an assembly contract test.",
                }
                for triple in TARGETS
            },
        }))

    def test_assemble_validates_manifests_and_writes_artifact_bound_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, budgets = self._input_tree(root)
            self._budgets(budgets)
            output = root / "release"

            report = assemble(
                "20261022", "owner/name", dist, output,
                expected_revision=REVISION, budgets_path=budgets,
            )

            self.assertEqual(report["source_revision"], REVISION)
            self.assertEqual(set(report["assets"]), {
                *[asset_name("20261022", triple) for triple in TARGETS],
                *[f"artifact-{triple}.json" for triple in TARGETS],
            })
            sums = (output / "SHA256SUMS").read_text()
            for triple in TARGETS:
                archive_name = asset_name("20261022", triple)
                self.assertEqual(
                    hashlib.sha256((output / archive_name).read_bytes()).hexdigest(),
                    next(line.split()[0] for line in sums.splitlines() if line.endswith(archive_name)),
                )
                artifact = json.loads((output / f"artifact-{triple}.json").read_text())
                self.assertEqual(artifact["source_revision"], REVISION)
            release = json.loads((output / "download-metadata.json").read_text())
            smoke = json.loads((output / "smoke-metadata.json").read_text())
            self.assertEqual(set(release), set(smoke))
            self.assertEqual(
                release[f"cpython-{PRODUCT.version_string}-darwin-aarch64-none"]["sha256"],
                smoke[f"cpython-{PRODUCT.version_string}-darwin-aarch64-none"]["sha256"],
            )
            self.assertTrue(
                smoke[f"cpython-{PRODUCT.version_string}-darwin-aarch64-none"]["url"].startswith(
                    CANONICAL_PREFIX
                )
            )

    def test_rejects_revision_mismatch_and_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, budgets = self._input_tree(root)
            self._budgets(budgets)
            output = root / "release"
            output.mkdir()
            accepted = output / "accepted.txt"
            accepted.write_text("accepted")

            with self.assertRaisesRegex(UvMirrorError, "does not match built revision"):
                assemble(
                    "20261022", "owner/name", dist, output,
                    expected_revision="d" * 40, budgets_path=budgets,
                )

            self.assertEqual(accepted.read_text(), "accepted")

    def test_artifact_validation_binds_exact_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, _ = self._input_tree(root)
            target_directory = active_generation(dist / "aarch64-unknown-linux-musl")
            archive, manifest = validate_artifact(
                target_directory, "aarch64-unknown-linux-musl", REVISION
            )
            self.assertEqual(archive.name, manifest["archive"]["filename"])
            with self.assertRaisesRegex(UvMirrorError, "does not match built revision"):
                validate_artifact(
                    target_directory, "aarch64-unknown-linux-musl", "b" * 40
                )

    def test_emulated_linux_runtime_does_not_qualify_for_release_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, budgets = self._input_tree(root)
            self._budgets(budgets)
            triple = "x86_64-unknown-linux-musl"
            manifest = json.loads((active_generation(dist / triple) / "artifact.json").read_text())
            manifest["qualification"]["checks"]["runtime_container"]["execution"] = "emulated"
            self._replace_published(dist, triple, "artifact.json", json.dumps(manifest).encode())
            output = root / "release"
            with self.assertRaisesRegex(UvMirrorError, "native runtime-container"):
                assemble(
                    "20261022", "owner/name", dist, output,
                    expected_revision=REVISION, budgets_path=budgets,
                )
            self.assertFalse(output.exists())

    def test_rejects_corrupt_archive_before_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, budgets = self._input_tree(root)
            self._budgets(budgets)
            triple = "x86_64-unknown-linux-musl"
            archive = next(active_generation(dist / triple).glob("*.tar.gz"))
            self._replace_published(dist, triple, archive.name, b"corrupt")

            with self.assertRaisesRegex(UvMirrorError, "digest or size"):
                assemble(
                    "20261022", "owner/name", dist, root / "release",
                    expected_revision=REVISION, budgets_path=budgets,
                )

            self.assertFalse((root / "release").exists())

    def test_rejects_missing_target_or_unreviewed_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, budgets = self._input_tree(root)
            self._budgets(budgets)
            (dist / "aarch64-unknown-linux-musl" / "ACTIVE").unlink()
            with self.assertRaisesRegex(UvMirrorError, "no complete published artifact"):
                assemble(
                    "20261022", "owner/name", dist, root / "release",
                    expected_revision=REVISION, budgets_path=budgets,
                )
            with self.assertRaisesRegex(UvMirrorError, "size budgets are missing or invalid"):
                assemble(
                    "20261022", "owner/name", dist, root / "release",
                    expected_revision=REVISION, budgets_path=root / "missing.json",
                )

    def test_budget_overrun_fails_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, budgets = self._input_tree(root)
            self._budgets(budgets, compressed=1)
            output = root / "release"
            with self.assertRaisesRegex(UvMirrorError, "compressed archive exceeds"):
                assemble(
                    "20261022", "owner/name", dist, output,
                    expected_revision=REVISION, budgets_path=budgets,
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
