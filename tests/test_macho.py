"""Mach-O load-command edits and the buildlib inspection they rest on.

Inspection itself is tested in laputa-buildlib; these tests pin the argv of the
edits this build layers on top of it, against synthetic images built by hand.
"""
from __future__ import annotations

import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buildsys.macho import (
    MachOError,
    build_version,
    is_macho,
    read_header,
    sign_adhoc,
    signature_status,
)
from buildsys.product import PRODUCT

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
MH_EXECUTE = 2
MH_PIE = 0x00200000
LC_RPATH = 0x8000001C
LC_BUILD_VERSION = 0x32
LIBPYTHON = f"lib{PRODUCT.shared_library_stem}.dylib"


def _pad8(raw: bytes) -> bytes:
    return raw + b"\x00" * (-len(raw) % 8)


def _command(cmd: int, payload: bytes) -> bytes:
    return struct.pack("<II", cmd, 8 + len(payload)) + payload


def rpath_command(path: str) -> bytes:
    raw = _pad8(path.encode() + b"\x00")
    return _command(LC_RPATH, struct.pack("<I", 12) + raw)


def build_version_command(platform: int = 1, minos=(26, 0), sdk=(26, 5)) -> bytes:
    packed = lambda t: (t[0] << 16) | (t[1] << 8) | (t[2] if len(t) > 2 else 0)
    return _command(
        LC_BUILD_VERSION,
        struct.pack("<IIII", platform, packed(minos), packed(sdk), 0),
    )


def make_macho(commands, *, cputype: int = CPU_TYPE_ARM64,
               filetype: int = MH_EXECUTE, flags: int = MH_PIE) -> bytes:
    body = b"".join(commands)
    header = struct.pack(
        "<IiiIIIII", MH_MAGIC_64, cputype, 3, filetype, len(commands), len(body), flags, 0
    )
    return header + body


class EditCommandTests(unittest.TestCase):
    """The load-command edits must invoke the right tools with the right argv.

    These assert argv construction rather than the effect, because the effect
    needs a real signed binary; a missing import in this path is invisible
    until a full interpreter build reaches relocation, which is far too late
    to discover it.
    """

    def setUp(self) -> None:
        from buildsys import macho
        self.macho = macho
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "image"
        self.path.write_bytes(make_macho([build_version_command()]))
        self.runner = patch("buildsys.macho.subprocess.run")
        self.run = self.runner.start()
        self.run.return_value.returncode = 0
        self.run.return_value.stdout = ""
        self.run.return_value.stderr = ""
        self.addCleanup(self.runner.stop)
        self.addCleanup(self.directory.cleanup)

    def test_set_install_name_edits_then_resigns(self) -> None:
        self.macho.set_install_name(self.path, f"@rpath/{LIBPYTHON}")
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[0],
                         ["install_name_tool", "-id", f"@rpath/{LIBPYTHON}", str(self.path)])
        self.assertEqual(commands[1][:3], ["codesign", "--force", "--sign"])

    def test_change_dependency_rewrites_then_resigns(self) -> None:
        self.macho.change_dependency(self.path, f"/install/lib/{LIBPYTHON}",
                                     f"@rpath/{LIBPYTHON}")
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[0], [
            "install_name_tool", "-change",
            f"/install/lib/{LIBPYTHON}", f"@rpath/{LIBPYTHON}", str(self.path),
        ])
        self.assertEqual(commands[-1][0], "codesign")

    def test_add_rpath_is_idempotent(self) -> None:
        # The image already carries this rpath, so nothing should be run.
        image = make_macho([rpath_command("@loader_path/../lib")])
        self.path.write_bytes(image)
        self.macho.add_rpath(self.path, "@loader_path/../lib")
        self.run.assert_not_called()

    def test_add_rpath_edits_and_resigns_when_absent(self) -> None:
        self.macho.add_rpath(self.path, "@loader_path/../lib")
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[0],
                         ["install_name_tool", "-add_rpath", "@loader_path/../lib", str(self.path)])
        self.assertEqual(commands[-1][0], "codesign")

    def test_a_failing_tool_is_reported_not_ignored(self) -> None:
        self.run.return_value.returncode = 1
        self.run.return_value.stderr = "boom"
        with self.assertRaises(MachOError) as caught:
            self.macho.set_install_name(self.path, "@rpath/x.dylib")
        self.assertIn("boom", str(caught.exception))

    def test_edits_refuse_to_leave_a_stale_signature(self) -> None:
        # On Apple Silicon an edited-but-unsigned Mach-O does not launch, so
        # every edit path must end in a re-sign rather than trusting callers.
        for edit in (
            lambda: self.macho.set_install_name(self.path, "@rpath/x.dylib"),
            lambda: self.macho.change_dependency(self.path, "a", "b"),
            lambda: self.macho.add_rpath(self.path, "@loader_path/lib"),
        ):
            self.run.reset_mock()
            self.run.return_value.returncode = 0
            edit()
            last = self.run.call_args_list[-1].args[0]
            self.assertEqual(last[0], "codesign", f"{last} did not re-sign")


@unittest.skipUnless(sys.platform == "darwin", "host interpreter is not Mach-O")
class HostBinaryTests(unittest.TestCase):
    """The parser must agree with a real binary, not only with synthetic ones."""

    def test_host_interpreter_parses(self) -> None:
        executable = Path(sys.executable)
        if not is_macho(executable):
            # The system Python can be a universal Mach-O. The product parser
            # intentionally rejects fat files, so give it the running host's
            # thin slice rather than weakening that product invariant.
            with tempfile.TemporaryDirectory() as temporary:
                thin = Path(temporary) / "python"
                result = subprocess.run(
                    ["lipo", "-thin", platform.machine(), str(executable), "-output", str(thin)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                executable = thin
                self._assert_host_interpreter(executable)
            return
        self._assert_host_interpreter(executable)

    def _assert_host_interpreter(self, executable: Path) -> None:
        self.assertTrue(is_macho(executable), f"{executable} is not a thin Mach-O")
        header = read_header(executable)
        version = build_version(executable)
        self.assertIn(header.arch, {"arm64", "x86_64"})
        self.assertEqual(header.kind, "execute")
        self.assertIsNotNone(version)
        self.assertEqual(version.platform_name, "macos")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    sys.platform == "darwin" and shutil.which("clang") and shutil.which("install_name_tool"),
    "needs a macOS toolchain",
)
class SignatureSurvivesInstallNameRewriteTests(unittest.TestCase):
    def test_signed_image_stays_valid_after_install_name_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "lib.c"
            source.write_text("int answer(void) { return 42; }\n")
            image = Path(temporary) / "libanswer.dylib"
            subprocess.run(
                ["clang", "-dynamiclib", "-o", str(image), str(source)],
                check=True, capture_output=True,
            )
            sign_adhoc(image)
            subprocess.run(
                ["install_name_tool", "-id", "/somewhere/else/libanswer.dylib", str(image)],
                check=True, capture_output=True,
            )
            valid, detail = signature_status(image)
            self.assertTrue(valid, detail)
