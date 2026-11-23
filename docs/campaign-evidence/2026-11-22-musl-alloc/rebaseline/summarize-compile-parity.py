#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).parent / "correctness-followup"


def read_map(name: str) -> dict[str, str]:
    text = (ROOT / f"{name}-parity.log").read_text()
    start = text.find("{")
    if start < 0:
        raise SystemExit(f"{name}: JSON output not found")
    value = json.loads(text[start:])
    if "files" in value:
        value = value["files"]
    return value


parent = read_map("crel3-parent")
candidate = read_map("h2")
added = sorted(candidate.keys() - parent.keys())
removed = sorted(parent.keys() - candidate.keys())
changed = sorted(
    key for key in parent.keys() & candidate.keys()
    if parent[key] != candidate[key]
)
lines = [
    f"CREL3 compile parity: parent files={len(parent)} candidate files={len(candidate)}",
    f"added={len(added)} removed={len(removed)} changed={len(changed)}",
]
for label, paths in (("added", added), ("removed", removed), ("changed", changed)):
    lines.extend(f"{label}: {path}" for path in paths)
output = "\n".join(lines) + "\n"
(ROOT / "compile-parity-diff.txt").write_text(output)
print(output, end="")
