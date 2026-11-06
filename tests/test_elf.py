from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from buildsys.elf import (
    dynamic_relocation_report,
    dynamic_tags,
    full_relro_report,
    gnu_relro_report,
    gnu_stack_report,
    hardening_report,
    linkage_report,
    machine_report,
    pie_report,
    writable_executable_loads_report,
)
from buildsys.product import PRODUCT


class LinkageReportTests(unittest.TestCase):
    def test_parses_dynamic_dependencies_and_relative_search_paths(self) -> None:
        dynamic = (
            " 0x0000000000000001 (NEEDED) Shared library: [libpython3.15.so.1.0]\n"
            " 0x000000000000001d (RUNPATH) Library runpath: [$ORIGIN/../lib]\n"
        )
        self.assertEqual(dynamic_tags(dynamic), {
            "needed": ["libpython3.15.so.1.0"],
            "soname": [],
            "rpath": [],
            "runpath": ["$ORIGIN/../lib"],
        })

    def test_resolves_package_libraries_and_rejects_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "lib").mkdir()
            (root / "lib" / "libpython3.15.so.1.0").write_bytes(b"libpython")
            report = linkage_report(
                root,
                root / "bin" / PRODUCT.interpreter_name,
                needed=["libpython3.15.so.1.0", "libc.musl-x86_64.so.1"],
                runpaths=["$ORIGIN/../lib"],
                system_libraries={"libc.musl-x86_64.so.1"},
            )
            unsafe = linkage_report(
                root,
                root / "bin" / PRODUCT.interpreter_name,
                needed=["libpython3.15.so.1.0"],
                runpaths=["/opt/homebrew/lib"],
                system_libraries=set(),
            )
        self.assertTrue(report["ok"])
        self.assertFalse(unsafe["ok"])
        self.assertIn("unsafe runpath", unsafe["runpath_violations"][0])

    def test_rejects_missing_libraries_and_root_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            report = linkage_report(
                root,
                root / "bin" / PRODUCT.interpreter_name,
                needed=["libmissing.so"],
                runpaths=["$ORIGIN/../../outside"],
                system_libraries=set(),
            )
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_product_libraries"], ["libmissing.so"])
        self.assertTrue(report["runpath_violations"])

    def test_machine_report_requires_64_bit_expected_architecture(self) -> None:
        output = "  Class: ELF64\n  Machine: AArch64\n"
        self.assertTrue(machine_report(output, "aarch64")["ok"])
        self.assertFalse(machine_report(output, "x86_64")["ok"])


class GNUStackReportTests(unittest.TestCase):
    def test_reports_non_executable_stack(self) -> None:
        report = gnu_stack_report(
            "  GNU_STACK      0x000000 0x000000 0x000000 0x000000 0x000000 RW 0x10\n"
        )

        self.assertEqual(report["flags"], "RW")
        self.assertFalse(report["executable"])
        self.assertTrue(report["ok"])

    def test_executable_stack_fails(self) -> None:
        report = gnu_stack_report(
            "  GNU_STACK      0x000000 0x000000 0x000000 0x000000 0x000000 RWE 0x10\n"
        )

        self.assertTrue(report["executable"])
        self.assertFalse(report["ok"])

    def test_missing_duplicate_and_malformed_records_fail(self) -> None:
        missing = gnu_stack_report("  LOAD 0x0 0x0 0x0 0x0 0x0 R 0x1000\n")
        duplicate = gnu_stack_report(
            "  GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW 0x10\n"
            "  GNU_STACK 0x0 0x0 0x0 0x0 0x0 RW 0x10\n"
        )
        malformed = gnu_stack_report(
            "  GNU_STACK 0x0 0x0 0x0 0x0 0x0 ? 0x10\n"
        )

        self.assertFalse(missing["ok"])
        self.assertFalse(duplicate["ok"])
        self.assertFalse(malformed["ok"])


class FinalElfHardeningTests(unittest.TestCase):
    def test_interpreter_requires_et_dyn(self) -> None:
        pie = pie_report("  Type: DYN (Position-Independent Executable file)\n")
        executable = pie_report("  Type: EXEC (Executable file)\n")
        missing = pie_report("  Machine: AArch64\n")

        self.assertEqual(pie["elf_type"], "ET_DYN")
        self.assertTrue(pie["ok"])
        self.assertFalse(executable["ok"])
        self.assertFalse(missing["ok"])

    def test_full_relro_requires_read_only_segment_and_eager_binding(self) -> None:
        program_headers = (
            "  LOAD 0x0 0x1000 0x1000 0x100 0x100 R E 0x1000\n"
            "  GNU_RELRO 0x1000 0x3000 0x3000 0x100 0x100 R 0x1\n"
        )
        bind_now_tags = " 0x0000000000000018 (BIND_NOW) 0x0\n"
        flags_now = (
            " 0x000000000000001e (FLAGS) BIND_NOW\n"
            " 0x000000006ffffffb (FLAGS_1) Flags: NOW PIE\n"
        )
        numeric_flags_now = (
            " 0x000000000000001e (FLAGS) 0x8\n"
            " 0x000000006ffffffb (FLAGS_1) Flags: 0x1\n"
        )

        self.assertTrue(full_relro_report(program_headers, bind_now_tags)["ok"])
        self.assertTrue(full_relro_report(program_headers, flags_now)["ok"])
        self.assertTrue(full_relro_report(program_headers, numeric_flags_now)["ok"])
        self.assertFalse(full_relro_report("", bind_now_tags)["ok"])
        self.assertFalse(full_relro_report(program_headers, "")["ok"])
        self.assertFalse(
            full_relro_report(
                "  GNU_RELRO 0x1000 0x3000 0x3000 0x100 0x100 RW 0x1\n",
                bind_now_tags,
            )["ok"]
        )
        self.assertFalse(
            full_relro_report(
                "  GNU_RELRO 0x1000 0x3000 0x3000 0x100 0x0 R 0x1\n",
                bind_now_tags,
            )["ok"]
        )

    def test_relro_segment_must_be_unique_and_writable_loads_must_not_execute(self) -> None:
        duplicated = gnu_relro_report(
            "  GNU_RELRO 0x0 0x1000 0x1000 0x100 0x100 R 0x1\n"
            "  GNU_RELRO 0x1000 0x3000 0x3000 0x100 0x100 R 0x1\n"
        )
        safe_loads = writable_executable_loads_report(
            "  LOAD 0x0 0x1000 0x1000 0x100 0x100 R E 0x1000\n"
            "  LOAD 0x1000 0x3000 0x3000 0x100 0x100 RW 0x1000\n"
        )
        writable_executable = writable_executable_loads_report(
            "  LOAD 0x0 0x1000 0x1000 0x100 0x100 RWE 0x1000\n"
        )
        malformed = writable_executable_loads_report(
            "  LOAD 0x0 0x1000 0x1000 0x100 0x100 ? 0x1000\n"
        )

        self.assertFalse(duplicated["ok"])
        self.assertTrue(safe_loads["ok"])
        self.assertFalse(writable_executable["ok"])
        self.assertFalse(malformed["ok"])

    def test_text_relocations_are_rejected(self) -> None:
        direct_textrel = " 0x0000000000000016 (TEXTREL) 0x0\n"
        flag_textrel = " 0x000000000000001e (FLAGS) TEXTREL\n"

        self.assertFalse(dynamic_relocation_report(direct_textrel)["ok"])
        self.assertFalse(dynamic_relocation_report(flag_textrel)["ok"])
        self.assertFalse(dynamic_relocation_report("")["ok"])


class HardeningReportTests(unittest.TestCase):
    def test_accepts_musl_hardening_evidence_without_glibc_chk_symbols(self) -> None:
        report = hardening_report(
            "-O3 -fstack-protector-strong -D_FORTIFY_SOURCE=2",
            {
                PRODUCT.interpreter_name: "",
                "libpython": "  4: 0000000000000000 0 FUNC GLOBAL DEFAULT UND __stack_chk_fail\n",
            },
        )

        self.assertTrue(report["ok"])
        self.assertFalse(
            report["stack_protector_dynamic_references"][PRODUCT.interpreter_name]
        )
        self.assertTrue(report["stack_protector_dynamic_references"]["libpython"])

    def test_missing_flags_or_libpython_guard_fail(self) -> None:
        missing_fortify = hardening_report(
            "-O3 -fstack-protector-strong",
            {"libpython": "__stack_chk_fail"},
        )
        missing_guard = hardening_report(
            "-O3 -fstack-protector-strong -D_FORTIFY_SOURCE=2",
            {"libpython": ""},
        )

        self.assertFalse(missing_fortify["ok"])
        self.assertFalse(missing_guard["ok"])


if __name__ == "__main__":
    unittest.main()
