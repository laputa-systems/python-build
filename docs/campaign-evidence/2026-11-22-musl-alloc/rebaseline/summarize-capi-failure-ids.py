#!/usr/bin/env python3
import re
from pathlib import Path

ROOT = Path(__file__).parent / "correctness-followup"
NAMES = ("jes2-parent", "h2", "jes3-candidate")
failures: dict[str, list[str]] = {}
counts: dict[str, int] = {}
for name in NAMES:
    text = (ROOT / f"{name}-capi-opt-verbose.log").read_text()
    failures[name] = re.findall(r"^FAIL: (.+)$", text, re.MULTILINE)
    match = re.search(r"^FAILED \(failures=(\d+)", text, re.MULTILINE)
    counts[name] = int(match.group(1)) if match else -1

lines = []
for name in NAMES:
    lines.append(f"{name}: failure_count={counts[name]} failure_ids={len(failures[name])}")
    lines.extend(f"  {failure}" for failure in failures[name])

for parent, candidate in (("jes2-parent", "h2"), ("h2", "jes3-candidate")):
    before = set(failures[parent])
    after = set(failures[candidate])
    lines.append(f"{parent} -> {candidate}:")
    lines.append(f"  added={sorted(after - before)}")
    lines.append(f"  removed={sorted(before - after)}")

output = "\n".join(lines) + "\n"
(ROOT / "capi-opt-failure-id-diff.txt").write_text(output)
print(output, end="")
