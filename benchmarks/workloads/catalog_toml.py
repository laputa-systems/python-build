"""Load a complete, deterministic catalog manifest through the public TOML API.

One operation parses one 512-record TOML document with `tomllib.loads`. The
document text and its input check precede timing; hashing the parsed tables
follows timing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import tomllib

_EXPECTED_INPUT = "3f3724bfbb7f04ae4b48c6cde25544b55f4e270810736410e0edb08a8f43838a"
_EXPECTED_OUTPUT = "3f9dc8c27b5027f12d2780142601126987795c4cea24d3d8ae1a6aa315f778e2"
_RECORD_COUNT = 512


class WorkloadError(RuntimeError):
    """The fixed catalog document or its parsed tables changed."""


def _basic_string(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def catalog_document() -> str:
    """The manifest text: one `[[entry]]` table per record, in a fixed order."""
    titles = (
        "Release notes: Python & TOML", "Café inventory / été",
        "画像の説明と検索", 'Quoted "title" \\ path',
        "Package manifest — arm64", "Source map: Δ changes",
        "Security advisory <2026>", "Documentation: naïve approach",
    )
    bodies = (
        "A compact record with ordinary English words and punctuation.",
        "Unicode text: résumé, 東京, emoji 🐍, and € prices. " * 3,
        'Escapes: quote " slash \\ tab\t and newline\n in a field.',
        "Long description with repeated searchable detail. " * 12,
    )
    kinds = ("article", "image", "package", "advisory", "document")
    owners = ("Alice", "Bjørn", "Team Δ")
    mimes = ("application/json", "image/png", "text/plain")
    lines = [
        'title = "Catalog manifest"',
        "schema = 3",
        "generated = 2026-01-15T08:30:00Z",
        "",
        "[defaults]",
        'language = "ja"',
        "visible = true",
        "ratings = { minimum = 0.5, maximum = 5.0 }",
        "",
    ]
    for index in range(_RECORD_COUNT):
        zone = ("+09:00", "-07:00", "Z")[index % 3]
        lines += [
            "[[entry]]",
            f'identifier = "item-{(index * 73) % _RECORD_COUNT:04d}"',
            f"title = {_basic_string(f'{titles[index % len(titles)]} #{index:04d}')}",
            f'kind = "{kinds[index % len(kinds)]}"',
            f"body = {_basic_string(bodies[index % len(bodies)])}",
            f"revision = {1 + index % 9}",
            f"size = {(index * 7919) % 1_000_003}",
            f"rating = {0.5 + (index % 10) * 0.5}",
            f"weight = {index}.{(index * 37) % 1000:03d}e-2",
            f"visible = {'true' if index % 5 else 'false'}",
            f"modified = 2026-01-{1 + index % 28:02d}T{index % 24:02d}:{index % 60:02d}:00{zone}",
            f"due = 2026-{1 + index % 12:02d}-{1 + index % 28:02d}",
            f"labels = [\"Public\", \"Group-{index % 17}\", \"{'été' if index % 7 == 0 else 'stable'}\"]",
            (f"history = [{{ rev = {1 + index % 9}, by = \"{owners[index % 3]}\" }}, "
             f"{{ rev = {2 + index % 9}, by = \"{owners[(index + 1) % 3]}\" }}]"),
            "[entry.attributes]",
            f'uri = "https://catalog.example/items/{index}?view=full&lang=ja"',
            f'owner = "{owners[index % 3]}"',
            f'mime = "{mimes[index % 3]}"',
            "",
        ]
    return "\n".join(lines)


def _canonical(tables: object) -> bytes:
    return json.dumps(tables, sort_keys=True, default=str, ensure_ascii=True).encode("ascii")


def catalog_toml_load(iterations: int) -> dict[str, int | float | str]:
    """Time one complete 512-record TOML load per iteration."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    text = catalog_document()
    input_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if input_digest != _EXPECTED_INPUT:
        raise WorkloadError(f"catalog TOML input changed: {input_digest}")

    elapsed = 0.0
    for _ in range(iterations):
        started = time.perf_counter()
        tables = tomllib.loads(text)
        elapsed += time.perf_counter() - started
        output_digest = hashlib.sha256(_canonical(tables)).hexdigest()
        if output_digest != _EXPECTED_OUTPUT:
            raise WorkloadError(f"catalog TOML output changed: {output_digest}")
    return {
        "operation_count": iterations,
        "digest": output_digest,
        "input_digest": input_digest,
        "elapsed_seconds": elapsed,
        "records_per_operation": len(tables["entry"]),
        "bytes_per_operation": len(text.encode("utf-8")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("catalog_toml_load",))
    parser.add_argument("--iterations", type=int, default=1)
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    try:
        result = catalog_toml_load(args.iterations)
    except WorkloadError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
