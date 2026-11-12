"""Toolchain lock and macOS toolchain construction.

A lock that is never checked against the machine is a comment, so these tests
cover both halves: the lock parses, and a lock that disagrees with the host is
reported rather than silently used.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buildsys.bootstrap import (
    GCC_TOOLCHAIN_PACKAGE,
    BootstrapError,
    XcodeFacts,
    _resolve_host_tool,
    _resolve_xcode_facts,
    load_linux_bootstrap,
    load_macos_toolchain,
    problems,
    toolchain_for,
)
from buildsys.targets import target_for_triple

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "bootstrap.lock.json"


class LockParsingTests(unittest.TestCase):
    def test_repository_lock_parses(self) -> None:
        toolchain = load_macos_toolchain(LOCK)
        self.assertTrue(toolchain.llvm_version)
        self.assertEqual(toolchain.llvm_version, "23.1.4")
        self.assertEqual(toolchain.llvm_input().target, "aarch64-apple-darwin")
        self.assertEqual(toolchain.llvm_input().role, "build-source")
        self.assertEqual(toolchain.llvm_input().license, "Apache-2.0 WITH LLVM-exception")
        self.assertEqual(
            toolchain.llvm_attestation_input().sha256,
            toolchain.llvm_attestation_sha256,
        )
        self.assertTrue(toolchain.deployment_target)
        self.assertEqual(toolchain.host_floor, "27.0")
        self.assertEqual(toolchain.deployment_target, "27.0")
        self.assertEqual(toolchain.xcode_major, 27)
        self.assertTrue(toolchain.cpu_baseline)
        lock = json.loads(LOCK.read_text())
        crypto = lock["macos_toolchain"]["llvm"]["archive"]["provenance"]
        self.assertTrue(crypto["cryptographic_verification"]["verified"])
        self.assertEqual(
            crypto["cryptographic_verification"]["verifier"],
            "sigstore verify github",
        )

    def test_missing_section_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            path.write_text(json.dumps({"linux_toolchain": {}}))
            with self.assertRaises(BootstrapError):
                load_macos_toolchain(path)

    def test_malformed_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            path.write_text("{not json")
            with self.assertRaises(BootstrapError):
                load_macos_toolchain(path)

    def test_missing_field_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            path.write_text(json.dumps({"macos_toolchain": {"llvm": {}}}))
            with self.assertRaises(BootstrapError):
                load_macos_toolchain(path)


class ToolchainConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        locked = load_macos_toolchain(LOCK)
        self.locked = dataclasses.replace(
            locked,
            make=Path("/usr/bin/make"),
            xcode=dataclasses.replace(
                locked.xcode,
                developer_dir=Path("/Xcode/Contents/Developer"),
                sdkroot=Path("/Xcode/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX27.0.sdk"),
                sdk_version="27.0",
            ),
        )

    def test_recipe_toolchain_addresses_the_locked_llvm(self) -> None:
        toolchain = self.locked.toolchain()
        self.assertTrue(toolchain.is_macos)
        self.assertEqual(toolchain.cc, str(self.locked.llvm_prefix / "bin" / "clang"))
        self.assertEqual(
            self.locked.llvm_profdata,
            self.locked.llvm_prefix / "bin" / "llvm-profdata",
        )
        self.assertEqual(toolchain.make, str(self.locked.make))
        self.assertEqual(toolchain.deployment_target, self.locked.deployment_target)

    def test_environment_pins_the_floor_and_sdk(self) -> None:
        env = self.locked.toolchain().env()
        self.assertEqual(env["MACOSX_DEPLOYMENT_TARGET"], self.locked.deployment_target)
        self.assertEqual(env["SDKROOT"], str(self.locked.sdkroot))

    def test_linker_is_left_to_the_clang_driver(self) -> None:
        # Naming LD is how a mismatched LLD gets paired with LLVM 23 bitcode
        # and rejected at link time.
        self.assertNotIn("LD", self.locked.toolchain().env())

    def test_ambient_dyld_variables_are_scrubbed(self) -> None:
        # An inherited DYLD_* variable would substitute a different library
        # at link or run time without any change to the recipe.
        import os
        os.environ["DYLD_INSERT_LIBRARIES"] = "/tmp/evil.dylib"
        os.environ["DYLD_LIBRARY_PATH"] = "/tmp/evil"
        try:
            env = self.locked.toolchain().env()
        finally:
            del os.environ["DYLD_INSERT_LIBRARIES"]
            del os.environ["DYLD_LIBRARY_PATH"]
        self.assertNotIn("DYLD_INSERT_LIBRARIES", env)
        self.assertNotIn("DYLD_LIBRARY_PATH", env)

    def test_flags_carry_the_deployment_floor(self) -> None:
        toolchain = self.locked.toolchain()
        self.assertIn(f"-mmacosx-version-min={self.locked.deployment_target}",
                      toolchain.cflags())
        self.assertIn(f"-mmacosx-version-min={self.locked.deployment_target}",
                      toolchain.ldflags(Path("/private")))
        self.assertIn(self.locked.cpu_baseline, toolchain.cflags())

    def test_no_elf_loader_flags_leak_into_macos_flags(self) -> None:
        toolchain = self.locked.toolchain()
        flags = toolchain.cflags() + toolchain.ldflags(Path("/private"))
        self.assertNotIn("noexecstack", flags)
        self.assertNotIn("build-id", flags)
        self.assertNotIn("FORTIFY", flags)

    def test_recipe_identity_excludes_machine_specific_paths(self) -> None:
        identity = self.locked.identity()
        serialized = json.dumps(identity)
        self.assertNotIn("/Xcode/Contents/Developer", serialized)
        self.assertNotIn("/usr/bin/make", serialized)
        self.assertNotIn("llvm_prefix", identity)
        self.assertEqual(
            identity["host_tools"]["make"]["role"], "parallel build executor"
        )
        self.assertEqual(
            identity["host_tools"]["pkgconf"]["capability"],
            "pkg-config query interface",
        )
        self.assertTrue(
            identity["llvm_archive"]["cryptographic_verification"]["verified"]
        )
        facts = self.locked.observed_facts()
        self.assertEqual(facts["developer_dir"], "/Xcode/Contents/Developer")
        self.assertEqual(facts["host_tools"]["make"]["path"], "/usr/bin/make")


class HostToolResolutionTests(unittest.TestCase):
    def test_resolves_candidate_and_observes_its_version(self) -> None:
        with patch("buildsys.bootstrap.shutil.which", return_value="/tools/gmake"), \
             patch("buildsys.bootstrap._run_output", return_value=("GNU Make 4.4", None)):
            path, version = _resolve_host_tool(("gmake", "make"))
        self.assertEqual(path, Path("/tools/gmake"))
        self.assertEqual(version, "4.4")

    def test_xcrun_resolves_selected_developer_directory_and_sdk_facts(self) -> None:
        with patch("buildsys.bootstrap._run_output", side_effect=[
            ("/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX27.0.sdk", None),
            ("27.0", None),
            ("/Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild", None),
            ("Xcode 27.1\nBuild version 27B45", None),
        ]) as run:
            facts = _resolve_xcode_facts()
        self.assertEqual(
            facts.developer_dir,
            Path("/Applications/Xcode.app/Contents/Developer"),
        )
        self.assertEqual(facts.sdk_version, "27.0")
        self.assertEqual(facts.xcode_version, "27.1")
        self.assertEqual(facts.xcode_build, "27B45")
        self.assertEqual(facts.problems, ())
        self.assertEqual(run.call_args_list[0].args[0][0], "xcrun")
        self.assertEqual(run.call_args_list[2].args[0], ["xcrun", "--find", "xcodebuild"])


def without_archive_verification():
    """Skip re-reading the multi-gigabyte LLVM archive for tests of other checks."""
    return patch("buildsys.llvm.verify_llvm_prefix_from_archive")


class LockAgainstMachineTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "locked macOS/Xcode tools are macOS-only")
    def test_repository_lock_matches_this_machine(self) -> None:
        locked = load_macos_toolchain(LOCK)
        found = problems(locked, host_floor=locked.host_floor)
        if found:
            self.skipTest("locked toolchain prerequisites are unavailable: " + "; ".join(found))
        self.assertEqual(found, [], f"toolchain lock disagrees with host: {found}")

    def test_wrong_compiler_version_is_reported(self) -> None:
        import dataclasses
        locked = load_macos_toolchain(LOCK)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            llvm_prefix = root / "llvm"
            clang = llvm_prefix / "bin" / "clang"
            clang.parent.mkdir(parents=True)
            clang.touch()
            make = root / "gmake"
            make.touch()
            sdkroot = root / "MacOSX.sdk"
            sdkroot.mkdir()
            stale = dataclasses.replace(
                locked,
                llvm_prefix=llvm_prefix,
                make=make,
                xcode=dataclasses.replace(locked.xcode, sdkroot=sdkroot),
                llvm_version="0.0.1-nonexistent",
            )
            with patch("buildsys.bootstrap._tool_output", side_effect=[
                f"clang version {locked.llvm_version}",
                str(locked.llvm_resource_dir),
                f"GNU Make {locked.make_minimum_version}",
            ]), patch("buildsys.bootstrap.sdk_version", return_value=locked.sdk_minimum_version):
                found = problems(stale)
        self.assertTrue(any("0.0.1-nonexistent" in problem for problem in found))

    def test_missing_sdk_is_reported(self) -> None:
        import dataclasses
        locked = load_macos_toolchain(LOCK)
        broken = dataclasses.replace(
            locked,
            xcode=dataclasses.replace(
                locked.xcode, sdkroot=Path("/nonexistent/SDK")
            ),
        )
        with without_archive_verification():
            found = problems(broken)
        self.assertTrue(any("SDK resolved by xcrun is unavailable" in problem for problem in found))

    def test_missing_llvm_profdata_is_reported(self) -> None:
        import dataclasses
        locked = load_macos_toolchain(LOCK)
        broken = dataclasses.replace(locked, llvm_prefix=Path("/nonexistent/llvm"))
        with without_archive_verification():
            found = problems(broken)
        self.assertTrue(any("llvm-profdata not found" in problem for problem in found))

    def test_host_below_floor_is_reported(self) -> None:
        locked = load_macos_toolchain(LOCK)
        with patch("buildsys.bootstrap.platform.mac_ver", return_value=("26.0", "", "")), \
                without_archive_verification():
            found = problems(locked, host_floor="999.0")
        self.assertTrue(any("floor" in problem for problem in found))


class LtoSmokeGateTests(unittest.TestCase):
    """The toolchain gate that must pass before any dependency build."""

    def test_locked_toolchain_builds_and_runs_thinlto(self) -> None:
        from buildsys.bootstrap import lto_smoke_test
        locked = load_macos_toolchain(LOCK)
        if problems(locked, host_floor=locked.host_floor):
            self.skipTest("toolchain lock does not match this machine")
        with tempfile.TemporaryDirectory() as temporary:
            report = lto_smoke_test(locked.toolchain(), Path(temporary))
        self.assertEqual(report["arch"], "arm64")
        self.assertEqual(report["minos"], locked.deployment_target)
        self.assertTrue(report["runs"])


class ToolchainSelectionTests(unittest.TestCase):
    def test_linux_targets_keep_the_bare_name_toolchain(self) -> None:
        toolchain = toolchain_for(target_for_triple("x86_64-unknown-linux-musl"), LOCK)
        self.assertFalse(toolchain.is_macos)
        self.assertEqual(toolchain.cc, "clang")
        self.assertEqual(toolchain.ld, "ld.lld")
        self.assertEqual(toolchain.env()["LD"], "ld.lld")

    def test_macos_target_resolves_from_the_lock(self) -> None:
        facts = XcodeFacts(
            Path("/Xcode/Contents/Developer"),
            Path("/Xcode/Contents/Developer/SDKs/MacOSX27.0.sdk"),
            "27.0", "27.1", "27B", (),
        )
        with patch("buildsys.bootstrap.problems", return_value=[]), \
             patch("buildsys.bootstrap._resolve_host_tool", return_value=(Path("/usr/bin/make"), "4.4")), \
             patch("buildsys.bootstrap._resolve_xcode_facts", return_value=facts):
            toolchain = toolchain_for(target_for_triple("aarch64-apple-darwin"), LOCK)
        self.assertTrue(toolchain.is_macos)
        self.assertTrue(Path(toolchain.cc).is_absolute())
        self.assertEqual(toolchain.ld, "")
        self.assertNotIn("LD", toolchain.env())


class LinuxBootstrapLockTests(unittest.TestCase):
    def test_repository_lock_has_complete_exact_architecture_closures(self) -> None:
        locked = load_linux_bootstrap(LOCK)
        self.assertEqual(locked.release, "3.24.2")
        self.assertEqual(locked.images["amd64"].platform, "linux/amd64")
        self.assertEqual(locked.images["arm64"].platform, "linux/arm64")
        self.assertEqual(locked.llvm.version, "23.1.5")
        self.assertEqual(set(locked.llvm.archives), {"x86_64", "aarch64"})
        for packages in locked.apk_closure.values():
            self.assertFalse(
                [package.name for package in packages if GCC_TOOLCHAIN_PACKAGE.fullmatch(package.name)]
            )
            for package in packages:
                self.assertEqual(len(package.sha256), 64)
                self.assertGreater(package.size, 0)
                self.assertTrue(package.url.endswith(f"/{package.name}-{package.version}.apk"))

    def test_docker_base_image_digests_match_the_lock(self) -> None:
        locked = load_linux_bootstrap(LOCK)
        dockerfile = (ROOT / "Dockerfile").read_text()
        for docker_arch, image in locked.images.items():
            stage = "alpine-" + docker_arch
            self.assertIn(
                f"FROM --platform={image.platform} alpine@{image.digest} AS {stage}",
                dockerfile,
            )

    def test_build_stage_receives_bootstrap_lock(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn(
            "COPY --chown=builder:builder bootstrap.lock.json /work/bootstrap.lock.json",
            dockerfile,
        )

    def test_apk_pin_rejects_architecture_or_url_drift(self) -> None:
        lock = json.loads(LOCK.read_text())
        lock["linux_toolchain"]["apk_closure"]["aarch64"][0]["arch"] = "x86_64"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            path.write_text(json.dumps(lock))
            with self.assertRaises(BootstrapError):
                load_linux_bootstrap(path)

        lock = json.loads(LOCK.read_text())
        lock["linux_toolchain"]["apk_closure"]["x86_64"][0]["url"] = "https://example.invalid/other.apk"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            path.write_text(json.dumps(lock))
            with self.assertRaises(BootstrapError):
                load_linux_bootstrap(path)

    def test_gcc_or_alpine_llvm_toolchain_packages_are_rejected(self) -> None:
        for name in ("gcc", "g++", "libgcc-static", "libstdc++-dev", "binutils", "build-base",
                     "clang23", "llvm23-libs", "lld23", "compiler-rt"):
            with self.subTest(name=name):
                lock = json.loads(LOCK.read_text())
                package = dict(lock["linux_toolchain"]["apk_closure"]["x86_64"][0])
                package["name"] = name
                package["url"] = package["url"].rsplit("/", 1)[0] + f"/{name}-{package['version']}.apk"
                lock["linux_toolchain"]["apk_closure"]["x86_64"].append(package)
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "lock.json"
                    path.write_text(json.dumps(lock))
                    with self.assertRaisesRegex(BootstrapError, "GCC or Alpine LLVM"):
                        load_linux_bootstrap(path)

    def test_gcc_runtime_libraries_needed_by_host_tools_are_allowed(self) -> None:
        for name in ("libgcc", "libstdc++"):
            self.assertIsNone(GCC_TOOLCHAIN_PACKAGE.fullmatch(name))

    def test_linux_llvm_must_be_llvm_23(self) -> None:
        lock = json.loads(LOCK.read_text())
        lock["linux_toolchain"]["llvm"]["version"] = "22.1.8"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(BootstrapError, "LLVM 23"):
                load_linux_bootstrap(path)


if __name__ == "__main__":
    unittest.main()
