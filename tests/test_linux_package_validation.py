from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from buildsys.product import PRODUCT
from buildsys.targets import TARGETS

REPO = Path(__file__).resolve().parents[1]
SCRATCH = REPO / ".work" / "tmp"
SCRATCH.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location(
    "linux_package_driver", REPO / "build" / "package.py"
)
DRIVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DRIVER
SPEC.loader.exec_module(DRIVER)


class LinuxPackageValidationCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.install = Path(self.temporary.name)
        self.interpreter = PRODUCT.interpreter(self.install)
        self.interpreter.parent.mkdir(parents=True)
        self.interpreter.write_bytes(b"interpreter")
        self.libpython = self.install / "lib" / "libpython3.15.so"
        self.libpython.parent.mkdir()
        self.libpython.write_bytes(b"libpython")
        self.static_executable = self.install / "bin" / "static-tool"
        self.static_executable.parent.mkdir(exist_ok=True)
        self.static_executable.write_bytes(b"static executable")
        self.static_executable.chmod(0o755)
        self.extra_elfs = []
        self.dynamic_by_path: dict[str, str] = {}
        self.comment = (
            "String dump of section '.comment':\n"
            "[     0] clang version 23.1.0-rc2\n"
            "[    1a] Linker: LLD 23.1.0\n"
        )
        self.comment_by_path: dict[str, str] = {}
        self.header_type = "DYN"
        self.dynamic = (
            " 0x0000000000000001 (NEEDED) Shared library: "
            "[libc.musl-x86_64.so.1]\n"
            " 0x0000000000000018 (BIND_NOW) 0x0\n"
        )
        self.program_headers = (
            "  INTERP 0x0 0x1000 0x1000 0x20 0x20 R 0x1\n"
            "  LOAD 0x0 0x1000 0x1000 0x100 0x100 R E 0x1000\n"
            "  LOAD 0x1000 0x3000 0x3000 0x100 0x100 RW 0x1000\n"
            "  GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW 0x10\n"
            "  GNU_RELRO 0x1000 0x3000 0x3000 0x100 0x100 R 0x1\n"
            "  [Requesting program interpreter: /lib/ld-musl-x86_64.so.1]\n"
        )

    def _readelf(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        if command[1] == "-hW":
            elf_type = (
                "EXEC"
                if command[-1] == str(self.static_executable)
                else self.header_type
            )
            output = (
                f"  Class: ELF64\n  Type: {elf_type}\n"
                "  Machine: Advanced Micro Devices X86-64\n"
            )
        elif command[1] == "-dW":
            output = (
                "There is no dynamic section in this file.\n"
                if command[-1] == str(self.static_executable)
                else self.dynamic_by_path.get(command[-1], self.dynamic)
            )
        elif command[1] == "-lW":
            output = self.program_headers
            if command[-1] != str(self.interpreter):
                output = "\n".join(
                    line for line in output.splitlines()
                    if not line.lstrip().startswith("INTERP")
                    and "Requesting program interpreter" not in line
                )
        elif command[1:3] == ["-p", ".comment"]:
            output = self.comment_by_path.get(command[-1], self.comment)
        elif command[1] == "--dyn-syms":
            output = "__stack_chk_fail\n" if command[-1] == str(self.libpython) else ""
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def _product_process(self, command: list[str], *args, **kwargs):
        code = command[-1]
        if "INSTSONAME" in code:
            output = "libpython3.15.so\n"
        elif "CFLAGS" in code:
            output = json.dumps({
                "CFLAGS": "-O3 -fstack-protector-strong -D_FORTIFY_SOURCE=2",
                "CFLAGS_NODIST": "",
                "PY_CFLAGS": "",
                "PY_CFLAGS_NODIST": "",
                "CPPFLAGS": "",
            })
        else:
            output = ""
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    def _validate(self) -> dict:
        with (
            patch.object(DRIVER, "TARGET_DESCRIPTION", TARGETS["x86_64-unknown-linux-musl"]),
            patch.object(
                DRIVER,
                "find_elfs",
                return_value=[self.interpreter, self.libpython, *self.extra_elfs],
            ),
            patch.object(DRIVER, "_run", side_effect=self._readelf),
            patch.object(DRIVER, "run_product_process", side_effect=self._product_process),
        ):
            return DRIVER._linux_validation(self.install)


class LinuxPackageHardeningTests(LinuxPackageValidationCase):
    def test_final_elf_images_pass_pie_relro_and_relocation_gates(self) -> None:
        report = self._validate()

        self.assertTrue(report["ok"], report["failed"])
        self.assertTrue(report["checks"]["elf_pie"]["ok"])
        self.assertTrue(report["checks"]["elf_relro"]["ok"])
        self.assertTrue(report["checks"]["elf_writable_executable"]["ok"])
        self.assertTrue(report["checks"]["elf_text_relocations"]["ok"])

    def test_static_et_exec_is_included_in_pie_gate(self) -> None:
        self.extra_elfs.append(self.static_executable)

        report = self._validate()

        self.assertFalse(report["checks"]["elf_pie"]["ok"])
        self.assertIn(
            str(self.static_executable.relative_to(self.install)),
            report["checks"]["elf_pie"]["violations"],
        )

    def test_final_elf_images_fail_when_hardening_evidence_is_missing(self) -> None:
        self.header_type = "EXEC"
        self.dynamic = (
            " 0x0000000000000001 (NEEDED) Shared library: "
            "[libc.musl-x86_64.so.1]\n"
            " 0x0000000000000016 (TEXTREL) 0x0\n"
        )
        self.program_headers = (
            "  INTERP 0x0 0x1000 0x1000 0x20 0x20 R 0x1\n"
            "  LOAD 0x0 0x1000 0x1000 0x100 0x100 RWE 0x1000\n"
            "  GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW 0x10\n"
        )

        report = self._validate()
        checks = report["checks"]

        self.assertFalse(report["ok"])
        self.assertFalse(checks["elf_pie"]["ok"])
        self.assertFalse(checks["elf_relro"]["ok"])
        self.assertFalse(checks["elf_writable_executable"]["ok"])
        self.assertFalse(checks["elf_text_relocations"]["ok"])


class LinuxRuntimeDependencyTests(LinuxPackageValidationCase):
    def _extension(self, name: str, dynamic: str) -> Path:
        extension = self.install / "lib" / "python3.15" / "lib-dynload" / name
        extension.parent.mkdir(parents=True, exist_ok=True)
        extension.write_bytes(b"extension")
        self.extra_elfs.append(extension)
        self.dynamic_by_path[str(extension)] = dynamic
        return extension

    def test_musl_is_the_only_platform_library(self) -> None:
        self._extension("_ok.so", self.dynamic)

        report = self._validate()

        self.assertTrue(report["ok"], report["failed"])
        self.assertEqual(
            report["checks"]["elf_linkage"]["platform_libraries"],
            ["libc.musl-x86_64.so.1"],
        )

    def test_libgcc_s_dependency_is_rejected(self) -> None:
        extension = self._extension(
            "_rustls.abi3.so",
            " 0x0000000000000001 (NEEDED) Shared library: [libgcc_s.so.1]\n"
            + self.dynamic,
        )

        report = self._validate()
        linkage = report["checks"]["elf_linkage"]

        self.assertFalse(report["ok"])
        self.assertIn("elf_linkage", report["failed"])
        self.assertEqual(
            linkage["forbidden_libraries"],
            {str(extension.relative_to(self.install)): ["libgcc_s.so.1"]},
        )
        self.assertNotIn("libgcc_s.so.1", linkage["platform_libraries"])

    def test_bundled_libgcc_s_is_rejected_even_when_resolvable(self) -> None:
        bundled = self.install / "lib" / "libgcc_s.so.1"
        bundled.write_bytes(b"libgcc_s")
        self.extra_elfs.append(bundled)
        self.dynamic_by_path[str(bundled)] = (
            " 0x000000000000000e (SONAME) Library soname: [libgcc_s.so.1]\n"
            + self.dynamic
        )
        consumer = self._extension(
            "_decimal.so",
            " 0x0000000000000001 (NEEDED) Shared library: [libgcc_s.so.1]\n"
            " 0x000000000000001d (RUNPATH) Library runpath: [$ORIGIN/../..]\n"
            + self.dynamic,
        )

        report = self._validate()
        linkage = report["checks"]["elf_linkage"]

        self.assertFalse(report["ok"])
        self.assertEqual(linkage["unresolved"], [])
        self.assertEqual(sorted(linkage["forbidden_libraries"]), sorted([
            str(bundled.relative_to(self.install)),
            str(consumer.relative_to(self.install)),
        ]))

    def test_gcc_built_objects_are_rejected(self) -> None:
        extension = self._extension("_decimal.so", self.dynamic)
        self.comment_by_path[str(extension)] = (
            self.comment + "[    34] GCC: (Alpine 15.2.0) 15.2.0\n"
        )

        report = self._validate()
        check = report["checks"]["elf_compiler_runtime"]

        self.assertFalse(report["ok"])
        self.assertIn("elf_compiler_runtime", report["failed"])
        self.assertEqual(
            check["violations"],
            {str(extension.relative_to(self.install)): ["GCC: (Alpine 15.2.0) 15.2.0"]},
        )

    def test_unlisted_platform_libraries_are_rejected(self) -> None:
        for library in ("libatomic.so.1", "libstdc++.so.6", "libz.so.1"):
            with self.subTest(library=library):
                self.extra_elfs.clear()
                self.dynamic_by_path.clear()
                extension = self._extension(
                    "_module.so",
                    f" 0x0000000000000001 (NEEDED) Shared library: [{library}]\n"
                    + self.dynamic,
                )

                report = self._validate()
                linkage = report["checks"]["elf_linkage"]

                self.assertFalse(report["ok"])
                self.assertEqual(
                    linkage["unresolved"],
                    [str(extension.relative_to(self.install))],
                )


if __name__ == "__main__":
    unittest.main()
