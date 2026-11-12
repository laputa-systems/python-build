from __future__ import annotations

import hashlib
import io
import json
import ssl
import tarfile
import tempfile
import unittest
from pathlib import Path

from buildsys.ca_bundle import (
    BUNDLE_RELATIVE_PATH,
    CABundleError,
    install_fallback_ca_bundle,
)
from buildsys.inputs import Cache, load_lock
from buildsys.product import PRODUCT


class InstallFallbackCABundleTests(unittest.TestCase):
    def _source_archive(self, root: Path) -> tuple[Path, bytes]:
        fixture = Path(__file__).parent / "fixtures" / "localhost.crt"
        archive_path = root / "certifi-test.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            pem = tarfile.TarInfo("certifi-test/certifi/cacert.pem")
            pem_bytes = fixture.read_bytes()
            pem.size = len(pem_bytes)
            archive.addfile(pem, fileobj=io.BytesIO(pem_bytes))
            license_info = tarfile.TarInfo("certifi-test/LICENSE")
            license_bytes = b"Mozilla Public License Version 2.0\n"
            license_info.size = len(license_bytes)
            archive.addfile(
                license_info, fileobj=io.BytesIO(license_bytes)
            )
        return archive_path, fixture.read_bytes()

    def _lock(self, root: Path, archive: Path, version: str = "test") -> tuple[Path, Path]:
        data = archive.read_bytes()
        entry = {
            "name": "certifi-ca",
            "version": version,
            "url": "https://example.invalid/certifi.tar.gz",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "role": "source",
            "license": "MPL-2.0",
            "purpose": "test fallback CA source",
        }
        lock = root / "sources.lock.json"
        lock.write_text(json.dumps({"schema_version": 1, "inputs": [entry]}))
        cache_root = root / "cache"
        Cache(cache_root).store(archive, load_lock(lock)[0])
        return lock, cache_root

    def test_installs_only_validated_bundle_and_license_from_locked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, pem = self._source_archive(root)
            lock, cache_root = self._lock(root, archive, version="2026.7.22")
            install = root / "python"
            PRODUCT.stdlib(install).mkdir(parents=True)

            report = install_fallback_ca_bundle(
                install, cache_root=cache_root, lock_path=lock
            )

            bundle = install / BUNDLE_RELATIVE_PATH
            self.assertEqual(bundle.read_bytes(), pem)
            self.assertTrue((install / report["license_path"]).is_file())
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=str(bundle))
            self.assertEqual(context.cert_store_stats()["x509_ca"], 1)
            self.assertEqual(report["ca_certificates"], 1)

    def test_rejects_a_cached_source_with_wrong_hash_or_license(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, _ = self._source_archive(root)
            lock, cache_root = self._lock(root, archive)
            document = json.loads(lock.read_text())
            document["inputs"][0]["sha256"] = "0" * 64
            lock.write_text(json.dumps(document))

            with self.assertRaises(CABundleError):
                install_fallback_ca_bundle(
                    root / "python", cache_root=cache_root, lock_path=lock
                )


if __name__ == "__main__":
    unittest.main()
