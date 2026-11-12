from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from buildsys.product import Product, ProductError, load_product


class ProductTests(unittest.TestCase):
    def test_product_paths_and_tags_derive_from_version(self) -> None:
        product = Product((3, 15, 1))
        root = Path("/install")

        self.assertEqual(product.version_string, "3.15.1")
        self.assertEqual(product.major_minor, "3.15")
        self.assertEqual(product.abi_tag, "cp315")
        self.assertEqual(product.interpreter(root), root / "bin/python3.15")
        self.assertEqual(product.config_tool(root), root / "bin/python3.15-config")
        self.assertEqual(product.stdlib(root), root / "lib/python3.15")
        self.assertEqual(product.headers(root), root / "include/python3.15")
        self.assertEqual(
            product.local_archive_name("aarch64-apple-darwin", "r1"),
            "cpython-3.15.1-aarch64-apple-darwin-r1.tar.gz",
        )
        self.assertEqual(
            product.mirror_archive_name("v1", "aarch64-apple-darwin"),
            "cpython-3.15.1+v1-aarch64-apple-darwin-install_only_stripped.tar.gz",
        )
        self.assertEqual(
            product.release_archive_name("aarch64-apple-darwin"),
            "cpython-3.15.1-aarch64-apple-darwin.tar.xz",
        )
        self.assertEqual(
            product.release_manifest_name("aarch64-apple-darwin"),
            "cpython-3.15.1-aarch64-apple-darwin.manifest.json",
        )

    def test_locked_source_controls_product_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sources.lock.json"
            path.write_text(json.dumps({"schema_version": 1, "inputs": [{
                "name": "cpython",
                "version": "3.15.1",
                "url": "https://example.invalid/Python-3.15.1.tar.xz",
                "sha256": "1" * 64,
                "size": 1,
                "role": "source",
            }]}))

            self.assertEqual(load_product(path), Product((3, 15, 1)))

    def test_missing_or_nonrelease_cpython_pin_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sources.lock.json"
            path.write_text('{"schema_version": 1, "inputs": []}')
            with self.assertRaises(ProductError):
                load_product(path)
            path.write_text(json.dumps({"schema_version": 1, "inputs": [{
                "name": "cpython",
                "version": "3.15.1rc1",
                "url": "https://example.invalid/source.tar.xz",
                "sha256": "1" * 64,
                "role": "source",
            }]}))
            with self.assertRaises(ProductError):
                load_product(path)


if __name__ == "__main__":
    unittest.main()
