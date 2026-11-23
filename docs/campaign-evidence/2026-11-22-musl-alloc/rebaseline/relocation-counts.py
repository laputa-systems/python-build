#!/usr/bin/env python3
import json
import os
import re
import subprocess
from pathlib import Path

REPO = Path("/home/ubuntu/repos/python-build")
LANE = REPO / "wt-rebase/rust-cpython"
OUT = Path(__file__).parent / "relocations"
STAGES = {
    "crel3-parent": "perf-rb2-crel3-parent-serc",
    "h2-candidate": "perf-rb2-h-serc",
}
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
OUT.mkdir(parents=True, exist_ok=True)
result = {}

for label, stage_name in STAGES.items():
    library = LANE / f"stage-{stage_name}/lib/libpython3.16.so.1.0"
    sections = subprocess.run(
        ["readelf", "-SW", str(library)], check=True, capture_output=True, text=True)
    relocations = subprocess.run(
        ["readelf", "-rW", str(library)], check=True, capture_output=True, text=True)
    (OUT / f"{label}.sections.txt").write_text(
        sections.stdout + sections.stderr, encoding="utf-8")
    (OUT / f"{label}.relocations.txt").write_text(
        relocations.stdout + relocations.stderr, encoding="utf-8")
    section = None
    for line in sections.stdout.splitlines():
        if re.search(r"\]\s+\.data\.rel\.ro\s+", line):
            fields = line.split()
            section = {"address": int(fields[3], 16), "size": int(fields[5], 16)}
            break
    if section is None:
        raise RuntimeError(f".data.rel.ro section missing in {library}")
    end = section["address"] + section["size"]
    offsets = []
    for line in relocations.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[2] == "R_X86_64_RELATIVE":
            offset = int(fields[0], 16)
            if section["address"] <= offset < end:
                offsets.append(offset)
    result[label] = {
        "stage": stage_name,
        "library": str(library),
        "section": section,
        "relative_relocations": len(offsets),
        "distinct_pages": len({offset // PAGE_SIZE for offset in offsets}),
    }

json_path = Path(__file__).parent / "relocation-counts.json"
json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
lines = [f"{key}: .data.rel.ro RELATIVE={item['relative_relocations']:,}; "
         f"distinct_pages={item['distinct_pages']}; "
         f"section_size={item['section']['size']:,} bytes"
         for key, item in result.items()]
(Path(__file__).parent / "relocation-counts.txt").write_text(
    "\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
