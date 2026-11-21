#!/usr/bin/env python3
"""Executable libpython residency after importing one payload module.

usage: residency_probe.py <stage-python> <payload.py> <observations> > out.json
Modes: source (empty pycache prefix: payload compiled from source in-process)
and pyc (prefix pre-warmed: payload loaded from .pyc, no compile). Each
observation is a fresh interpreter. Reads /proc/self/smaps at the end of the
payload import and sums Rss/Pss over libpython mappings by permission.
"""
import json, os, shutil, statistics, subprocess, sys, tempfile
from pathlib import Path

python, payload, count = sys.argv[1], Path(sys.argv[2]).resolve(), int(sys.argv[3])
READER = r'''
import sys
sys.path.insert(0, sys.argv[1])
import probe_payload
out = {}
cur = None
for line in open("/proc/self/smaps"):
    head = line.split()
    if "-" in head[0] and len(head) >= 5 and ":" not in head[0]:
        cur = (head[1], head[5] if len(head) > 5 else "")
        continue
    if cur and "libpython" in cur[1] and head[0] in ("Rss:", "Pss:", "Private_Dirty:"):
        key = cur[0] + " " + head[0].rstrip(":")
        out[key] = out.get(key, 0) + int(head[1])
import json; print(json.dumps(out))
'''
def run(mode, scratch):
    src = Path(scratch) / "src"; src.mkdir(exist_ok=True)
    shutil.copy(payload, src / "probe_payload.py")
    prefix = Path(scratch) / f"pyc-{mode}"
    if mode == "source":
        shutil.rmtree(prefix, ignore_errors=True)
    elif not prefix.exists():
        subprocess.run([python, "-X", f"pycache_prefix={prefix}", "-c", READER, str(src)],
                       check=True, capture_output=True)
    done = subprocess.run([python, "-X", f"pycache_prefix={prefix}", "-c", READER, str(src)],
                          check=True, capture_output=True, text=True)
    return json.loads(done.stdout.strip().splitlines()[-1])
result = {"python": python, "payload": str(payload), "observations": count, "modes": {}}
with tempfile.TemporaryDirectory() as scratch:
    for mode in ("pyc", "source"):
        samples = [run(mode, scratch) for _ in range(count)]
        keys = sorted({k for s in samples for k in s})
        result["modes"][mode] = {"samples_kib": samples,
                                 "median_kib": {k: statistics.median(s.get(k, 0) for s in samples) for k in keys}}
json.dump(result, sys.stdout, indent=1, sort_keys=True); print()
