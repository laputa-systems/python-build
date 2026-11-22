
import sys
mode = sys.argv[2]
sys.path.insert(0, sys.argv[1])
import probe_payload
if mode == "source-x20":
    with open(probe_payload.__file__, "rb") as fh:
        text = fh.read()
    for _ in range(20):
        compile(text, probe_payload.__file__, "exec")
lib = {}
cat = {}
cur = None
with open("/proc/self/smaps") as fh:
    for line in fh:
        head = line.split()
        if "-" in head[0] and len(head) >= 5 and ":" not in head[0]:
            name = head[5] if len(head) > 5 else ""
            if "libpython" in name:
                kind = "libpython " + head[1]
            elif name == "[heap]":
                kind = "heap"
            elif name == "":
                kind = "anon"
            else:
                kind = "other"
            cur = (head[1], name, kind)
            continue
        if cur and head[0] in ("Rss:", "Pss:", "Private_Dirty:", "Anonymous:"):
            k = head[0].rstrip(":")
            if "libpython" in cur[1] and k != "Anonymous":
                key = cur[0] + " " + k
                lib[key] = lib.get(key, 0) + int(head[1])
            key = cur[2] + " " + k
            cat[key] = cat.get(key, 0) + int(head[1])
rollup = {}
with open("/proc/self/smaps_rollup") as fh:
    for line in fh:
        head = line.split()
        if len(head) == 3 and head[2] == "kB":
            rollup[head[0].rstrip(":")] = int(head[1])
status = {}
with open("/proc/self/status") as fh:
    for line in fh:
        head = line.split()
        if head and head[0] in ("VmHWM:", "VmRSS:", "RssAnon:", "RssFile:"):
            status[head[0].rstrip(":")] = int(head[1])
spec = probe_payload.__spec__
out = {"lib": lib, "categories": cat, "rollup": rollup, "status": status,
       "origin": spec.origin, "cached": spec.cached,
       "loader": type(spec.loader).__name__,
       "pyc_loaded": bool(getattr(probe_payload, "__cached__", None)) and type(spec.loader).__name__ == "SourcelessFileLoader"}
sys.stdout.write(repr(out) + "\n")
