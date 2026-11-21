#!/usr/bin/env python3
"""ELF section sizes and .text bytes by owning crate for one shared object (JSON)."""
import json, re, subprocess, sys
path = sys.argv[1]
sections = {}
for line in subprocess.run(["readelf", "-SW", path], capture_output=True, text=True).stdout.splitlines():
    m = re.match(r"\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+[0-9a-f]+\s+[0-9a-f]+\s+([0-9a-f]+)", line)
    if m: sections[m.group(1)] = int(m.group(2), 16)
groups = {}
CRATES = {"serc": r"serc(_[a-z_]+)?", "ruff": r"ruff_[a-z_]+"}
nm = subprocess.run(["nm", "-S", "-C", "--defined-only", path], capture_output=True, text=True).stdout
for line in nm.splitlines():
    parts = line.split(" ", 3)
    if len(parts) < 4 or parts[2] not in "tT": continue
    size, name = int(parts[1], 16), parts[3]
    head = name.lstrip("<").split("::", 1)[0] if "::" in name else None
    owner = "c_or_unmangled"
    if head is not None:
        owner = "rust_other"
        for key, pat in CRATES.items():
            if re.fullmatch(pat, head) or re.search(rf"<{pat}::|\bas {pat}::", name):
                owner = key; break
    groups[owner] = groups.get(owner, 0) + size
json.dump({"path": path, "sections": {k: v for k, v in sections.items() if not k.startswith(".debug")},
           "text_by_owner": groups}, sys.stdout, indent=1, sort_keys=True); print()
