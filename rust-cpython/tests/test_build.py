"""Private Rust launchers must work without ambient compiler proxies."""

from __future__ import annotations

import hashlib
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


class JitConfigurationTests(unittest.TestCase):
    toolchain = SimpleNamespace(llvm_version="23.1.2", llvm_prefix=Path("/opt/llvm/23.1.2"))

    def test_both_flavors_use_the_locked_llvm(self) -> None:
        enabled = perf.lb.jit_configuration(self.toolchain, jit=True)
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.argument, "--enable-experimental-jit=yes")
        self.assertEqual(enabled.environment, {
            "LLVM_VERSION": "23", "LLVM_TOOLS_INSTALL_DIR": "/opt/llvm/23.1.2"})
        self.assertEqual(enabled.llvm_version, "23.1.2")
        disabled = perf.lb.jit_configuration(self.toolchain, jit=False)
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.argument, "--enable-experimental-jit=no")
        self.assertEqual(disabled.environment["LLVM_VERSION"], "23")

    def test_configuration_applies_the_jit_argument_and_environment(self) -> None:
        target = SimpleNamespace(triple="x86_64-unknown-linux-gnu")
        with mock.patch.object(perf.lb, "_environment", return_value={}), \
                mock.patch.object(perf.lb, "_platform_flags", return_value={"CFLAGS": "-O2"}):
            enabled_args, enabled_env = perf.lb._configuration(self.toolchain, target, jit=True)
            disabled_args, disabled_env = perf.lb._configuration(self.toolchain, target, jit=False)
        self.assertIn("--enable-experimental-jit=yes", enabled_args)
        self.assertIn("--enable-experimental-jit=no", disabled_args)
        self.assertEqual(enabled_env["LLVM_TOOLS_INSTALL_DIR"], "/opt/llvm/23.1.2")
        self.assertEqual(disabled_env["LLVM_VERSION"], "23")

    def regen(self, version: str) -> tuple[Path, Path]:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        script = root / "regen-python"
        script.write_text(f'#!/bin/sh\necho "Python {version} (main) [GCC 13]"\n')
        script.chmod(0o755)
        makefile = root / "Makefile"
        makefile.write_text(f"PYTHON_FOR_REGEN={script}\n")
        return makefile, script

    def test_python_for_regen_rejects_a_pre_311_host(self) -> None:
        makefile, _ = self.regen("3.10.14")
        with self.assertRaisesRegex(perf.lb.LaneError, "older than 3.11"):
            perf.lb._python_for_regen_version(makefile, {})
        makefile, _ = self.regen("3.12.3")
        self.assertEqual(perf.lb._python_for_regen_version(makefile, {}), "3.12.3")

    def test_jit_command_must_select_the_locked_llvm(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        makefile = root / "Makefile"
        makefile.write_text(
            "REGEN_JIT_COMMAND=python Tools/jit/build.py --llvm-version=\"23\" "
            "--llvm-tools-install-dir=\"/opt/llvm/23.1.2\"\n")
        perf.lb._check_jit_command(makefile, self.toolchain)
        makefile.write_text(
            "REGEN_JIT_COMMAND=python Tools/jit/build.py --llvm-version=\"21\" "
            "--llvm-tools-install-dir=\"/opt/llvm/23.1.2\"\n")
        with self.assertRaisesRegex(perf.lb.LaneError, "selects LLVM 21"):
            perf.lb._check_jit_command(makefile, self.toolchain)
        makefile.write_text(
            "REGEN_JIT_COMMAND=python Tools/jit/build.py --llvm-version=\"23\" "
            "--llvm-tools-install-dir=\"/usr/lib/llvm\"\n")
        with self.assertRaisesRegex(perf.lb.LaneError, "locked LLVM"):
            perf.lb._check_jit_command(makefile, self.toolchain)

    def test_stencil_clang_must_be_the_locked_binary(self) -> None:
        self.assertEqual(
            perf.lb.stencil_clangs("ok /opt/llvm/23.1.2/bin/clang -c\n"),
            {"/opt/llvm/23.1.2/bin/clang"})
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        log = root / "build.log"
        log.write_text("clang -c fixture.c\n")
        perf.lb._check_jit_logs((log,), self.toolchain)
        log.write_text("/usr/bin/clang-18 -c fixture.c\n")
        with self.assertRaisesRegex(perf.lb.LaneError, "stencil clang"):
            perf.lb._check_jit_logs((log,), self.toolchain)

    def test_activation_requirements_fail_loudly(self) -> None:
        perf.lb._require_jit_state(
            {"available": True, "enabled": True, "active": True}, jit=True)
        perf.lb._require_jit_state(
            {"available": False, "enabled": False, "active": False}, jit=False)
        with self.assertRaisesRegex(perf.lb.LaneError, "available and enabled"):
            perf.lb._require_jit_state(
                {"available": False, "enabled": False, "active": False}, jit=True)
        with self.assertRaisesRegex(perf.lb.LaneError, "did not become active"):
            perf.lb._require_jit_state(
                {"available": True, "enabled": True, "active": False}, jit=True)
        with self.assertRaisesRegex(perf.lb.LaneError, "no-JIT stage"):
            perf.lb._require_jit_state(
                {"available": True, "enabled": False, "active": False}, jit=False)

    def evidence(self, jit: bool, state: str,
                 *, header: bool = True) -> tuple[perf.lb.JitEvidence, Path]:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        build_dir = root / "build"
        build_dir.mkdir()
        script = root / "regen-python"
        script.write_text('#!/bin/sh\necho "Python 3.12.3 (main) [GCC 13]"\n')
        script.chmod(0o755)
        python = root / "python"
        python.write_text(f'#!/bin/sh\necho \'{state}\'\n')
        python.chmod(0o755)
        (build_dir / "Makefile").write_text(
            f"PYTHON_FOR_REGEN={script}\n"
            "REGEN_JIT_COMMAND=python Tools/jit/build.py --llvm-version=\"23\" "
            "--llvm-tools-install-dir=\"/opt/llvm/23.1.2\"\n")
        if header:
            (build_dir / "jit_stencils-x86_64-unknown-linux-gnu.h").write_bytes(b"stencils\n")
        log = root / "build.log"
        log.write_text("/opt/llvm/23.1.2/bin/clang -c fixture.c\n")
        evidence = perf.lb.jit_build_evidence(
            build_dir=build_dir, python=python, env={}, toolchain=self.toolchain,
            target_triple="x86_64-unknown-linux-gnu", jit=jit, logs=(log,))
        return evidence, build_dir

    def test_evidence_records_the_stencil_digest_and_regen_version(self) -> None:
        state = '{"available": true, "enabled": true, "active": true}'
        evidence, build_dir = self.evidence(True, state)
        self.assertTrue(evidence.enabled)
        self.assertEqual(evidence.llvm_version, "23.1.2")
        self.assertEqual(evidence.stencils_sha256, hashlib.sha256(b"stencils\n").hexdigest())
        self.assertEqual(evidence.python_for_regen, "3.12.3")
        disabled, _ = self.evidence(
            False, '{"available": false, "enabled": false, "active": false}', header=False)
        self.assertFalse(disabled.enabled)
        self.assertIsNone(disabled.llvm_version)
        self.assertIsNone(disabled.stencils_sha256)

    def test_missing_stencil_header_fails_the_build(self) -> None:
        state = '{"available": true, "enabled": true, "active": true}'
        with self.assertRaisesRegex(perf.lb.LaneError, "did not generate"):
            self.evidence(True, state, header=False)

    def test_inactive_jit_and_available_no_jit_fail_the_build(self) -> None:
        with self.assertRaisesRegex(perf.lb.LaneError, "did not become active"):
            self.evidence(True, '{"available": true, "enabled": true, "active": false}')
        with self.assertRaisesRegex(perf.lb.LaneError, "no-JIT stage"):
            self.evidence(False, '{"available": true, "enabled": false, "active": false}',
                          header=False)


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

    def test_already_shared_ssl_rs_is_left_in_place(self) -> None:
        contents = (
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
            contents,
        )


class CargoLockTests(unittest.TestCase):
    def test_assembled_lock_must_match_the_source_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            lock = source / "Cargo.lock"
            lock.write_text("locked")
            metadata = {"cargo_lock_sha256": hashlib.sha256(b"locked").hexdigest()}
            with mock.patch.object(perf.lb, "_read_lock", return_value=(metadata, None)):
                perf.lb._verify_cargo_lock(source)
                lock.write_text("changed")
                with self.assertRaisesRegex(perf.lb.LaneError, "digest disagrees"):
                    perf.lb._verify_cargo_lock(source)

    def test_assembled_lock_is_required(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(perf.lb, "_read_lock", return_value=({}, None)), \
                self.assertRaisesRegex(perf.lb.LaneError, "no Cargo.lock"):
            perf.lb._verify_cargo_lock(Path(temporary))


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
