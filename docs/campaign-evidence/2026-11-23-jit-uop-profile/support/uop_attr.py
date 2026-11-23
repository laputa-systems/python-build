"""Attribute JIT-code self Ir and call Ir to uops using a PYTHON_JIT_MAP file."""
import bisect, collections, lzma, re, sys

cg_path, map_path = sys.argv[1], sys.argv[2]
ranges = []  # (start, end, trace_id, index, name)
traces = {}
tid = -1
for line in open(map_path):
    p = line.rstrip("\n").split("\t")
    if p[0] == "T":
        tid += 1
        traces[tid] = (p[7], p[6], int(p[5]))
    elif p[0] == "U":
        ranges.append((int(p[2], 16), int(p[3], 16), tid, int(p[4]), p[5]))
ranges.sort()
starts = [r[0] for r in ranges]
overlaps = sum(1 for a, b in zip(ranges, ranges[1:]) if b[0] < a[1])

def lookup(addr):
    i = bisect.bisect_right(starts, addr) - 1
    if i >= 0 and ranges[i][0] <= addr < ranges[i][1]:
        return ranges[i]
    return None

self_by = collections.Counter(); call_by = collections.Counter()
self_uop = collections.Counter(); call_uop = collections.Counter()
unmapped_jit = 0; total = 0; mapped_self = 0
pending = False; fn = ""
for line in lzma.open(cg_path, "rt", errors="replace"):
    if line.startswith("fn="):
        fn = line[3:].strip(); continue
    if line.startswith("calls="):
        pending = True; continue
    if not line or not line.startswith("0x"):
        continue
    parts = line.split()
    addr = int(parts[0], 16); ir = int(parts[2]) if len(parts) > 2 else 0
    r = lookup(addr)
    if pending:
        pending = False
        if r:
            call_by[r[4]] += ir; call_uop[(r[2], r[3], r[4])] += ir
        continue
    total += ir
    if r:
        self_by[r[4]] += ir; self_uop[(r[2], r[3], r[4])] += ir; mapped_self += ir
    elif fn.startswith("0x0000000006"):
        unmapped_jit += ir
print(f"traces {len(traces)} uops {len(ranges)} overlapping-ranges {overlaps}")
print(f"self Ir total {total:,}; mapped to uops {mapped_self:,}; unmapped JIT-page self {unmapped_jit:,}")
print("-- self Ir by uop name")
for k, v in self_by.most_common(30):
    print(f"{v:>14,} {100*v/mapped_self:6.2f}%  call {call_by[k]:>13,}  {k}")
print("-- call Ir (inclusive callees) by uop name")
for k, v in call_by.most_common(12):
    print(f"{v:>14,}  {k}")
print("-- hottest (trace, index, uop) by self+call")
comb = collections.Counter({k: self_uop[k] + call_uop[k] for k in set(self_uop) | set(call_uop)})
for (t, i, n), v in comb.most_common(25):
    print(f"{v:>14,} self {self_uop[(t,i,n)]:>12,} T{t} {traces[t][0]}@{traces[t][2]} #{i} {n}")
