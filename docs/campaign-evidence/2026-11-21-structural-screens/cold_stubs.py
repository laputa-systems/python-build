#!/usr/bin/env python3
"""Noreturn stub bytes and placement in the largest Serc/ruff functions.

usage: cold_stubs.py <lib> [N] > out.json
A stub is the straight-line code from the previous block terminator
(jmp/ret/ud2/previous cold call) through a call into panic/assert/bounds/
alloc-failure machinery. Reports stub bytes and how many sit in the first
half of their function (interleaved with likely-hot code) vs the second half.
"""
import json, re, subprocess, sys
lib, n = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 60
syms = []
for line in subprocess.run(["nm", "-S", "-C", "--defined-only", lib], capture_output=True, text=True).stdout.splitlines():
    p = line.split(" ", 3)
    if len(p) == 4 and p[2] in "tT" and re.search(r"serc::|ruff_", p[3]):
        syms.append((int(p[1], 16), int(p[0], 16), p[3]))
syms.sort(reverse=True)
COLD = re.compile(r"\tcall.*(panic|assert_failed|unwrap_failed|expect_failed|slice_\w*fail|bounds|handle_alloc_error|capacity_overflow)")
TERM = re.compile(r"\t(jmp|ret|ud2)")
tot = {"size": 0, "stub_bytes": 0, "stubs": 0, "stub_bytes_first_half": 0}
rows = []
for size, addr, name in syms[:n]:
    out = subprocess.run(["objdump", "-d", "--no-show-raw-insn", "-C", f"--start-address={addr}",
                          f"--stop-address={addr + size}", lib], capture_output=True, text=True).stdout
    ins = [(int(l.split(":")[0], 16), l) for l in out.splitlines() if re.match(r"\s+[0-9a-f]+:\t", l)]
    ends = [a for a, _ in ins[1:]] + [addr + size]
    start, stub_bytes, stubs, first = ins[0][0] if ins else addr, 0, 0, 0
    for (a, l), nxt in zip(ins, ends):
        if COLD.search(l):
            b = nxt - start; stub_bytes += b; stubs += 1
            if start < addr + size / 2: first += b
            start = nxt
        elif TERM.search(l):
            start = nxt
    rows.append({"name": name[:160], "size": size, "stubs": stubs, "stub_bytes": stub_bytes, "stub_bytes_first_half": first})
    tot["size"] += size; tot["stub_bytes"] += stub_bytes; tot["stubs"] += stubs; tot["stub_bytes_first_half"] += first
json.dump({"lib": lib, "n": n, "total": tot, "functions": rows}, sys.stdout, indent=1); print()
