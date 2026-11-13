"""Regression for the setuptools vendored-wheel failure.

setuptools stores hashlib.sha256 on WheelFile as a class attribute and calls
it with a single argument; CPython's hashing constructors are C builtins and
never bind. Plain Python functions bind as methods, so the facade's
constructors must not be plain functions.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
FACADE = REPO / "backend" / "hashing" / "_hashlib.py"
FIXED_DIGEST_NAMES = (
    "md5",
    "sha1",
    "sha224",
    "sha256",
    "sha384",
    "sha512",
    "sha3_224",
    "sha3_256",
    "sha3_384",
    "sha3_512",
)


def _load_facade() -> ModuleType:
    if "_hashlib_graviola" not in sys.modules:
        stub = ModuleType("_hashlib_graviola")
        stub.UnsupportedDigestmodError = type("UnsupportedDigestmodError", (Exception,), {})
        sys.modules["_hashlib_graviola"] = stub
    spec = importlib.util.spec_from_file_location("backend_hashing_hashlib_facade", FACADE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACADE = _load_facade()


class OpensslConstructorBindingTests(unittest.TestCase):
    def test_openssl_constructors_do_not_bind_as_methods(self) -> None:
        for name in FIXED_DIGEST_NAMES:
            constructor = getattr(FACADE, "openssl_" + name)
            with self.subTest(name=name):
                holder = type("Holder", (), {"_algorithm": constructor})()
                self.assertEqual(
                    holder._algorithm(b"pyyaml").digest(),
                    hashlib.new(name, b"pyyaml").digest(),
                )

    def test_openssl_constructors_are_not_descriptors(self) -> None:
        for name in FIXED_DIGEST_NAMES:
            constructor = getattr(FACADE, "openssl_" + name)
            with self.subTest(name=name):
                self.assertFalse(hasattr(constructor, "__get__"))
                self.assertEqual(constructor.__name__, "openssl_" + name)


if __name__ == "__main__":
    unittest.main()
