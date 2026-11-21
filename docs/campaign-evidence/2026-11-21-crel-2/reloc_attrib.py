#!/usr/bin/env python3
"""Attribute R_X86_64_RELATIVE relocation targets (r_offset) to owning data symbols and crates.

usage: reloc_attrib.py <lib> > out.json
"""
import bisect, json, re, subprocess, sys
lib = sys.argv[1]
syms = []
for line in subprocess.run(["nm", "-S", "-C", "--defined-only", lib], capture_output=True, text=True).stdout.splitlines():
    p = line.split(" ", 3)
    if len(p) == 4 and p[2] in "dDrRbB":
        syms.append((int(p[0], 16), int(p[1], 16), p[3]))
syms.sort(); starts = [s[0] for s in syms]
def owner(name):
    if re.search(r"serc::|<serc|serc_", name): return "serc"
    if "ruff_" in name: return "ruff"
    if "::" in name: return "rust_other"
    return "c_or_unmangled"
counts, by_sym, total, unknown = {}, {}, 0, 0
for line in subprocess.run(["readelf", "-rW", lib], capture_output=True, text=True).stdout.splitlines():
    f = line.split()
    if len(f) >= 3 and f[2] == "R_X86_64_RELATIVE":
        total += 1; off = int(f[0], 16); i = bisect.bisect_right(starts, off) - 1
        if i >= 0 and syms[i][0] <= off < syms[i][0] + max(syms[i][1], 1):
            o = owner(syms[i][2]); counts[o] = counts.get(o, 0) + 1
            by_sym[syms[i][2][:140]] = by_sym.get(syms[i][2][:140], 0) + 1
        else:
            unknown += 1
top = sorted(by_sym.items(), key=lambda kv: -kv[1])[:25]
json.dump({"lib": lib, "relative_total": total, "unattributed": unknown, "by_owner": counts, "top_symbols": top}, sys.stdout, indent=1); print()
