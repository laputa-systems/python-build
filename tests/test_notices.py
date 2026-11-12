from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from buildsys.inputs import Cache, locked_inputs
from buildsys.notices import (
    NATIVE_NOTICES,
    SOURCES_RECORD,
    NoticeError,
    bundled_native_names,
    expected_notice_paths,
    install_notices,
    verify_notices,
)
from buildsys.targets import TARGETS

REPOSITORY = Path(__file__).resolve().parents[1]
LINUX = TARGETS["aarch64-unknown-linux-musl"]
MACOS = TARGETS["aarch64-apple-darwin"]
BDB_LICENSE = b"Sleepycat license text\n"
UUID_SOURCE = b"/*\n * BSD header\n */\nint secret_code;\n"


def make_archive(name: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative, content in files.items():
            info = tarfile.TarInfo(f"{name}-src/{relative}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class NoticeFixture:
    def __init__(self, root: Path, names: tuple[str, ...]) -> None:
        self.cache_root = root / "cache"
        self.lock_path = root / "lock.json"
        entries = []
        cache = Cache(self.cache_root)
        for name in ("cpython", *names):
            files = {"LICENSE": f"{name} license\n".encode()}
            for source in NATIVE_NOTICES.get(name, ()):
                files[source.removesuffix(":header")] = (
                    UUID_SOURCE if source.endswith(":header")
                    else BDB_LICENSE if name == "bdb"
                    else f"{name} {source}\n".encode()
                )
            if name == "cpython":
                files["Doc/license.rst"] = b"third party\n"
            blob = make_archive(name, files)
            archive = root / f"{name}.tar.gz"
            archive.write_bytes(blob)
            entry = {
                "name": name,
                "version": "1.0",
                "url": f"https://example.invalid/{name}.tar.gz",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "role": "source",
                "license": "Sleepycat" if name == "bdb" else "MIT",
            }
            entries.append(entry)
        self.lock_path.write_text(json.dumps({"schema_version": 1, "inputs": entries}))
        for entry, archive in zip(locked_inputs(self.lock_path), entries):
            cache.store(root / f"{entry.name}.tar.gz", entry)

    def install(self, install: Path, target=LINUX) -> dict:
        return install_notices(
            install,
            target=target,
            cache_root=self.cache_root,
            lock_path=self.lock_path,
            source_revision="r" * 40,
            rtls_revision="a" * 40,
            rtls_source_sha256="b" * 64,
        )


class InstallNoticesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_linux_notices_are_installed_and_verified(self) -> None:
        fixture = NoticeFixture(self.root, bundled_native_names(LINUX))
        install = self.root / "install"
        fixture.install(install)
        licenses = install / "share/licenses/python-build"
        self.assertEqual(
            (licenses / "native/bdb-1.0/LICENSE").read_bytes(), BDB_LICENSE
        )
        self.assertEqual(
            (licenses / "native/libuuid-1.0/gen_uuid.c").read_text(),
            "/*\n * BSD header\n */\n",
        )
        self.assertEqual(
            (licenses / "cpython-1.0-third-party-licenses.rst").read_bytes(),
            b"third party\n",
        )
        record = (install / SOURCES_RECORD).read_text()
        self.assertIn("Berkeley DB is statically linked", record)
        self.assertIn("`" + "a" * 40 + "`", record)
        self.assertEqual(
            verify_notices(install, LINUX, fixture.lock_path)["ok"], True
        )

    def test_macos_has_no_berkeley_db_notice_or_offer(self) -> None:
        fixture = NoticeFixture(self.root, bundled_native_names(MACOS))
        install = self.root / "install"
        fixture.install(install, MACOS)
        self.assertFalse((install / "share/licenses/python-build/native/bdb-1.0").exists())
        self.assertNotIn("Berkeley DB", (install / SOURCES_RECORD).read_text())
        self.assertTrue(verify_notices(install, MACOS, fixture.lock_path)["ok"])

    def test_verification_reports_a_removed_notice(self) -> None:
        fixture = NoticeFixture(self.root, bundled_native_names(LINUX))
        install = self.root / "install"
        fixture.install(install)
        (install / "share/licenses/python-build/native/bdb-1.0/LICENSE").unlink()
        report = verify_notices(install, LINUX, fixture.lock_path)
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["missing_notices"],
            ["share/licenses/python-build/native/bdb-1.0/LICENSE"],
        )

    def test_missing_notice_file_in_source_archive_fails(self) -> None:
        names = bundled_native_names(MACOS)
        fixture = NoticeFixture(self.root, names)
        lock = json.loads(fixture.lock_path.read_text())
        cache = Cache(fixture.cache_root)
        broken = make_archive("expat", {"README": b"no license here\n"})
        entry = next(item for item in lock["inputs"] if item["name"] == "expat")
        entry["sha256"] = hashlib.sha256(broken).hexdigest()
        fixture.lock_path.write_text(json.dumps(lock))
        archive = self.root / "broken.tar.gz"
        archive.write_bytes(broken)
        cache.store(archive, next(e for e in locked_inputs(fixture.lock_path) if e.name == "expat"))
        with self.assertRaises(NoticeError):
            fixture.install(self.root / "install", MACOS)


class ShippedPolicyTests(unittest.TestCase):
    def test_every_bundled_input_has_a_notice_rule(self) -> None:
        locked = {entry.name: entry for entry in locked_inputs(REPOSITORY / "sources.lock.json")}
        for target in (LINUX, MACOS):
            for name in bundled_native_names(target):
                self.assertIn(name, NATIVE_NOTICES, name)
            self.assertTrue(expected_notice_paths(target, locked))

    def test_berkeley_db_is_linux_only(self) -> None:
        self.assertIn("bdb", bundled_native_names(LINUX))
        self.assertNotIn("bdb", bundled_native_names(MACOS))


if __name__ == "__main__":
    unittest.main()
