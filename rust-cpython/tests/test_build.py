"""Private Rust launchers must work without ambient compiler proxies."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

LANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LANE))

import perf


class RustLauncherTests(unittest.TestCase):
    def test_cargo_can_find_the_pinned_rustc_on_the_private_path(self):
        with tempfile.TemporaryDirectory(prefix="rust launcher ") as temporary:
            root = Path(temporary)
            rustup = root / "rustup"
            rustup.write_text(
                '#!/bin/sh\n'
                'test "$1" = run || exit 10\n'
                f'test "$2" = {perf.lb.RUST_CHANNEL} || exit 11\n'
                'case "$3" in\n'
                'cargo) printf "cargo\\n"; rustc -V ;;\n'
                'rustc) printf "pinned-rustc\\n" ;;\n'
                '*) exit 12 ;;\n'
                'esac\n'
            )
            rustup.chmod(0o755)
            cargo_home = root / "cargo-home"
            with mock.patch.object(perf.lb, "CARGO_HOME", cargo_home):
                cargo = perf.lb._rust_tool_wrappers(str(rustup))
            env = dict(os.environ, PATH=str(cargo_home / "bin"))
            result = subprocess.run([str(cargo), "-V"], env=env,
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "cargo\npinned-rustc\n")


class CompressionEngineEnvironmentTests(unittest.TestCase):
    def test_configure_inputs_name_each_engine_archive_and_its_own_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packages = []
            for engine in perf.lb.COMPRESSION_ENGINES:
                manifest = root / "registry" / engine.engine_crate / "Cargo.toml"
                (manifest.parent / "include").mkdir(parents=True)
                packages.append({"name": engine.engine_crate, "manifest_path": str(manifest)})
            metadata = SimpleNamespace(returncode=0, stdout=json.dumps({"packages": packages}), stderr="")
            def build(argv, **kwargs):
                for engine in perf.lb.COMPRESSION_ENGINES:
                    archive = root / "target" / "release" / engine.archive
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    archive.write_text("")
            with mock.patch.object(perf.lb, "IS_LINUX", False), \
                    mock.patch.object(perf.lb, "_require_command", side_effect=build) as command, \
                    mock.patch.object(perf.lb.subprocess, "run", return_value=metadata):
                variables = perf.lb.compression_engine_environment(
                    root, {"PATH": "p"}, target_dir=root / "target", profile="release",
                    log=root / "log")
            argv = command.call_args.args[0]
            self.assertEqual([argv[index + 1] for index, item in enumerate(argv) if item == "--package"],
                             ["cpython-libz-rs", "cpython-libbz2-rs"])
            self.assertEqual(command.call_args.kwargs["env"]["CARGO_TARGET_DIR"], str(root / "target"))
            self.assertEqual(variables, {
                "ZLIB_CFLAGS": f"-I{root / 'registry/libz-rs-sys/include'}",
                "ZLIB_LIBS": f"-L{root / 'target/release'} -Wl,-hidden-lcpython_libz_rs -Wl,-dead_strip",
                "BZIP2_CFLAGS": f"-I{root / 'registry/libbz2-rs-sys/include'}",
                "BZIP2_LIBS": f"-L{root / 'target/release'} -Wl,-hidden-lcpython_libbz2_rs -Wl,-dead_strip",
            })

    def test_linux_configure_inputs_hide_the_engine_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packages = []
            for engine in perf.lb.COMPRESSION_ENGINES:
                manifest = root / "registry" / engine.engine_crate / "Cargo.toml"
                (manifest.parent / "include").mkdir(parents=True)
                packages.append({"name": engine.engine_crate, "manifest_path": str(manifest)})
            metadata = SimpleNamespace(returncode=0, stdout=json.dumps({"packages": packages}), stderr="")
            def build(argv, **kwargs):
                for engine in perf.lb.COMPRESSION_ENGINES:
                    archive = root / "target" / "release" / engine.archive
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    archive.write_text("")
            with mock.patch.object(perf.lb, "IS_LINUX", True), \
                    mock.patch.object(perf.lb, "_require_command", side_effect=build), \
                    mock.patch.object(perf.lb.subprocess, "run", return_value=metadata):
                variables = perf.lb.compression_engine_environment(
                    root, {"PATH": "p"}, target_dir=root / "target", profile="release",
                    log=root / "log")
            self.assertEqual(variables, {
                "ZLIB_CFLAGS": f"-I{root / 'registry/libz-rs-sys/include'}",
                "ZLIB_LIBS": f"-L{root / 'target/release'} -Wl,--exclude-libs,ALL "
                             "-Wl,--gc-sections -lcpython_libz_rs",
                "BZIP2_CFLAGS": f"-I{root / 'registry/libbz2-rs-sys/include'}",
                "BZIP2_LIBS": f"-L{root / 'target/release'} -Wl,--exclude-libs,ALL "
                              "-Wl,--gc-sections -lcpython_libbz2_rs",
            })

    def test_missing_archive_fails_loudly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(perf.lb, "_require_command"), \
                    self.assertRaisesRegex(perf.LaneError, "archive was not built"):
                perf.lb.compression_engine_environment(
                    root, {}, target_dir=root / "target", profile="dev", log=root / "log")

    def test_only_cargo_profiles_are_accepted(self):
        with self.assertRaisesRegex(perf.LaneError, "dev or release"):
            perf.lb.compression_engine_environment(
                Path("."), {}, target_dir=Path("."), profile="bench", log=Path("log"))


class LinuxToolchainProvenanceTests(unittest.TestCase):
    def test_workflow_path_is_loaded_separately_from_builder_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            toolchain = perf.lb.lane_linux.load_linux_toolchain(
                LANE / "linux-toolchain.lock.json", Path(temporary)
            )
        self.assertEqual(
            toolchain.llvm_workflow,
            "https://github.com/llvm/llvm-project/.github/workflows/release-binaries.yml@refs/tags/llvmorg-23.1.2",
        )
        self.assertEqual(
            toolchain.llvm_workflow_path,
            ".github/workflows/release-tasks.yml",
        )
        with mock.patch.dict(os.environ, {"LIBCLANG_PATH": "/ambient/libclang"}):
            self.assertEqual(
                toolchain.env()["LIBCLANG_PATH"],
                "/usr/lib/llvm-18/lib",
            )


class LinuxLLVMRuntimeTests(unittest.TestCase):
    def test_icu_runtime_is_provisioned_after_llvm_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "llvm"
            toolchain = SimpleNamespace(
                llvm_input=lambda: "archive",
                llvm_attestation_input=lambda: "attestation",
                llvm_archive_url="https://example.invalid/llvm.tar.xz",
                llvm_archive_sha256="archive-sha256",
                llvm_release_tag="llvmorg-23.1.2",
                llvm_source_commit="source-commit",
                llvm_workflow="workflow-id",
                llvm_workflow_path="workflow-path",
                llvm_prefix=prefix,
                llvm_archive_root="llvm-root",
                llvm_archive_size=1,
                llvm_version="23.1.2",
                icu_input=lambda: "icu",
            )
            events = []
            cache = mock.Mock()
            cache.require.side_effect = lambda item: item
            with mock.patch.object(perf.lb, "IS_LINUX", True), \
                    mock.patch.object(perf.lb, "CACHE_ROOT", Path(temporary)), \
                    mock.patch.object(perf.lb, "Cache", return_value=cache), \
                    mock.patch.object(perf.lb, "verify_llvm_attestation_metadata"), \
                    mock.patch.object(
                        perf.lb,
                        "extract_llvm_archive",
                        side_effect=lambda *args, **kwargs: events.append("extract") or prefix,
                    ), \
                    mock.patch.object(
                        perf.lb.lane_linux,
                        "provision_icu_runtime",
                        side_effect=lambda *args: events.append("icu"),
                    ):
                result = perf.lb._llvm_ready(toolchain)
        self.assertEqual(result, prefix)
        self.assertEqual(events, ["extract", "icu"])


class LinuxSetupTests(unittest.TestCase):
    def test_ssl_rs_is_shared_in_the_generated_linux_setup(self):
        contents = (
            "*shared*\n"
            "_bz2 _bz2module.c\n"
            "\n"
            "*static*\n"
            "_random_rs\n"
            "_ssl_rs\n"
            "_tempfile_rs\n"
            "\n"
            "*disabled*\n"
            "_ssl\n"
        )
        expected = (
            "*shared*\n"
            "_bz2 _bz2module.c\n"
            "_ssl_rs _ssl_rs/Cargo.toml _ssl_rs/src/lib.rs\n"
            "\n"
            "*static*\n"
            "_random_rs\n"
            "_tempfile_rs\n"
            "\n"
            "*disabled*\n"
            "_ssl\n"
        )
        self.assertEqual(
            perf.lb.lane_linux.setup_local_with_shared_ssl_rs(contents),
            expected,
        )


class MacOSDependencyProbeTests(unittest.TestCase):
    def test_mpdecimal_metadata_is_visible_to_configure(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            pkgconfig = prefix / "lib" / "pkgconfig"
            pkgconfig.mkdir(parents=True)
            def command(argv):
                if argv == ["brew", "--prefix", "mpdecimal"]:
                    return {"returncode": 0, "output": str(prefix)}
                return {"returncode": 1, "output": ""}
            with mock.patch.object(perf.lb.shutil, "which", return_value="brew"), \
                    mock.patch.object(perf.lb, "_command", side_effect=command):
                found = perf.lb._brew_pkg_config_path().split(":")
            self.assertIn(str(pkgconfig), found)


class MacOSDeploymentProbeTests(unittest.TestCase):
    def test_new_sdk_posix_functions_are_disabled_below_their_introduction(self):
        toolchain = SimpleNamespace(deployment_target="26.0", sdkroot=Path("/SDK"))
        target = SimpleNamespace(cpu_baseline_cflag="-mcpu=apple-m1")
        with mock.patch.object(perf.lb, "IS_LINUX", False), \
                mock.patch.object(perf.lb, "_brew_pkg_config_path", return_value=""):
            for flags in (perf.lb._platform_flags(toolchain, target),
                          perf._perf_flags(toolchain, target, stage=Path("/stage"))):
                self.assertEqual(flags.get("ac_cv_func_dup3"), "no")
                self.assertEqual(flags.get("ac_cv_func_pipe2"), "no")

    def test_supported_posix_functions_remain_probeable_at_their_introduction(self):
        toolchain = SimpleNamespace(deployment_target="27.0", sdkroot=Path("/SDK"))
        target = SimpleNamespace(cpu_baseline_cflag="-mcpu=apple-m1")
        with mock.patch.object(perf.lb, "IS_LINUX", False), \
                mock.patch.object(perf.lb, "_brew_pkg_config_path", return_value=""):
            for flags in (perf.lb._platform_flags(toolchain, target),
                          perf._perf_flags(toolchain, target, stage=Path("/stage"))):
                self.assertNotIn("ac_cv_func_dup3", flags)
                self.assertNotIn("ac_cv_func_pipe2", flags)


if __name__ == "__main__":
    unittest.main()
