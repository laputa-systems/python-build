"""Assemble uv-compatible release assets from per-triple dist/ directories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buildsys.uvmirror import UvMirrorError, assemble


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble release assets and uv-compatible metadata")
    parser.add_argument("--build", required=True, help="uv build id, a UTC date such as 20260923")
    parser.add_argument("--repo", required=True, help="owner/name of this repository")
    parser.add_argument("--dist", default="dist", help="per-triple dist/ root")
    parser.add_argument("--out", default="release", help="output directory")
    parser.add_argument("--budgets", default="release-budgets.json")
    args = parser.parse_args(argv)
    try:
        report = assemble(
            args.build,
            args.repo,
            Path(args.dist),
            Path(args.out),
            budgets_path=Path(args.budgets),
        )
    except UvMirrorError as error:
        print(f"FAIL uvmirror: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
