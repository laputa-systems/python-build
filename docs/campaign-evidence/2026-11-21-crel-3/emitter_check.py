#!/usr/bin/env python3
"""Check generated blob declarations against the C-REL-3 files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/ubuntu/repos/python-build/wt-crel3-cand")
HELPERS = Path("/home/ubuntu/exp-serc")
PARENT = Path("/home/ubuntu/repos/python-build/wt-cloc-parent")
BASELINE = "ef48b04"

sys.path.insert(0, str(HELPERS))
from table_equality import file_pairs, parse_source_tables

sys.path.insert(
    0, str(REPO / "rust-cpython/serc-overlay/crates/serc/scripts")
)
import generate_cpython_target


def baseline_file(path: Path) -> str:
    relative = path.relative_to(REPO).as_posix()
    result = subprocess.run(
        ["git", "show", f"{BASELINE}:{relative}"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return re.sub(
        r"(?m)^// Transformed by blobify\.py from the generated file\.\n",
        "",
        result.stdout,
    )


def declaration_block(source: str, table) -> str:
    blob_declaration = f"const {table.name}_BLOB: &str = "
    blob_start = source.index(blob_declaration)
    block_start = source.rfind("\n", 0, blob_start) + 1
    if table.decorators:
        prefix = "\n".join(table.decorators) + "\n"
        decorated_start = blob_start - len(prefix)
        if source[decorated_start:blob_start] != prefix:
            raise AssertionError(f"{table.name}: outer attributes do not match")
        block_start = decorated_start

    static_start = source.index(f"static {table.name}:", blob_start)
    end = source.index("    });", static_start) + len("    });")
    return source[block_start:end]


def main() -> int:
    parent_head = subprocess.run(
        ["git", "-C", str(PARENT), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if parent_head != "da74796":
        raise RuntimeError(f"original source worktree is at {parent_head}, not da74796")

    failures = []
    for filename, original_path, candidate_path in file_pairs():
        try:
            source_tables = parse_source_tables(
                original_path.read_text(encoding="utf-8")
            )
            candidate = baseline_file(candidate_path)
            for table in source_tables:
                expected = "\n".join(
                    generate_cpython_target.render_rust_string_table(
                        table.name,
                        list(table.values),
                        table.visibility,
                        table.decorators,
                    )
                )
                actual = declaration_block(candidate, table)
                if actual != expected:
                    raise AssertionError(f"{table.name}: emitted declaration differs")
            print(f"{filename}: PASS ({len(source_tables)} tables)")
        except Exception as error:
            failures.append(filename)
            print(f"{filename}: FAIL ({error})")

    print(
        f"OVERALL: {'FAIL' if failures else 'PASS'}; "
        f"{len(failures)} failed files out of {len(file_pairs())}"
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
