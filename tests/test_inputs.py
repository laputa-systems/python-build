import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from buildsys.inputs import (
    Cache,
    Input,
    InputError,
    identity,
    load_lock,
    mirror_url,
    safe_extract,
)


def make_input(**overrides):
    fields = {
        "name": "sample",
        "version": "1.0",
        "url": "https://example.invalid/sample.tar.gz",
        "sha256": hashlib.sha256(b"payload").hexdigest(),
        "role": "source",
    }
    fields.update(overrides)
    return Input(**fields)


class LockTests(unittest.TestCase):
    def write_lock(self, root: Path, document) -> Path:
        path = root / "lock.json"
        path.write_text(json.dumps({"schema_version": 1, **document}))
        return path

    def test_bdb_metadata_is_version_specific(self):
        inputs = {x.name: x for x in load_lock(
            Path(__file__).resolve().parents[1] / "sources.lock.json"
        )}
        self.assertEqual(inputs["bdb"].version, "6.0.19")
        self.assertEqual(inputs["bdb"].license, "Sleepycat")

    def test_invalid_field_types_and_digest_rejected(self):
        for fields in ({"sha256": "g" * 64}, {"sha256": None},
                       {"name": []}, {"size": True}, {"size": -1},
                       {"version": None}, {"target": 42}):
            with self.subTest(fields=fields), self.assertRaises(InputError):
                make_input(**fields)

    def test_valid_lock_round_trips(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_lock(
                root,
                {
                    "inputs": [
                        {
                            "name": "sample",
                            "version": "1.0",
                            "url": "https://example.invalid/sample.tar.gz",
                            "sha256": "a" * 64,
                            "role": "source",
                        }
                    ]
                },
            )
            inputs = load_lock(path)
            self.assertEqual(len(inputs), 1)
            self.assertEqual(inputs[0].name, "sample")

    def test_missing_or_unsupported_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            for document in ({"inputs": []}, {"schema_version": 2, "inputs": []}):
                with self.subTest(document=document):
                    path.write_text(json.dumps(document))
                    with self.assertRaisesRegex(InputError, "schema_version"):
                        load_lock(path)

    def test_malformed_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bad = root / "bad.json"
            bad.write_text("{not json")
            with self.assertRaises(InputError):
                load_lock(bad)
            path = self.write_lock(root, {"inputs": {"not": "a list"}})
            with self.assertRaises(InputError):
                load_lock(path)

    def test_missing_field_and_unknown_role_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_lock(root, {"inputs": [{"name": "x", "version": "1"}]})
            with self.assertRaises(InputError):
                load_lock(path)
            path = self.write_lock(
                root,
                {
                    "inputs": [
                        {
                            "name": "x",
                            "version": "1",
                            "url": "u",
                            "sha256": "a" * 64,
                            "role": "mystery",
                        }
                    ]
                },
            )
            with self.assertRaises(InputError):
                load_lock(path)

    def test_duplicate_names_rejected(self):
        entry = {
            "name": "dup",
            "version": "1",
            "url": "u",
            "sha256": "a" * 64,
            "role": "source",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_lock(Path(temporary), {"inputs": [entry, entry]})
            with self.assertRaises(InputError):
                load_lock(path)

    def test_bad_digest_format_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_lock(
                root,
                {
                    "inputs": [
                        {
                            "name": "x",
                            "version": "1",
                            "url": "u",
                            "sha256": "NOTAHASH",
                            "role": "source",
                        }
                    ]
                },
            )
            with self.assertRaises(InputError):
                load_lock(path)


class IdentityTests(unittest.TestCase):
    def test_identity_is_stable_and_canonical(self):
        first = identity(b"abc")
        again = identity(b"abc")
        self.assertEqual(first, again)
        self.assertEqual(len(first), 64)
        import json as json_module

        reordered = json_module.dumps({"a": 2, "b": 1}, separators=(",", ":"))
        canonical = json_module.dumps({"b": 1, "a": 2}, sort_keys=True, separators=(",", ":"))
        self.assertEqual(identity(reordered.encode()), identity(canonical.encode()))


class CacheTests(unittest.TestCase):
    def test_interrupted_publication_leaves_no_object_or_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            incoming = root / "incoming"
            incoming.write_bytes(b"payload")
            cache = Cache(root / "cache")
            with patch("buildsys.inputs.os.replace", side_effect=OSError("interrupted")), self.assertRaises(OSError):
                cache.store(incoming, make_input())
            self.assertEqual(list((cache.root / "objects").iterdir()), [])

    def test_concurrent_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            incoming = root / "incoming"
            incoming.write_bytes(b"payload")
            cache = Cache(root / "cache")
            with ThreadPoolExecutor(max_workers=8) as pool:
                paths = list(pool.map(lambda _: cache.store(incoming, make_input()), range(32)))
            self.assertTrue(all(path.read_bytes() == b"payload" for path in paths))
            self.assertEqual(len(list((cache.root / "objects").iterdir())), 1)

    def test_require_missing_object_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Cache(Path(temporary))
            with self.assertRaises(InputError):
                cache.require(make_input())

    def test_store_then_require_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = Cache(root)
            blob = root / "incoming.bin"
            blob.write_bytes(b"payload")
            stored = cache.store(blob, make_input())
            self.assertEqual(stored.read_bytes(), b"payload")
            self.assertEqual(cache.require(make_input()), stored)

    def test_tampered_cache_object_detected_on_require(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = Cache(root)
            blob = root / "incoming.bin"
            blob.write_bytes(b"payload")
            stored = cache.store(blob, make_input())
            stored.write_bytes(b"tampered")
            with self.assertRaises(InputError):
                cache.require(make_input())

    def test_fetch_replaces_tampered_cache_object_after_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = Cache(root / "cache")
            incoming = root / "incoming.bin"
            incoming.write_bytes(b"payload")
            stored = cache.store(incoming, make_input())
            stored.write_bytes(b"corrupt")

            with patch(
                "buildsys.inputs.urllib.request.urlopen",
                return_value=io.BytesIO(b"payload"),
            ):
                repaired = cache.fetch(make_input())

            self.assertEqual(repaired.read_bytes(), b"payload")
            self.assertEqual(cache.require(make_input()), repaired)

    def test_fetch_does_not_publish_unverified_replacement_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = Cache(root / "cache")
            incoming = root / "incoming.bin"
            incoming.write_bytes(b"payload")
            stored = cache.store(incoming, make_input())
            stored.write_bytes(b"corrupt")

            with patch(
                "buildsys.inputs.urllib.request.urlopen",
                return_value=io.BytesIO(b"wrong payload"),
            ), self.assertRaises(InputError):
                cache.fetch(make_input())

            self.assertEqual(stored.read_bytes(), b"corrupt")
            with self.assertRaises(InputError):
                cache.require(make_input())

    def test_fetch_prefers_the_content_addressed_mirror(self):
        requested = []

        def urlopen(request, timeout):
            requested.append(request.full_url)
            return io.BytesIO(b"payload")

        with tempfile.TemporaryDirectory() as temporary:
            cache = Cache(Path(temporary))
            with patch("buildsys.inputs.urllib.request.urlopen", side_effect=urlopen):
                fetched = cache.fetch(make_input())

        self.assertEqual(requested, [mirror_url(make_input().sha256)])
        self.assertEqual(fetched.name, f"{make_input().sha256}.blob")

    def test_fetch_falls_back_to_upstream_when_the_mirror_lacks_the_object(self):
        requested = []

        def urlopen(request, timeout):
            requested.append(request.full_url)
            if request.full_url == mirror_url(make_input().sha256):
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            return io.BytesIO(b"payload")

        with tempfile.TemporaryDirectory() as temporary:
            cache = Cache(Path(temporary))
            with patch("buildsys.inputs.urllib.request.urlopen", side_effect=urlopen):
                fetched = cache.fetch(make_input())
            self.assertEqual(fetched.read_bytes(), b"payload")

        self.assertEqual(requested, [mirror_url(make_input().sha256), make_input().url])

    def test_fetch_rejects_wrong_mirror_bytes_without_falling_back(self):
        requested = []

        def urlopen(request, timeout):
            requested.append(request.full_url)
            return io.BytesIO(b"tampered mirror object")

        with tempfile.TemporaryDirectory() as temporary:
            cache = Cache(Path(temporary))
            with patch(
                "buildsys.inputs.urllib.request.urlopen", side_effect=urlopen
            ), self.assertRaisesRegex(InputError, "digest"):
                cache.fetch(make_input())

        self.assertEqual(requested, [mirror_url(make_input().sha256)])

    def test_fetch_reports_every_unreachable_source(self):
        def urlopen(request, timeout):
            raise urllib.error.URLError("unreachable")

        with tempfile.TemporaryDirectory() as temporary:
            cache = Cache(Path(temporary))
            with patch(
                "buildsys.inputs.urllib.request.urlopen", side_effect=urlopen
            ), self.assertRaises(InputError) as raised:
                cache.fetch(make_input())

        self.assertIn(mirror_url(make_input().sha256), str(raised.exception))
        self.assertIn(make_input().url, str(raised.exception))

    def test_size_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = Cache(root)
            blob = root / "incoming.bin"
            blob.write_bytes(b"payload")
            with self.assertRaises(InputError):
                cache.store(blob, make_input(size=999))

    def test_wrong_digest_download_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = Cache(root)
            blob = root / "incoming.bin"
            blob.write_bytes(b"payload")
            with self.assertRaises(InputError):
                cache.store(blob, make_input(sha256="b" * 64))


class ExtractTests(unittest.TestCase):
    def make_tar(self, root: Path, name: str, members):
        archive = root / name
        with tarfile.open(archive, "w") as output:
            for info, payload in members:
                output.addfile(info, io.BytesIO(payload) if payload is not None else None)
        return archive

    def test_normal_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = tarfile.TarInfo("pkg/file.txt")
            member.size = 5
            archive = self.make_tar(root, "ok.tar", [(member, b"hello")])
            out = safe_extract(archive, root / "out")
            self.assertEqual((out / "pkg" / "file.txt").read_bytes(), b"hello")

    def test_rejects_traversal_without_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = tarfile.TarInfo("../escape")
            member.size = 1
            archive = self.make_tar(root, "bad.tar", [(member, b"x")])
            with self.assertRaises(InputError):
                safe_extract(archive, root / "out")
            self.assertFalse((root / "escape").exists())
            self.assertFalse((root / "out").exists())

    def test_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = tarfile.TarInfo("/etc/passwd")
            member.size = 1
            archive = self.make_tar(root, "bad.tar", [(member, b"x")])
            with self.assertRaises(InputError):
                safe_extract(archive, root / "out")

    def test_rejects_device_and_fifo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = tarfile.TarInfo("dev/node")
            member.type = tarfile.CHRTYPE
            archive = self.make_tar(root, "bad.tar", [(member, None)])
            with self.assertRaises(InputError):
                safe_extract(archive, root / "out")

    def test_rejects_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = tarfile.TarInfo("pkg/evil")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../../etc/passwd"
            archive = self.make_tar(root, "bad.tar", [(member, None)])
            with self.assertRaises(InputError):
                safe_extract(archive, root / "out")

    def test_existing_destination_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = tarfile.TarInfo("f")
            member.size = 1
            archive = self.make_tar(root, "ok.tar", [(member, b"x")])
            target = root / "exists"
            target.mkdir()
            with self.assertRaises(InputError):
                safe_extract(archive, target)


if __name__ == "__main__":
    unittest.main()
