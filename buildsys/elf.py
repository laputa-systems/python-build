"""ELF security properties that can be checked in final package bytes."""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

_STACK_TYPES = frozenset({"GNU_STACK", "PT_GNU_STACK"})
_RELRO_TYPES = frozenset({"GNU_RELRO", "PT_GNU_RELRO"})
_LOAD_TYPES = frozenset({"LOAD", "PT_LOAD"})
_PROGRAM_FLAGS = re.compile(r"[RWE]+")
_ELF_TYPE = re.compile(r"^\s*Type:\s+([A-Z]+)(?:\s+\([^)]*\))?\s*$", re.MULTILINE)
_STACK_PROTECTOR_SYMBOL = re.compile(r"__stack_chk_fail(?:_local)?(?:@[^\s]+)?")
_DYNAMIC_TAG = re.compile(r"\((NEEDED|SONAME|RPATH|RUNPATH)\).*?\[([^\]]*)\]")
_DYNAMIC_RECORD = re.compile(r"^\s*0x[0-9a-fA-F]+\s+\(([^)]+)\)\s*(.*?)\s*$")


class ElfError(Exception):
    """An ELF record is malformed or cannot be safely resolved."""


def machine_report(header: str, expected_machine: str) -> dict[str, object]:
    expected = {
        "x86_64": "Advanced Micro Devices X86-64",
        "aarch64": "AArch64",
    }.get(expected_machine)
    fields = {
        key: next((line.partition(":")[2].strip() for line in header.splitlines()
                   if line.strip().startswith(f"{key}:")), None)
        for key in ("Class", "Machine")
    }
    ok = expected is not None and fields["Class"] == "ELF64" and fields["Machine"] == expected
    return {"ok": ok, "expected_machine": expected_machine, **fields}


def pie_report(header: str) -> dict[str, object]:
    matches = _ELF_TYPE.findall(header)
    if len(matches) != 1:
        return {
            "elf_type": None,
            "ok": False,
            "error": f"expected one ELF type record, found {len(matches)}",
        }
    elf_type = f"ET_{matches[0]}"
    return {
        "elf_type": elf_type,
        "ok": elf_type == "ET_DYN",
        "error": None if elf_type == "ET_DYN" else "Linux executable is not ET_DYN",
    }


def dynamic_tags(dynamic: str) -> dict[str, list[str]]:
    result = {"needed": [], "soname": [], "rpath": [], "runpath": []}
    for line in dynamic.splitlines():
        match = _DYNAMIC_TAG.search(line)
        if match is None:
            continue
        tag, value = match.groups()
        result[tag.lower()].append(value)
    return result


def forbidden_runtime_libraries(names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name.startswith(("libc.so.6", "libgcc_s.so")) or "GLIBC" in name
    ]


def _program_header_records(
    program_headers: str, kinds: frozenset[str]
) -> list[list[str]]:
    return [
        fields
        for line in program_headers.splitlines()
        if (fields := line.split()) and fields[0] in kinds
    ]


def _program_header_flags(fields: list[str]) -> str | None:
    if len(fields) < 8:
        return None
    flag_fields = fields[6:-1]
    if not flag_fields or any(
        _PROGRAM_FLAGS.fullmatch(field) is None for field in flag_fields
    ):
        return None
    return "".join(flag_fields)


def _dynamic_flag(value: str, names: frozenset[str], mask: int) -> bool:
    tokens = value.removeprefix("Flags:").split()
    if any(token in names for token in tokens):
        return True
    for token in tokens:
        try:
            number = int(token, 0)
        except ValueError:
            continue
        if number & mask:
            return True
    return False


def dynamic_relocation_report(dynamic: str) -> dict[str, object]:
    records = []
    malformed = []
    for line in dynamic.splitlines():
        if not line.lstrip().startswith("0x"):
            continue
        match = _DYNAMIC_RECORD.fullmatch(line)
        if match is None:
            malformed.append(line.strip())
        else:
            records.append(match.groups())

    bind_now = False
    textrel = False
    for tag, value in records:
        if tag == "BIND_NOW":
            bind_now = True
        elif tag == "FLAGS":
            bind_now = bind_now or _dynamic_flag(value, frozenset({"BIND_NOW"}), 0x8)
            textrel = textrel or _dynamic_flag(value, frozenset({"TEXTREL"}), 0x4)
        elif tag == "FLAGS_1":
            bind_now = bind_now or _dynamic_flag(value, frozenset({"NOW"}), 0x1)
        elif tag == "TEXTREL":
            textrel = True

    parsed = bool(records) and not malformed
    return {
        "parsed": parsed,
        "malformed_records": malformed,
        "bind_now": bind_now,
        "textrel": textrel,
        "ok": parsed and not textrel,
    }


def linkage_report(
    root: Path,
    elf: Path,
    *,
    needed: list[str],
    runpaths: list[str],
    system_libraries: set[str],
) -> dict[str, object]:
    root = Path(root).resolve()
    elf = Path(elf).resolve()
    if not elf.is_relative_to(root):
        raise ElfError(f"ELF path escaped product root: {elf}")
    relative = PurePosixPath(elf.relative_to(root).as_posix())
    resolved_paths: list[Path] = []
    violations: list[str] = []
    for raw in runpaths:
        if not raw.startswith("$ORIGIN") or (len(raw) > 7 and raw[7] != "/"):
            violations.append(f"unsafe runpath {raw!r}")
            continue
        stack = list(relative.parent.parts)
        for part in raw[7:].split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not stack:
                    violations.append(f"runpath escapes product root: {raw!r}")
                    break
                stack.pop()
            elif "$" in part:
                violations.append(f"unsupported runpath variable in {raw!r}")
                break
            else:
                stack.append(part)
        else:
            directory = root.joinpath(*stack).resolve()
            if not directory.is_relative_to(root) or not directory.is_dir():
                violations.append(f"unresolved runpath {raw!r}")
            else:
                resolved_paths.append(directory)

    missing: list[str] = []
    for library in needed:
        if library in system_libraries:
            continue
        candidates = [directory / library for directory in resolved_paths]
        if not any(
            candidate.is_file() and candidate.resolve().is_relative_to(root)
            for candidate in candidates
        ):
            missing.append(library)
    return {
        "ok": not violations and not missing,
        "needed": needed,
        "runpaths": runpaths,
        "resolved_runpaths": [str(path.relative_to(root)) for path in resolved_paths],
        "missing_product_libraries": missing,
        "runpath_violations": violations,
    }


def gnu_stack_report(program_headers: str) -> dict[str, str | bool | None]:
    """Parse one final ELF's `readelf -lW` PT_GNU_STACK record."""
    records = _program_header_records(program_headers, _STACK_TYPES)
    if not records:
        return {
            "present": False,
            "flags": None,
            "executable": None,
            "ok": False,
            "error": "missing PT_GNU_STACK (executable by default on some loaders)",
        }
    if len(records) != 1:
        return {
            "present": True,
            "flags": None,
            "executable": None,
            "ok": False,
            "error": f"expected one PT_GNU_STACK record, found {len(records)}",
        }
    fields = records[0]
    flags = _program_header_flags(fields)
    if flags is None:
        return {
            "present": True,
            "flags": None,
            "executable": None,
            "ok": False,
            "error": "unrecognized PT_GNU_STACK record",
        }
    executable = "E" in flags
    return {
        "present": True,
        "flags": flags,
        "executable": executable,
        "ok": not executable,
        "error": "executable stack requested" if executable else None,
    }


def gnu_relro_report(program_headers: str) -> dict[str, object]:
    records = _program_header_records(program_headers, _RELRO_TYPES)
    if not records:
        return {
            "present": False,
            "flags": None,
            "memory_size": None,
            "read_only": None,
            "ok": False,
            "error": "missing PT_GNU_RELRO",
        }
    if len(records) != 1:
        return {
            "present": True,
            "flags": None,
            "memory_size": None,
            "read_only": None,
            "ok": False,
            "error": f"expected one PT_GNU_RELRO record, found {len(records)}",
        }
    fields = records[0]
    flags = _program_header_flags(fields)
    if flags is None:
        return {
            "present": True,
            "flags": None,
            "memory_size": None,
            "read_only": None,
            "ok": False,
            "error": "unrecognized PT_GNU_RELRO record",
        }
    try:
        memory_size = int(fields[5], 0)
    except ValueError:
        memory_size = 0
    read_only = "R" in flags and "W" not in flags and memory_size > 0
    return {
        "present": True,
        "flags": flags,
        "memory_size": memory_size,
        "read_only": read_only,
        "ok": read_only,
        "error": None if read_only else "PT_GNU_RELRO must be read-only and nonempty",
    }


def full_relro_report(program_headers: str, dynamic: str) -> dict[str, object]:
    relro = gnu_relro_report(program_headers)
    relocations = dynamic_relocation_report(dynamic)
    ok = relro["ok"] and relocations["parsed"] and relocations["bind_now"]
    return {
        "ok": ok,
        "gnu_relro": relro,
        "bind_now": relocations["bind_now"],
        "dynamic_tags_parsed": relocations["parsed"],
        "error": None if ok else "full RELRO requires PT_GNU_RELRO and eager binding",
    }


def writable_executable_loads_report(program_headers: str) -> dict[str, object]:
    records = _program_header_records(program_headers, _LOAD_TYPES)
    invalid = []
    writable_executable = []
    for index, fields in enumerate(records):
        flags = _program_header_flags(fields)
        if flags is None:
            invalid.append(index)
        elif "W" in flags and "E" in flags:
            writable_executable.append({"index": index, "flags": flags})
    return {
        "load_segments": len(records),
        "invalid_records": invalid,
        "writable_executable_segments": writable_executable,
        "ok": bool(records) and not invalid and not writable_executable,
    }


def has_stack_protector_reference(dynamic_symbols: str) -> bool:
    """Whether final dynamic symbols include a stack-protector failure call."""
    return any(
        _STACK_PROTECTOR_SYMBOL.fullmatch(symbol) is not None
        for line in dynamic_symbols.splitlines()
        for symbol in line.split()
    )


def hardening_report(
    compiler_flags: str,
    dynamic_symbols: dict[str, str],
) -> dict[str, object]:
    """Check portable clang/musl hardening evidence in a Linux install.

    `_FORTIFY_SOURCE` is implemented by musl headers and compiler builtins;
    unlike glibc it does not require exported `__*_chk` symbols. The configured
    compile flag is therefore the portable fortify evidence. A dynamic
    `__stack_chk_fail` reference in libpython proves stack-protected functions
    survived link-time optimization and stripping. The small launcher is
    reported too, but is allowed to have no guard reference when none of its
    functions meet the compiler's `-fstack-protector-strong` selection rules.
    """
    try:
        tokens = shlex.split(compiler_flags)
    except ValueError:
        tokens = []
    stack_protector_flag = "-fstack-protector-strong" in tokens
    fortify_flag = "-D_FORTIFY_SOURCE=2" in tokens
    references = {
        name: has_stack_protector_reference(output)
        for name, output in dynamic_symbols.items()
    }
    return {
        "configured_flags": {
            "stack_protector_strong": stack_protector_flag,
            "fortify_source_2": fortify_flag,
        },
        "stack_protector_dynamic_references": references,
        "fortify_implementation": "musl headers and compiler builtins; no glibc _chk symbol requirement",
        "ok": (
            stack_protector_flag
            and fortify_flag
            and references.get("libpython", False)
        ),
    }
