#!/usr/bin/env python3
"""Verify that blobified Serc tables preserve each original element."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from blobify import (
    CANDIDATE_TARGET_ROOT,
    PARENT_OVERLAY_TARGET_ROOT,
    PARENT_TARGET_ROOT,
    file_pairs,
    parse_rust_string,
    parse_source_tables,
    skip_trivia,
)


BLOB_RE = re.compile(
    r"(?m)^const (?P<name>[A-Za-z_][A-Za-z_0-9]*)_BLOB: &str = "
)
OFFSETS_RE = re.compile(
    r"(?ms)^const (?P<name>[A-Za-z_][A-Za-z_0-9]*)_OFFSETS: &\[u32\] = &\[(?P<body>.*?)\];"
)


def parse_blob_tables(source: str) -> dict[str, tuple[str, ...]]:
    blobs: dict[str, bytes] = {}
    for match in BLOB_RE.finditer(source):
        blob, end = parse_rust_string(source, match.end())
        end = skip_trivia(source, end)
        if end >= len(source) or source[end] != ";":
            raise ValueError(f"missing semicolon after {match.group('name')}_BLOB")
        blobs[match.group("name")] = blob.encode("utf-8")

    offsets: dict[str, list[int]] = {}
    for match in OFFSETS_RE.finditer(source):
        tokens = [token.strip() for token in match.group("body").split(",") if token.strip()]
        try:
            offsets[match.group("name")] = [int(token, 10) for token in tokens]
        except ValueError as error:
            raise ValueError(f"invalid u32 offset in {match.group('name')}_OFFSETS") from error

    names = set(blobs)
    if names != set(offsets):
        raise ValueError(
            f"blob/offset table names differ: blobs={sorted(names)}, offsets={sorted(offsets)}"
        )

    decoded: dict[str, tuple[str, ...]] = {}
    for name in sorted(names):
        if not re.search(
            rf"(?m)^(?:pub(?:\([^)]+\))?[ \t]+)?static {re.escape(name)}:"
            r"[ \t]*std::sync::LazyLock<Box<\[&'static str\]>>",
            source,
        ):
            raise ValueError(f"missing LazyLock table {name}")
        data = blobs[name]
        boundaries = offsets[name]
        if not boundaries or boundaries[0] != 0 or boundaries[-1] != len(data):
            raise ValueError(f"invalid boundary endpoints for {name}")
        if len(boundaries) < 1 or any(
            left > right for left, right in zip(boundaries, boundaries[1:])
        ):
            raise ValueError(f"unordered boundaries for {name}")
        if any(boundary > len(data) for boundary in boundaries):
            raise ValueError(f"out-of-range boundary for {name}")
        try:
            decoded[name] = tuple(
                data[boundaries[index] : boundaries[index + 1]].decode("utf-8")
                for index in range(len(boundaries) - 1)
            )
        except UnicodeDecodeError as error:
            raise ValueError(f"offset splits UTF-8 in {name}") from error
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-source-root", type=Path, default=PARENT_TARGET_ROOT)
    parser.add_argument("--overlay-source-root", type=Path, default=PARENT_OVERLAY_TARGET_ROOT)
    parser.add_argument("--candidate-root", type=Path, default=CANDIDATE_TARGET_ROOT)
    args = parser.parse_args()

    table_count = 0
    element_count = 0
    file_count = 0
    for name, original_path, candidate_path in file_pairs(
        args.legacy_source_root, args.overlay_source_root, args.candidate_root
    ):
        original_tables = {
            table.name: table.values
            for table in parse_source_tables(original_path.read_text(encoding="utf-8"))
        }
        candidate_tables = parse_blob_tables(candidate_path.read_text(encoding="utf-8"))
        if set(original_tables) != set(candidate_tables):
            raise AssertionError(
                f"{name}: table names differ: "
                f"original={sorted(original_tables)}, candidate={sorted(candidate_tables)}"
            )
        for table_name, original_values in original_tables.items():
            candidate_values = candidate_tables[table_name]
            if original_values != candidate_values:
                raise AssertionError(f"{name} {table_name}: elements differ")
            table_count += 1
            element_count += len(original_values)
            print(f"{name} {table_name}: {len(original_values)} elements; equal")
        file_count += 1
    print(f"OVERALL: PASS; {file_count} files; {table_count} tables; {element_count} elements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
