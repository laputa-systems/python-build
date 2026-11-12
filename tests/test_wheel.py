import hashlib
import io
import json
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from buildsys import wheel

REPOSITORY = Path(__file__).resolve().parents[1]


def _wheel_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("buildlib/__init__.py", "MARKER = 1\n")
    return buffer.getvalue()


class PinTests(unittest.TestCase):
    def test_source_lock_uv_lock_and_pyproject_pin_the_same_wheel(self) -> None:
        lock = json.loads((REPOSITORY / "sources.lock.json").read_text())
        pin = next(entry for entry in lock["inputs"] if entry["name"] == wheel.INPUT_NAME)
        project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())
        self.assertIn(f"laputa-buildlib=={pin['version']}", project["project"]["dependencies"])
        uv = tomllib.loads((REPOSITORY / "uv.lock").read_text())
        package = next(entry for entry in uv["package"] if entry["name"] == "laputa-buildlib")
        self.assertEqual(package["version"], pin["version"])
        self.assertEqual(
            [(item["url"], item["hash"]) for item in package["wheels"]],
            [(pin["url"], f"sha256:{pin['sha256']}")],
        )


class EnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.wheel = _wheel_bytes()
        self.sha256 = hashlib.sha256(self.wheel).hexdigest()
        (self.root / "sources.lock.json").write_text(json.dumps({
            "schema_version": 1,
            "inputs": [{
                "name": wheel.INPUT_NAME,
                "version": "0.0.0",
                "url": "https://example.invalid/buildlib.whl",
                "sha256": self.sha256,
                "size": len(self.wheel),
                "role": "build-source",
            }],
        }))
        self.blob = wheel.object_path(self.root / ".cache", self.sha256)
        for patcher in (
            patch.object(wheel, "ROOT", self.root),
            patch.object(wheel.importlib.util, "find_spec", return_value=None),
            patch.object(sys, "path", list(sys.path)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        sys.path[:] = [entry for entry in sys.path if not entry.endswith(".blob")]

    def test_an_installed_buildlib_is_left_alone(self) -> None:
        with patch.object(wheel.importlib.util, "find_spec", return_value=object()):
            before = list(sys.path)
            wheel.ensure()
        self.assertEqual(sys.path, before)

    def test_a_verified_cached_wheel_goes_on_sys_path_without_network(self) -> None:
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(self.wheel)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            wheel.ensure()
        self.assertEqual(sys.path[-1], str(self.blob))

    def test_a_missing_wheel_is_downloaded_verified_and_cached(self) -> None:
        with patch("urllib.request.urlopen", side_effect=lambda url, timeout: io.BytesIO(self.wheel)):
            wheel.ensure()
        self.assertEqual(self.blob.read_bytes(), self.wheel)
        self.assertEqual(sys.path[-1], str(self.blob))

    def test_a_tampered_cached_wheel_is_replaced(self) -> None:
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(b"tampered")
        with patch("urllib.request.urlopen", side_effect=lambda url, timeout: io.BytesIO(self.wheel)):
            wheel.ensure()
        self.assertEqual(self.blob.read_bytes(), self.wheel)

    def test_wrong_bytes_are_rejected_and_not_cached(self) -> None:
        with (
            patch("urllib.request.urlopen", side_effect=lambda url, timeout: io.BytesIO(b"wrong")),
            self.assertRaises(ImportError),
        ):
            wheel.ensure()
        self.assertFalse(self.blob.exists())
        self.assertEqual(list(self.blob.parent.iterdir()), [])
        self.assertNotIn(str(self.blob), sys.path)

    def test_the_cached_wheel_imports_by_zipimport(self) -> None:
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(self.wheel)
        wheel.ensure()
        with patch.dict(sys.modules):
            sys.modules.pop("buildlib", None)
            import buildlib

            self.assertEqual(buildlib.MARKER, 1)
        self.assertTrue(buildlib.__file__.startswith(str(self.blob)))


if __name__ == "__main__":
    unittest.main()
