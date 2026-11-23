"""Private Rust launchers must work without ambient compiler proxies."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import io
from importlib.machinery import SourceFileLoader
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

LANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LANE))

import perf

_MUSL_RUN_LOADER = SourceFileLoader(
    "musl_run_review_test", str(LANE / "tools" / "musl-run"))
_MUSL_RUN_SPEC = importlib.util.spec_from_loader(
    _MUSL_RUN_LOADER.name, _MUSL_RUN_LOADER)
musl_run = importlib.util.module_from_spec(_MUSL_RUN_SPEC)
_MUSL_RUN_SPEC.loader.exec_module(musl_run)


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
        target = SimpleNamespace(triple="x86_64-unknown-linux-musl")
        with mock.patch.object(perf.lb, "_environment", return_value={}), \
                mock.patch.object(perf.lb, "_platform_flags", return_value={"CFLAGS": "-O2"}):
            enabled_args, enabled_env = perf.lb._configuration(self.toolchain, target, jit=True)
            disabled_args, disabled_env = perf.lb._configuration(self.toolchain, target, jit=False)
        self.assertIn("--enable-experimental-jit=yes", enabled_args)
        self.assertIn("--enable-experimental-jit=no", disabled_args)
        self.assertIn("--with-libs=-Wl,-l:libgcc_s.so.1 -lc", enabled_args)
        self.assertFalse(any("-lunwind" in item for item in enabled_args))
        self.assertEqual(enabled_env["LLVM_TOOLS_INSTALL_DIR"], "/opt/llvm/23.1.2")
        self.assertEqual(disabled_env["LLVM_VERSION"], "23")


    def test_allocator_archives_are_whole_archived_only_for_custom_allocators(self):
        archive = Path("/work/liballocator.a")
        self.assertEqual(perf.lb.allocator_link_flags("musl"), "")
        for allocator in ("mimalloc", "jemalloc"):
            with self.subTest(allocator=allocator):
                self.assertEqual(
                    perf.lb.allocator_link_flags(allocator, archive),
                    "-Wl,--whole-archive /work/liballocator.a -Wl,--no-whole-archive",
                )
        with self.assertRaisesRegex(perf.lb.LaneError, "invalid allocator link request"):
            perf.lb.allocator_link_flags("jemalloc")

    def test_mimalloc_internal_symbols_are_localized_without_hiding_malloc(self):
        output = (
            "00000000 T __mi_stat_increase\n"
            "00000000 T _mi_os_alloc\n"
            "00000000 T mi_malloc\n"
            "00000000 T malloc\n"
        )
        self.assertEqual(
            perf.lb._mimalloc_internal_symbols(output, ("mi_", "_mi_", "__mi_")),
            ["__mi_stat_increase", "_mi_os_alloc", "mi_malloc"],
        )

    def test_musl_layout_no_longer_requires_llvm_libunwind(self):
        self.assertNotIn("lib/libunwind.a", perf.lb.MUSL_X86_64_LAYOUT.required)
        self.assertNotIn("lib/libunwind.a", perf.lb.MUSL_X86_64_LAYOUT.library_files)
        for name in ("clang_rt.crtbegin.o", "clang_rt.crtend.o"):
            member = f"lib/clang/23/lib/linux/{name}"
            self.assertIn(member, perf.lb.MUSL_X86_64_LAYOUT.links)
        for name in (
            "clang_rt.crtbegin-x86_64.o",
            "clang_rt.crtend-x86_64.o",
        ):
            member = f"lib/clang/23/lib/linux/{name}"
            self.assertIn(member, perf.lb.MUSL_X86_64_LAYOUT.required)
            self.assertIn(member, perf.lb.MUSL_X86_64_LAYOUT.resource_files)
        for name in (
            "crtbegin.o",
            "crtbeginS.o",
            "crtbeginT.o",
            "crtend.o",
            "crtendS.o",
        ):
            member = f"lib/clang/23/lib/linux/{name}"
            self.assertIn(member, perf.lb.MUSL_X86_64_LAYOUT.links)

    def regen(self, version: str) -> tuple[Path, Path]:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        script = root / "regen-python"
        script.write_text(f'#!/bin/sh\necho "Python {version} (main) [GCC 13]"\n')
        script.chmod(0o755)
        makefile = root / "Makefile"
        makefile.write_text(f"PYTHON_FOR_REGEN?={script}\n")
        return makefile, script

    def test_python_for_regen_rejects_a_pre_311_host(self) -> None:
        makefile, _ = self.regen("3.10.14")
        with self.assertRaisesRegex(perf.lb.LaneError, "older than 3.11"):
            perf.lb._python_for_regen_version(makefile, {})
        makefile, _ = self.regen("3.12.3")
        self.assertEqual(perf.lb._python_for_regen_version(makefile, {}), "3.12.3")

    def test_jit_verification_skips_the_regen_interpreter_without_the_jit(self) -> None:
        makefile, _ = self.regen("3.9.6")
        with mock.patch.object(perf.lb, "_jit_stage_state", return_value={}), \
                mock.patch.object(perf.lb, "_require_jit_state"):
            perf.lb.verify_jit_build(
                build_dir=makefile.parent, python=Path("python"), env={},
                toolchain=self.toolchain, target_triple="x86_64-unknown-linux-musl",
                jit=False, logs=())

    def test_stencil_clang_scan_ignores_other_clang_tools(self) -> None:
        found = perf.lb.stencil_clangs(
            "/opt/llvm/bin/clang -c a.c /usr/bin/clang-format x /opt/llvm/bin/clang-23 y")
        self.assertEqual(found, {"/opt/llvm/bin/clang", "/opt/llvm/bin/clang-23"})

    def test_jit_command_must_select_the_locked_llvm(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        makefile = root / "Makefile"
        makefile.write_text(
            "\t$(PYTHON_FOR_REGEN) Tools/jit/build.py x86_64-unknown-linux-musl "
            "--llvm-version=\"23\" --llvm-tools-install-dir=\"/opt/llvm/23.1.2\"\n")
        perf.lb._check_jit_command(makefile, self.toolchain)
        makefile.write_text(
            "\t$(PYTHON_FOR_REGEN) Tools/jit/build.py x86_64-unknown-linux-musl "
            "--llvm-version=\"21\" --llvm-tools-install-dir=\"/opt/llvm/23.1.2\"\n")
        with self.assertRaisesRegex(perf.lb.LaneError, "selects LLVM 21"):
            perf.lb._check_jit_command(makefile, self.toolchain)
        makefile.write_text(
            "\t$(PYTHON_FOR_REGEN) Tools/jit/build.py x86_64-unknown-linux-musl "
            "--llvm-version=\"23\" --llvm-tools-install-dir=\"/usr/lib/llvm\"\n")
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

    def verify(self, jit: bool, state: str, *, header: bool = True) -> None:
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
            f"PYTHON_FOR_REGEN?={script}\n"
            "\t$(PYTHON_FOR_REGEN) Tools/jit/build.py x86_64-unknown-linux-musl "
            "--llvm-version=\"23\" --llvm-tools-install-dir=\"/opt/llvm/23.1.2\"\n")
        if header:
            (build_dir / "jit_stencils-x86_64-unknown-linux-musl.h").write_bytes(b"stencils\n")
        log = root / "build.log"
        log.write_text("/opt/llvm/23.1.2/bin/clang -c fixture.c\n")
        perf.lb.verify_jit_build(
            build_dir=build_dir, python=python, env={}, toolchain=self.toolchain,
            target_triple="x86_64-unknown-linux-musl", jit=jit, logs=(log,))

    def test_missing_jit_tools_are_extracted_from_the_locked_archive(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        archive = root / "llvm" / "objects" / f"{'a' * 64}.blob"
        archive.parent.mkdir(parents=True)
        with tarfile.open(archive, "w:xz") as tar:
            for name in ("LLVM-root/bin/llvm-objdump", "LLVM-root/bin/llvm-dwarfdump", "LLVM-root/bin/llvm-readobj"):
                data = b"#!/bin/sh\nexit 0\n"
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755
                tar.addfile(member, io.BytesIO(data))
        toolchain = SimpleNamespace(
            llvm_version="23.1.2", llvm_archive_root="LLVM-root",
            llvm_input=lambda: SimpleNamespace(sha256="a" * 64))
        with mock.patch.object(perf.lb, "CACHE_ROOT", root), \
                mock.patch.object(perf.lb, "WORK", root / "work"), \
                mock.patch.object(perf.lb, "IS_LINUX", False):
            directory = perf.lb._jit_llvm_tools(toolchain)
            self.assertEqual(sorted(path.name for path in directory.iterdir()),
                             ["llvm-dwarfdump", "llvm-objdump", "llvm-readobj"])
            self.assertTrue((directory / "llvm-objdump").stat().st_mode & 0o100)
            self.assertEqual(perf.lb._jit_llvm_tools(toolchain), directory)

    def test_missing_jit_tools_fail_loudly(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        toolchain = SimpleNamespace(
            llvm_version="23.1.2", llvm_archive_root="LLVM-root",
            llvm_input=lambda: SimpleNamespace(sha256="b" * 64))
        with mock.patch.object(perf.lb, "CACHE_ROOT", root), \
                mock.patch.object(perf.lb, "WORK", root / "work"), \
                self.assertRaisesRegex(perf.lb.LaneError, "archive is missing"):
            perf.lb._jit_llvm_tools(toolchain)

    def test_concurrent_jit_tool_extraction_publishes_complete_executables(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        archive = root / "llvm" / "objects" / f"{'a' * 64}.blob"
        archive.parent.mkdir(parents=True)
        contents = {name: (name.encode() + b"\n") * 128 for name in
                    ("llvm-objdump", "llvm-dwarfdump", "llvm-readobj")}
        with tarfile.open(archive, "w:xz") as tar:
            for name, data in contents.items():
                member = tarfile.TarInfo(f"LLVM-root/bin/{name}")
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        toolchain = SimpleNamespace(
            llvm_version="23.1.2", llvm_archive_root="LLVM-root",
            llvm_input=lambda: SimpleNamespace(sha256="a" * 64))
        barrier = threading.Barrier(2)
        copied = threading.local()
        copyfileobj = perf.lb.shutil.copyfileobj

        def overlap_first_copy(source, destination):
            copyfileobj(source, destination)
            if not getattr(copied, "first_done", False):
                copied.first_done = True
                barrier.wait(timeout=5)

        with mock.patch.object(perf.lb, "CACHE_ROOT", root), \
                mock.patch.object(perf.lb, "WORK", root / "work"), \
                mock.patch.object(perf.lb, "IS_LINUX", False), \
                mock.patch.object(perf.lb.shutil, "copyfileobj", side_effect=overlap_first_copy), \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(perf.lb._jit_llvm_tools, toolchain) for _ in range(2)]
            directories = [future.result(timeout=10) for future in futures]
        self.assertEqual(directories[0], directories[1])
        directory = directories[0]
        self.assertEqual(sorted(path.name for path in directory.iterdir()), sorted(contents))
        for name, data in contents.items():
            self.assertEqual((directory / name).read_bytes(), data)
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o755)

    def test_a_complete_jit_build_and_a_complete_no_jit_build_verify(self) -> None:
        self.verify(True, '{"available": true, "enabled": true, "active": true}')
        self.verify(False, '{"available": false, "enabled": false, "active": false}',
                    header=False)

    def test_missing_stencil_header_fails_the_build(self) -> None:
        state = '{"available": true, "enabled": true, "active": true}'
        with self.assertRaisesRegex(perf.lb.LaneError, "did not generate"):
            self.verify(True, state, header=False)

    def test_inactive_jit_and_available_no_jit_fail_the_build(self) -> None:
        with self.assertRaisesRegex(perf.lb.LaneError, "did not become active"):
            self.verify(True, '{"available": true, "enabled": true, "active": false}')
        with self.assertRaisesRegex(perf.lb.LaneError, "no-JIT stage"):
            self.verify(False, '{"available": true, "enabled": false, "active": false}',
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


class PreparedLinuxLLVMTests(unittest.TestCase):
    def test_second_llvm_ready_reuses_the_manifest_verified_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "llvm.tar.xz"
            archive.write_bytes(b"locked archive")
            raw_prefix = root / "llvm" / "raw"
            binary = raw_prefix / "bin"
            binary.mkdir(parents=True)

            def elf_with_gnu_stack():
                data = bytearray(0x100)
                data[:6] = b"\x7fELF\x02\x01"
                data[0x20:0x28] = (0x40).to_bytes(8, "little")
                data[0x36:0x38] = (56).to_bytes(2, "little")
                data[0x38:0x3a] = (1).to_bytes(2, "little")
                data[0x40:0x44] = (0x6474E551).to_bytes(4, "little")
                return bytes(data)

            raw_contents = elf_with_gnu_stack()
            for name in ("clang", "lld"):
                (binary / name).write_bytes(raw_contents)
            (binary / "llvm-strip").write_text("strip\n")
            (binary / "llvm-readelf").write_text("readelf\n")
            (binary / "clang++").write_text("clang++\n")
            archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
            prepared_prefix = root / "llvm" / "prepared" / "prefix"
            toolchain = perf.lb.lane_linux.MuslToolchain(
                lock_path=root / "linux-musl-toolchain.lock.json",
                target="x86_64-unknown-linux-musl",
                cpu_baseline="x86-64",
                alpine_release="3.22",
                image_digest="sha256:" + "0" * 64,
                image_revision="r0",
                repositories=(),
                package_pins=(),
                lane_packages=(),
                lane_apk_cache=".cache/bootstrap/lane-musl/apk/amd64",
                system_package_roots=(),
                llvm_raw_prefix=raw_prefix,
                llvm_prefix=prepared_prefix,
                llvm_resource_dir=prepared_prefix / "lib" / "clang" / "23",
                llvm_version="23.1.5",
                llvm_archive_url="https://example.invalid/llvm.tar.xz",
                llvm_archive_sha256=archive_sha256,
                llvm_archive_size=archive.stat().st_size,
                llvm_archive_root="llvm",
                llvm_license="Apache-2.0 WITH LLVM-exception",
                rust_channel="nightly-2026-09-15",
                rust_host="x86_64-unknown-linux-musl",
                make=Path("/usr/bin/make"),
                make_version="4.4.1",
                pkgconf=Path("/usr/bin/pkgconf"),
                pkgconf_version="2.3.0",
            )
            cache = mock.Mock()
            cache.require.return_value = archive

            def extract(_archive, destination, **_kwargs):
                self.assertEqual(Path(destination), raw_prefix)
                return raw_prefix

            with mock.patch.object(perf.lb, "Cache", return_value=cache), \
                    mock.patch.object(perf.lb, "_llvm_cache_root", return_value=root / "cache"), \
                    mock.patch.object(perf.lb, "_llvm_layout", return_value=object()), \
                    mock.patch.object(perf.lb, "extract_llvm_archive", side_effect=extract), \
                    mock.patch.object(perf.lb, "_toolchain", return_value=(toolchain, object())), \
                    mock.patch.object(
                        perf.lb.lane_linux, "_tool_output", return_value="GNU_STACK 0x800000"):
                first = perf.lb._llvm_ready(toolchain)
                watched = [prepared_prefix / "bin" / "clang",
                           prepared_prefix / "bin" / "x86_64-unknown-linux-musl.cfg",
                           prepared_prefix / "bin" / "strip"]
                before = [(path.lstat().st_mtime_ns, path.lstat().st_size) for path in watched]
                second = perf.lb._llvm_ready(toolchain)
            after = [(path.lstat().st_mtime_ns, path.lstat().st_size) for path in watched]
            self.assertEqual(first, prepared_prefix)
            self.assertEqual(second, prepared_prefix)
            self.assertEqual(before, after)
            self.assertEqual((binary / "clang").read_bytes(), raw_contents)
            self.assertEqual((binary / "lld").read_bytes(), raw_contents)
            self.assertTrue((prepared_prefix / ".prepared-manifest.json").is_file())


class LinuxDwarfdumpInputTests(unittest.TestCase):
    def test_dwarfdump_lock_records_reproducible_build_inputs(self):
        entry = json.loads(perf.lb.LINUX_TOOLCHAIN_LOCK.read_text())["llvm_dwarfdump"]
        recipe = entry["build"]
        deterministic = recipe["deterministic"]
        self.assertEqual(
            deterministic["environment"]["SOURCE_DATE_EPOCH"], "1704067200"
        )
        self.assertEqual(deterministic["environment"]["ZERO_AR_DATE"], "1")
        self.assertEqual(
            set(deterministic["path_maps"]),
            {"source", "build", "llvm_dev", "llvm_toolchain", "checkout"},
        )
        self.assertRegex(recipe["binary_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(recipe["binary_size"], 0)
        self.assertEqual(
            recipe["previous_binary_sha256"],
            "b323a79e2f687bb3b950f308fdeeea412f95a8e282ee15cc26277f25502265a5",
        )

    def test_fetch_provisions_source_dev_and_attestation_from_locked_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payloads = {
                "source": b"llvm source archive",
                "dev": b"llvm dev archive",
                "attestation": b"published attestation",
            }

            def pin(name, cache_path, url):
                data = payloads[name]
                return {
                    "archive_url": url,
                    "archive_sha256": hashlib.sha256(data).hexdigest(),
                    "archive_size": len(data),
                    "cache_path": cache_path,
                }

            source = pin("source", ".cache/llvm-source", "https://example.invalid/source")
            source.update({
                "attestation_url": "https://example.invalid/attestation",
                "attestation_sha256": hashlib.sha256(payloads["attestation"]).hexdigest(),
                "attestation_cache_path": ".cache/attestation",
            })
            dev = pin("dev", ".cache/llvm-dev", "https://example.invalid/dev")
            lock = root / "linux-musl-toolchain.lock.json"
            lock.write_text(json.dumps({
                "llvm_dwarfdump": {
                    "version": "23.1.5",
                    "license": "Apache-2.0 WITH LLVM-exception",
                    "source": source,
                    "dev_archive": dev,
                }
            }))
            cache = mock.Mock()
            def fetch(input_):
                name = input_.name.removeprefix("llvm-dwarfdump-")
                blob = root / "downloaded" / name
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_bytes(payloads[name])
                return blob
            cache.fetch.side_effect = fetch
            toolchain = SimpleNamespace(
                target="x86_64-unknown-linux-musl", llvm_version="23.1.5")
            with mock.patch.object(perf.lb, "LINUX_TOOLCHAIN_LOCK", lock), \
                    mock.patch.object(perf.lb, "REPO", root / "repo"), \
                    mock.patch.object(perf.lb, "CACHE_ROOT", root / "cache"), \
                    mock.patch.object(perf.lb, "Cache", return_value=cache):
                paths = perf.lb._musl_llvm_dwarfdump_inputs(toolchain, fetch=True)
            self.assertEqual(set(paths), {"source", "dev", "attestation"})
            for name, path in paths.items():
                self.assertEqual(path.read_bytes(), payloads[name])
            self.assertEqual(cache.fetch.call_count, 3)


class MuslAPKVerificationTests(unittest.TestCase):
    def test_setup_worktree_shares_inputs_before_container_preflight(self):
        command = ["python3", "rust-cpython/perf.py", "setup-worktree"]
        with mock.patch.object(musl_run, "share_worktree_caches") as share, \
                mock.patch.object(musl_run, "run_container", return_value=0) as run:
            self.assertEqual(musl_run.main(command), 0)
        share.assert_called_once_with(musl_run.REPO)
        run.assert_called_once_with("command", command, download_missing=True)

    def test_lane_lock_keeps_runtime_libgcc_but_excludes_gcc_toolchain(self):
        lock = json.loads(perf.lb.LINUX_TOOLCHAIN_LOCK.read_text())
        names = {package["name"] for package in lock["lane_packages"]}
        compiler_only = {
            "binutils", "gcc", "gmp", "isl26", "jansson", "libatomic",
            "libgcc-static", "libgomp", "mpc1", "mpfr4",
        }
        self.assertFalse(names & compiler_only)
        self.assertNotIn("gcc", lock["system_package_roots"])
        runtime = next(package for package in lock["lane_packages"] if package["name"] == "libgcc")
        self.assertEqual(runtime["role"], "lane runtime library libgcc_s.so.1, not a compiler")

    def test_tampered_and_extra_apks_are_rejected_before_container_install(self):
        payload = b"locked"
        package = {
            "name": "lane-pkg",
            "version": "1.0-r0",
            "arch": "x86_64",
            "repository": "main",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        lane = {
            "lane_apk_cache": ".cache/bootstrap/lane-musl/apk/amd64-no-gcc",
            "lane_packages": [package],
            "repositories": ["https://example.invalid/main"],
        }
        production = {"apk_closure": {"x86_64": []}}
        for scenario, bytes_, extra, message in (
            ("tampered", b"bad!!!", False, "digest/size mismatch"),
            ("extra", payload, True, "unexpected cached lane APKs"),
        ):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                lane_cache = root / "lane-apks"
                lane_cache.mkdir()
                (lane_cache / "lane-pkg-1.0-r0.apk").write_bytes(bytes_)
                if extra:
                    (lane_cache / "unexpected.apk").write_bytes(b"extra")
                base_cache = root / "bootstrap" / "apk" / "amd64"
                base_cache.mkdir(parents=True)
                docker = mock.Mock()
                with mock.patch.object(musl_run, "lock_data",
                                       return_value=(lane, production)), \
                        mock.patch.object(musl_run, "cache_path",
                                          return_value=lane_cache), \
                        mock.patch.object(musl_run, "CACHE_ROOT", root), \
                        mock.patch.object(musl_run.subprocess, "run", docker):
                    with self.assertRaisesRegex(SystemExit, message):
                        musl_run.main(["python3", "--version"])
                docker.assert_not_called()


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
    def test_online_fetch_preserves_cargo_and_git_ca_settings(self):
        ca_settings = {
            "CARGO_HTTP_SSL_CAINFO": "/run/cargo-ca.pem",
            "GIT_SSL_CAINFO": "/run/git-ca.pem",
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "Cargo.lock").write_text("locked")
            with mock.patch.dict(os.environ, {**ca_settings, "RUSTFLAGS": "ambient"}), \
                    mock.patch.object(perf.lb, "_environment", return_value={"PATH": "/usr/bin"}), \
                    mock.patch.object(perf.lb, "_require_command") as command:
                perf.lb._fetch_cargo_dependencies(
                    source / "Cargo.toml", mock.Mock(), log=source / "fetch.log"
                )
        environment = command.call_args.kwargs["env"]
        self.assertEqual({key: environment[key] for key in ca_settings}, ca_settings)
        self.assertNotIn("RUSTFLAGS", environment)

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


class MuslRustupPinTests(unittest.TestCase):
    def test_container_fetch_rejects_rustup_init_before_execution(self):
        lane = {
            "rust": {
                "cache": ".cache/rustup-musl",
                "channel": "nightly-2026-09-15",
                "host": "x86_64-unknown-linux-musl",
                "rustup": {
                    "version": "1.29.3",
                    "init_url": "https://static.rust-lang.org/rustup/archive/1.29.3/"
                                "x86_64-unknown-linux-musl/rustup-init",
                    "init_sha256": "0" * 64,
                    "checksum_url": "https://static.rust-lang.org/rustup/archive/1.29.3/"
                                    "x86_64-unknown-linux-musl/rustup-init.sha256",
                },
            }
        }
        payload = b"not the pinned rustup-init"
        with tempfile.TemporaryDirectory() as temporary:
            lane_root = Path(temporary) / "rust-cpython"
            lane_root.mkdir()
            run = mock.Mock()
            response = io.BytesIO(payload)
            with mock.patch.object(musl_run, "LANE", lane_root), \
                    mock.patch.object(musl_run, "lock_data", return_value=(lane, {})), \
                    mock.patch.object(musl_run.urllib.request, "urlopen",
                                      return_value=response) as fetch, \
                    mock.patch.object(musl_run.subprocess, "run", run):
                with self.assertRaisesRegex(SystemExit, "rustup-init digest mismatch"):
                    musl_run.container_fetch()
            self.assertEqual(
                fetch.call_args.args[0].full_url,
                lane["rust"]["rustup"]["init_url"],
            )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
