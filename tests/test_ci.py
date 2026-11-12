from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

from verify_uv_python import verify_identity


class UvInterpreterTests(unittest.TestCase):
    def test_verifies_interpreter_identity_and_fresh_install_location(self) -> None:
        install_dir = ROOT / ".work" / "ci-test-install"
        verify_identity(
            install_dir,
            (3, 15, 1),
            (3, 15, 1),
            "linux",
            "aarch64",
            "cpython",
            "linux",
            "aarch64",
            install_dir / "bin" / "python3.15",
            install_dir,
        )

    def test_rejects_wrong_identity_version_and_paths(self) -> None:
        install_dir = ROOT / ".work" / "ci-test-install"
        arguments = (
            install_dir,
            (3, 15, 1),
            (3, 15, 1),
            "linux",
            "aarch64",
            "cpython",
            "linux",
            "aarch64",
            install_dir / "bin" / "python3.15",
            install_dir,
        )
        wrong_version = (*arguments[:2], (3, 15, 0), *arguments[3:])
        with self.assertRaisesRegex(ValueError, "Python version"):
            verify_identity(*wrong_version)
        wrong_machine = (*arguments[:4], "x86_64", *arguments[5:])
        with self.assertRaisesRegex(ValueError, "machine"):
            verify_identity(*wrong_machine)
        outside_install = (*arguments[:8], Path("/usr/bin/python3"), *arguments[9:])
        with self.assertRaisesRegex(ValueError, "escaped"):
            verify_identity(*outside_install)


class WorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    def test_all_actions_use_full_commit_pins(self) -> None:
        references = re.findall(r"^\s+uses: ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([^\s]+)", self.workflow, re.MULTILINE)
        self.assertTrue(references)
        for action, revision in references:
            with self.subTest(action=action):
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")

    def test_preflight_and_tests_run_before_builds(self) -> None:
        self.assertFalse((ROOT / ".github" / "workflows" / "build.yml").exists())
        preflight = self.workflow.index("  preflight:")
        tests = self.workflow.index("- name: Run fast controller tests")
        macos_build = self.workflow.index("  build-macos:")
        linux_build = self.workflow.index("  build-linux:")
        self.assertLess(preflight, tests)
        self.assertLess(tests, macos_build)
        self.assertLess(tests, linux_build)
        self.assertEqual(self.workflow.count("needs: [preflight, test]"), 2)
        self.assertIn("workflow_dispatch:\n\npermissions: {}", self.workflow)
        self.assertIn('"$REF" != "refs/heads/$default_branch"', self.workflow)

    def test_targets_and_release_gate_use_current_contract(self) -> None:
        self.assertIn('PYTHON_VERSION: "3.15.1"', self.workflow)
        self.assertEqual(self.workflow.count("runs-on: xcode-27"), 2)
        self.assertIn("- runs-on: ubuntu-26.04\n", self.workflow)
        self.assertIn("runs-on: ubuntu-26.04-arm", self.workflow)
        self.assertNotRegex(self.workflow, r"ubuntu-(latest|24\.04)")
        self.assertIn("aarch64-apple-darwin", self.workflow)
        self.assertIn("x86_64-unknown-linux-musl", self.workflow)
        self.assertIn("aarch64-unknown-linux-musl", self.workflow)
        self.assertIn("backend/build-no-openssl.py", self.workflow)
        self.assertIn("backend/docker-linux.py", self.workflow)
        self.assertIn("build/promote_openssl_free.py", self.workflow)
        self.assertIn('gh release create "$RELEASE_TAG" release/*', self.workflow)
        self.assertIn('--target "$GITHUB_SHA"', self.workflow)
        self.assertIn("--prerelease --verify-tag", self.workflow)
        self.assertIn('--notes-file "$RUNNER_TEMP/release-notes.md"', self.workflow)
        self.assertIn("- name: Write release notes", self.workflow)
        self.assertEqual(
            set(re.findall(r"RELEASE_TAG: (.+)", self.workflow)),
            {"release-${{ github.sha }}"},
        )
        self.assertIn("(cd release && sha256sum -c SHA256SUMS)", self.workflow)
        self.assertIn('--build "$build_id"', self.workflow)
        self.assertNotIn("GH_TOKEN", self.workflow.split("  assemble:", 1)[1].split("  release:", 1)[0])
        self.assertNotIn("3.14", self.workflow)
        self.assertNotIn("macos-26", self.workflow)
        self.assertNotIn("1.2x", self.workflow)
        self.assertNotIn("python-build-standalone size", self.workflow)
        self.assertNotIn("brew update", self.workflow)
        self.assertNotIn("brew upgrade", self.workflow)
        self.assertNotIn("openssl-free-linux-arm64", self.workflow)

    def test_linux_smoke_images_match_locked_architecture_digests(self):
        lock = json.loads((ROOT / "bootstrap.lock.json").read_text())
        for arch in ("amd64", "arm64"):
            digest = lock["linux_toolchain"]["images"][arch]["digest"]
            with self.subTest(arch=arch):
                self.assertIn(f"runtime_image: alpine@{digest}", self.workflow)

    def test_build_jobs_bind_artifacts_to_the_checked_out_revision(self):
        self.assertIn("PYTHON_BUILD_SOURCE_REVISION: ${{ github.sha }}", self.workflow)
        self.assertIn("PYTHON_BUILD_SOURCE_DIRTY", self.workflow)
        self.assertIn('--source-revision "$GITHUB_SHA"', self.workflow)
        self.assertIn('--revision "$GITHUB_SHA"', self.workflow)
        self.assertIn("build/promote_openssl_free.py", self.workflow)

    def test_uv_smokes_verify_exact_assets_and_fresh_install_paths(self) -> None:
        self.assertIn("UV_VERSION: \"0.13.4\"", self.workflow)
        self.assertIn("shasum -a 256 --check", self.workflow)
        self.assertIn("sha256sum --check SHA256SUMS", self.workflow)
        self.assertIn("UV_PYTHON_INSTALL_DIR", self.workflow)
        self.assertIn("UV_PYTHON_CACHE_DIR", self.workflow)
        self.assertIn("UV_CACHE_DIR", self.workflow)
        self.assertIn("verify_uv_python.py", self.workflow)
        self.assertNotIn("uv/install.sh", self.workflow)

    def test_uv_smokes_pin_verified_uv_hashes(self) -> None:
        expected = {
            "e0b1d4743ce64c55d9f0f4a444afa6b81125680070ee04e853fcd0f6c1ebf8f1",
            "28d4385f9f9d1d1c7654d85b3855e0e2615b2f6cc13136a2edff858bbdc16ff1",
            "253a17cadcb659ae77a0fe3b9a2f1c14297528d480c421899e61e64d64f24e8f",
        }
        for digest in expected:
            with self.subTest(digest=digest[:12]):
                self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")
                self.assertIn(digest, self.workflow)


if __name__ == "__main__":
    unittest.main()
