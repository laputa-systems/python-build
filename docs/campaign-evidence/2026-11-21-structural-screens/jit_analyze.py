#!/usr/bin/env python3
"""Summarize per-workload pystats + JIT maps into trace-shape/boxing evidence (diagnostic).

usage: exp-jit-analyze.py <collect-dir> > summary.json
Per workload: tier-2 totals, object allocation/freelist counts, top uops by
execution count, float/int arithmetic uop counts, and from the JIT map the
traces containing >= 2 boxed-arithmetic uops (shape = uop name sequence).
"""
import json, re, sys
from collections import Counter
from pathlib import Path
root = Path(sys.argv[1])
ARITH = re.compile(r"^_(BINARY_OP_(ADD|SUBTRACT|MULTIPLY)_(FLOAT|INT)|BINARY_OP_(INPLACE_)?\w*FLOAT\w*|COMPARE_OP_(FLOAT|INT)|UNARY_NEGATIVE\w*FLOAT\w*)")
KEYS = ("Object allocations", "Object allocations from freelist", "Object frees", "Object frees to freelist",
        "Optimization traces created", "Optimization traces executed", "Optimization uops executed",
        "Executors invalidated")
out = {}
for wdir in sorted(p for p in root.iterdir() if p.is_dir()):
    stats = {}
    for f in wdir.glob("stats-*.txt"):
        for line in f.read_text().splitlines():
            k, _, v = line.rpartition(":")
            if k and v.strip().isdigit():
                stats[k.strip()] = stats.get(k.strip(), 0) + int(v)
    uops = {m.group(1): v for k, v in stats.items() if (m := re.fullmatch(r"uops\[(\w+)\]\.execution_count", k))}
    traces, cur = [], None
    mp = wdir / "jit-map.tsv"
    for line in (mp.read_text().splitlines() if mp.exists() else []):
        f = line.split("\t")
        if f[0] == "T": cur = []; traces.append(cur)
        elif f[0] == "U" and cur is not None: cur.append(f[5])
    shapes = Counter()
    for t in traces:
        if sum(bool(ARITH.match(u)) for u in t) >= 2:
            shapes[" ".join(u for u in t if ARITH.match(u) or u.startswith(("_GUARD", "_LOAD_FAST", "_STORE_FAST")))[:400]] += 1
    out[wdir.name] = {
        "files": len(list(wdir.glob("stats-*.txt"))), "totals": {k: stats.get(k) for k in KEYS},
        "uops_executed_total": sum(uops.values()),
        "top_uops": Counter(uops).most_common(15),
        "arith_uops": {k: v for k, v in sorted(uops.items()) if ARITH.match(k)},
        "traces": len(traces), "traces_with_2plus_arith": sum(shapes.values()),
        "top_arith_shapes": shapes.most_common(3),
    }
json.dump(out, sys.stdout, indent=1); print()
