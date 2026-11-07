import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRATCH = REPO / ".work" / "tmp"
SCRATCH.mkdir(parents=True, exist_ok=True)
DRIVER_PATH = REPO / "backend" / "build-no-openssl.py"
SPEC = importlib.util.spec_from_file_location("no_openssl_build_driver", DRIVER_PATH)
DRIVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DRIVER
SPEC.loader.exec_module(DRIVER)


class RtlsSourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name)
        self.source = self.run_dir / "rtls-build"
        self.inputs = self.run_dir / "inputs"
        self.source.mkdir()
        self.inputs.mkdir()
        (self.source / "src").mkdir()
        source_file = self.source / "src" / "lib.rs"
        source_file.write_text("pub fn value() -> u8 { 1 }\n")
        source_file.chmod(0o644)
        executable = self.source / "build.sh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        (self.source / "empty").mkdir()
        DRIVER.record_rtls_source_snapshot(self.run_dir)

    def test_exact_prepared_tree_passes(self):
        self.assertEqual(DRIVER.verify_rtls_source_snapshot(self.run_dir), self.source)

    def test_record_rejects_change_during_cargo_fetch(self):
        prepared_digest = DRIVER.rtls_source_digest(self.source)
        (self.source / "src" / "lib.rs").write_text("pub fn value() -> u8 { 3 }\n")
        (self.inputs / "rtls-source.sha256").unlink()
        with self.assertRaisesRegex(DRIVER.BuildError, "changed during Cargo fetch"):
            DRIVER.record_rtls_source_snapshot(
                self.run_dir,
                expected_digest=prepared_digest,
            )
        self.assertFalse((self.inputs / "rtls-source.sha256").exists())

    def test_changed_file_fails(self):
        (self.source / "src" / "lib.rs").write_text("pub fn value() -> u8 { 2 }\n")
        with self.assertRaisesRegex(DRIVER.BuildError, "changed after fetch"):
            DRIVER.verify_rtls_source_snapshot(self.run_dir)

    def test_added_file_fails(self):
        (self.source / "src" / "added.rs").write_text("pub fn added() {}\n")
        with self.assertRaisesRegex(DRIVER.BuildError, "changed after fetch"):
            DRIVER.verify_rtls_source_snapshot(self.run_dir)

    def test_removed_file_fails(self):
        (self.source / "src" / "lib.rs").unlink()
        with self.assertRaisesRegex(DRIVER.BuildError, "changed after fetch"):
            DRIVER.verify_rtls_source_snapshot(self.run_dir)

    def test_mode_change_fails(self):
        (self.source / "build.sh").chmod(0o644)
        with self.assertRaisesRegex(DRIVER.BuildError, "changed after fetch"):
            DRIVER.verify_rtls_source_snapshot(self.run_dir)

    def test_symlink_fails(self):
        outside = self.run_dir / "outside"
        outside.write_text("not part of the source tree\n")
        (self.source / "linked").symlink_to(outside)
        with self.assertRaisesRegex(DRIVER.BuildError, "contains a symlink"):
            DRIVER.verify_rtls_source_snapshot(self.run_dir)

    def test_missing_digest_fails_without_backfill(self):
        (self.inputs / "rtls-source.sha256").unlink()
        with self.assertRaisesRegex(DRIVER.BuildError, "digest is missing"):
            DRIVER.verify_rtls_source_snapshot(self.run_dir)


class RustLinkHardeningTests(unittest.TestCase):
    def test_linux_extensions_request_relro_and_eager_binding(self):
        from buildsys.targets import TARGETS

        for triple in (
            "x86_64-unknown-linux-musl",
            "aarch64-unknown-linux-musl",
        ):
            with self.subTest(triple=triple):
                flags = DRIVER.rust_flags(
                    TARGETS[triple], rtls=False, static_unwinder=Path("/unwinder")
                )
                self.assertIn("-C", flags)
                self.assertIn("link-arg=-Wl,-z,relro", flags)
                self.assertIn("link-arg=-Wl,-z,now", flags)

    def test_linux_extensions_cannot_fall_back_to_libgcc_s_unwinding(self):
        from buildsys.targets import TARGETS

        for triple in (
            "x86_64-unknown-linux-musl",
            "aarch64-unknown-linux-musl",
        ):
            with self.subTest(triple=triple), self.assertRaisesRegex(
                DRIVER.BuildError, "static LLVM unwinder"
            ):
                DRIVER.rust_flags(TARGETS[triple], rtls=False)


if __name__ == "__main__":
    unittest.main()
