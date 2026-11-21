#!/usr/bin/env python3
"""Replace generated Serc string tables with UTF-8 blobs and offsets."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path("/home/ubuntu/repos/python-build")
PARENT_TARGET_ROOT = (
    ROOT
    / "wt-cloc-parent/rust-cpython/work/perf/perf-cloc-parent/source/"
    "cpython-e891a9117985888df3e5d48145cf07ddda098335/serc/crates/serc/src/target"
)
PARENT_OVERLAY_TARGET_ROOT = (
    ROOT / "wt-cloc-parent/rust-cpython/serc-overlay/crates/serc/src/target"
)
CANDIDATE_TARGET_ROOT = (
    ROOT / "wt-crel3-cand/rust-cpython/serc-overlay/crates/serc/src/target"
)

CODEC_TABLES = (
    "ASCII",
    "LATIN_1",
    "CP1251",
    "CP1252",
    "ISO8859_15",
    "KOI8_R",
    "EUC_JP",
    "BIG5",
)

EXPECTED_TABLES = {
    "cpython_3_12_14.rs": (
        "PREINTERNED_STRINGS",
        "COMPILER_INTERNED_STRINGS",
        "STARTUP_INTERNED_STRINGS",
    ),
    "cpython_3_13_15.rs": (
        "PREINTERNED_STRINGS",
        "COMPILER_INTERNED_STRINGS",
        "STARTUP_INTERNED_STRINGS",
    ),
    "cpython_3_14_5.rs": (
        "PREINTERNED_STRINGS",
        "COMPILER_INTERNED_STRINGS",
        "BOOTSTRAP_INTERNED_STRINGS",
        "LINUX_PREINTERNED_STRINGS",
    ),
    "cpython_3_15_0rc2.rs": (
        "PREINTERNED_STRINGS",
        "COMPILER_INTERNED_STRINGS",
        "STARTUP_INTERNED_STRINGS",
    ),
    "cpython_3_16_0a1.rs": (
        "PREINTERNED_STRINGS",
        "COMPILER_INTERNED_STRINGS",
        "STARTUP_INTERNED_STRINGS",
    ),
}

DECLARATION_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)"
    r"(?P<visibility>pub(?:\([^)]+\))?[ \t]+)?"
    r"(?P<kind>static|const)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z_0-9]*)[ \t]*:[ \t]*"
    r"&[ \t]*\[[ \t]*&[ \t]*str[ \t]*\][ \t]*=[ \t]*&[ \t]*\["
)


@dataclass(frozen=True)
class Table:
    name: str
    visibility: str
    kind: str
    start: int
    end: int
    decorators: tuple[str, ...]
    values: tuple[str, ...]


def file_pairs(
    legacy_source_root: Path = PARENT_TARGET_ROOT,
    overlay_source_root: Path = PARENT_OVERLAY_TARGET_ROOT,
    output_root: Path = CANDIDATE_TARGET_ROOT,
) -> list[tuple[str, Path, Path]]:
    names = (
        "cpython_3_12_14.rs",
        "cpython_3_12_14_source_codecs.rs",
        "cpython_3_13_15.rs",
        "cpython_3_13_15_source_codecs.rs",
        "cpython_3_14_5.rs",
        "cpython_3_14_5_source_codecs.rs",
        "cpython_3_15_0rc2.rs",
        "cpython_3_15_0rc2_source_codecs.rs",
    )
    pairs = [(name, legacy_source_root / name, output_root / name) for name in names]
    for name in ("cpython_3_16_0a1.rs", "cpython_3_16_0a1_source_codecs.rs"):
        pairs.append((name, overlay_source_root / name, output_root / name))
    return pairs


def parse_rust_string(source: str, start: int) -> tuple[str, int]:
    raw = re.match(r'r(?P<hashes>#+)?"', source[start:])
    if raw is not None:
        hashes = raw.group("hashes") or ""
        body_start = start + raw.end()
        terminator = '"' + hashes
        end = source.find(terminator, body_start)
        if end < 0:
            raise ValueError(f"unterminated raw string at byte {start}")
        return source[body_start:end], end + len(terminator)

    if start >= len(source) or source[start] != '"':
        raise ValueError(f"expected Rust string literal at byte {start}")

    escapes = {
        "\\": "\\",
        '"': '"',
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "0": "\0",
    }
    result: list[str] = []
    index = start + 1
    while index < len(source):
        char = source[index]
        if char == '"':
            return "".join(result), index + 1
        if char != "\\":
            if char in "\r\n":
                raise ValueError(f"unescaped newline in Rust string at byte {index}")
            result.append(char)
            index += 1
            continue

        index += 1
        if index >= len(source):
            raise ValueError("unterminated escape in Rust string")
        escape = source[index]
        if escape in escapes:
            result.append(escapes[escape])
            index += 1
        elif escape == "x":
            digits = source[index + 1 : index + 3]
            if len(digits) != 2 or not re.fullmatch(r"[0-9a-fA-F]{2}", digits):
                raise ValueError(f"invalid Rust hexadecimal escape at byte {index}")
            value = int(digits, 16)
            if value > 0x7F:
                raise ValueError(f"non-ASCII Rust \\x escape at byte {index}")
            result.append(chr(value))
            index += 3
        elif escape == "u" and source[index + 1 : index + 2] == "{":
            end = source.find("}", index + 2)
            if end < 0:
                raise ValueError(f"unterminated Rust Unicode escape at byte {index}")
            digits = source[index + 2 : end].replace("_", "")
            if not digits or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                raise ValueError(f"invalid Rust Unicode escape at byte {index}")
            try:
                result.append(chr(int(digits, 16)))
            except ValueError as error:
                raise ValueError(f"invalid Rust Unicode scalar at byte {index}") from error
            index = end + 1
        elif escape == "\n":
            index += 1
            while index < len(source) and source[index] in " \t\r\n":
                index += 1
        elif escape == "\r" and source[index + 1 : index + 2] == "\n":
            index += 2
            while index < len(source) and source[index] in " \t\r\n":
                index += 1
        else:
            raise ValueError(f"unsupported Rust string escape \\{escape} at byte {index}")
    raise ValueError(f"unterminated Rust string starting at byte {start}")


def skip_trivia(source: str, index: int) -> int:
    while index < len(source):
        if source[index].isspace():
            index += 1
        elif source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
        elif source.startswith("/*", index):
            depth = 1
            index += 2
            while depth:
                if index >= len(source):
                    raise ValueError("unterminated Rust block comment")
                if source.startswith("/*", index):
                    depth += 1
                    index += 2
                elif source.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
        else:
            break
    return index


def parse_array(source: str, opening: int) -> tuple[tuple[str, ...], int]:
    if source[opening] != "[":
        raise ValueError(f"expected array opening at byte {opening}")
    values: list[str] = []
    index = opening + 1
    while True:
        index = skip_trivia(source, index)
        if index >= len(source):
            raise ValueError("unterminated Rust string table")
        if source[index] == "]":
            return tuple(values), index + 1

        value, index = parse_rust_string(source, index)
        values.append(value)
        index = skip_trivia(source, index)
        if index < len(source) and source[index] == ",":
            index += 1
        elif index < len(source) and source[index] == "]":
            return tuple(values), index + 1
        else:
            raise ValueError(f"expected comma or closing bracket at byte {index}")


def previous_line(source: str, cursor: int) -> tuple[int, str]:
    if cursor <= 0 or source[cursor - 1] != "\n":
        return cursor, ""
    end = cursor - 1
    start = source.rfind("\n", 0, end) + 1
    return start, source[start:end].rstrip("\r")


def square_bracket_delta(line: str) -> int:
    delta = 0
    in_string = False
    escaped = False
    for char in line:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            delta += 1
        elif char == "]":
            delta -= 1
    return delta


def attribute_block_before(source: str, cursor: int) -> tuple[int, list[str]] | None:
    lines: list[str] = []
    depth = 0
    while cursor > 0:
        start, line = previous_line(source, cursor)
        stripped = line.strip()
        if not stripped:
            return None
        lines.insert(0, line)
        depth -= square_bracket_delta(stripped)
        if stripped.startswith("#[") and depth == 0:
            return start, lines
        if depth <= 0:
            return None
        cursor = start
    return None


def doc_block_before(source: str, cursor: int) -> tuple[int, list[str]] | None:
    lines: list[str] = []
    while cursor > 0:
        start, line = previous_line(source, cursor)
        stripped = line.strip()
        if not stripped:
            lines.insert(0, line)
            cursor = start
            continue
        lines.insert(0, line)
        if "/**" in stripped and not stripped.startswith("/*!"):
            return start, lines
        if "/*" in stripped and "/**" not in stripped:
            return None
        cursor = start
    return None


def decorators_before(source: str, declaration_start: int) -> tuple[str, ...]:
    cursor = declaration_start
    blocks: list[list[str]] = []
    while cursor > 0:
        start, line = previous_line(source, cursor)
        stripped = line.strip()
        if not stripped:
            cursor = start
            continue
        if stripped.startswith("///"):
            blocks.append([line])
            cursor = start
            continue
        if stripped.startswith("/**") and stripped.endswith("*/"):
            blocks.append([line])
            cursor = start
            continue
        if stripped.endswith("*/"):
            block = doc_block_before(source, cursor)
            if block is None:
                break
            start, lines = block
            blocks.append(lines)
            cursor = start
            continue
        if stripped.startswith("#[") or stripped.endswith("]"):
            block = attribute_block_before(source, cursor)
            if block is None:
                break
            start, lines = block
            blocks.append(lines)
            cursor = start
            continue
        break

    return tuple(line for block in reversed(blocks) for line in block)


def parse_source_tables(source: str) -> list[Table]:
    tables: list[Table] = []
    for match in DECLARATION_RE.finditer(source):
        if match.group("indent"):
            raise ValueError(f"string-table declaration is not top-level: {match.group('name')}")
        values, after_array = parse_array(source, match.end() - 1)
        end = after_array
        while end < len(source) and source[end] in " \t":
            end += 1
        if end >= len(source) or source[end] != ";":
            raise ValueError(f"missing semicolon after table {match.group('name')}")
        tables.append(
            Table(
                name=match.group("name"),
                visibility=match.group("visibility") or "",
                kind=match.group("kind"),
                start=match.start(),
                end=end + 1,
                decorators=decorators_before(source, match.start()),
                values=values,
            )
        )
    return tables


def rust_string(value: str) -> str:
    escaped = ['"']
    for char in value:
        codepoint = ord(char)
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif char == "\0":
            escaped.append("\\0")
        elif codepoint < 0x20 or codepoint == 0x7F:
            escaped.append(f"\\u{{{codepoint:x}}}")
        else:
            escaped.append(char)
    escaped.append('"')
    return "".join(escaped)


def render_table(table: Table) -> str:
    blob_parts: list[str] = []
    offsets = [0]
    byte_count = 0
    for value in table.values:
        blob_parts.append(value)
        byte_count += len(value.encode("utf-8"))
        if byte_count > 0xFFFFFFFF:
            raise ValueError(f"{table.name} exceeds u32 offset range")
        offsets.append(byte_count)
    blob = "".join(blob_parts)

    lines = [
        f"const {table.name}_BLOB: &str = {rust_string(blob)};",
    ]
    lines.extend(table.decorators)
    lines.append(f"const {table.name}_OFFSETS: &[u32] = &[")
    for index in range(0, len(offsets), 12):
        values = ", ".join(str(offset) for offset in offsets[index : index + 12])
        lines.append(f"    {values},")
    lines.append("];")
    lines.extend(table.decorators)
    lines.extend(
        [
            f"{table.visibility}static {table.name}: std::sync::LazyLock<Box<[&'static str]>> =",
            "    std::sync::LazyLock::new(|| {",
            f"        (0..{table.name}_OFFSETS.len() - 1)",
            f"            .map(|i| &{table.name}_BLOB[{table.name}_OFFSETS[i] as usize..{table.name}_OFFSETS[i + 1] as usize])",
            "            .collect()",
            "    });",
        ]
    )
    return "\n".join(lines)


def expected_tables_for(name: str) -> tuple[str, ...]:
    if name.endswith("_source_codecs.rs"):
        return CODEC_TABLES
    return EXPECTED_TABLES[name]


def transform_file(source_path: Path, output_path: Path, name: str) -> list[Table]:
    source = source_path.read_text(encoding="utf-8")
    tables = parse_source_tables(source)
    expected = set(expected_tables_for(name))
    actual = {table.name for table in tables}
    if actual != expected:
        raise ValueError(
            f"{source_path}: expected tables {sorted(expected)}, found {sorted(actual)}"
        )

    transformed = source
    for table in reversed(tables):
        transformed = (
            transformed[: table.start]
            + render_table(table)
            + transformed[table.end :]
        )

    header_end = transformed.find("\n\n")
    if header_end < 0:
        raise ValueError(f"{source_path}: generated header is not terminated by a blank line")
    transformed = (
        transformed[:header_end]
        + "\n// Transformed by blobify.py from the generated file."
        + transformed[header_end:]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(transformed, encoding="utf-8")
    return tables


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-source-root", type=Path, default=PARENT_TARGET_ROOT)
    parser.add_argument("--overlay-source-root", type=Path, default=PARENT_OVERLAY_TARGET_ROOT)
    parser.add_argument("--output-root", type=Path, default=CANDIDATE_TARGET_ROOT)
    args = parser.parse_args()

    total_tables = 0
    for name, source, output in file_pairs(
        args.legacy_source_root, args.overlay_source_root, args.output_root
    ):
        tables = transform_file(source, output, name)
        total_tables += len(tables)
        for table in tables:
            print(f"{name} {table.name} {len(table.values)}")
    print(f"transformed {total_tables} tables across 10 files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
