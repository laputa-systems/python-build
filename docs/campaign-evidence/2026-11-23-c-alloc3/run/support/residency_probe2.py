#!/usr/bin/env python3
"""Corrected residency probe (2026-11-22). Supersedes residency_probe.py for new claims.

usage: residency_probe2.py <stage-python> <payload.py> <observations> <outdir> [--gdb]

Fixes over residency_probe.py: (1) the measurement reader is a precompiled
unchecked-hash .pyc run as a file, so the reader itself never reaches Serc or
the CPython compiler; (2) the payload is copied once (fixed mtime, sha256 recorded,
bytes retained in outdir) and never recopied; (3) no pycache_prefix: stdlib
modules load from the stage's own __pycache__ in both modes; (4) per-observation
validity: payload and payload-.pyc hashes unchanged, reader-reported origin of
probe_payload, no __pycache__ appearing in source mode; (5) whole-process
memory (smaps_rollup, heap/anon/libpython by permission, VmHWM) is recorded
alongside the old libpython-only keys. Modes, alternated per observation:
  pyc         payload imported from a pre-warmed unchecked-hash .pyc
              (intended compiler invocations: 0)
  source      payload imported from source (-B, never cached; intended: 1 compile)
  source-x20  payload source read and compile()d 20 times after the import
              (repeated-use behaviour; intended: 21 compiles)
With --gdb, one extra twin run per mode under gdb counts calls to
_PySerc_CompileSource and _PyAST_Compile and hits of every
serc::target::cpython_3_16_0a1* LazyLock initializer closure; twins are not
measurement samples.
"""
import hashlib, json, os, shutil, statistics, subprocess, sys, tempfile
from pathlib import Path

python, payload, count, outdir = sys.argv[1], Path(sys.argv[2]).resolve(), int(sys.argv[3]), Path(sys.argv[4])
use_gdb = "--gdb" in sys.argv[5:]
outdir.mkdir(parents=True, exist_ok=True)
MODES = ("pyc", "source", "source-x20")
FIXED_MTIME = 1_700_000_000

READER = r'''
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
'''

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def tree_listing(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))

result = {"python": python, "python_realpath": os.path.realpath(python),
          "libpython_sha256": None, "payload_name": payload.name,
          "payload_sha256": sha(payload), "observations": count, "modes": {},
          "probe_sha256": sha(__file__), "validity": [], "gdb": {}}
for lib in Path(python).resolve().parent.parent.glob("lib/libpython3.16.so.1.0"):
    result["libpython_sha256"] = sha(lib)
shutil.copy2(payload, outdir / "payload.py")

with tempfile.TemporaryDirectory() as scratch:
    scratch = Path(scratch)
    dirs = {}
    for mode in MODES:
        d = scratch / mode.replace("-", "_")
        d.mkdir()
        shutil.copyfile(payload, d / "probe_payload.py")
        os.utime(d / "probe_payload.py", (FIXED_MTIME, FIXED_MTIME))
        dirs[mode] = d
    (scratch / "reader.py").write_text(READER)
    reader_pyc = scratch / "reader.pyc"
    pyc_name = None
    # Setup compiles happen in separate processes, never in a measured one.
    subprocess.run([python, "-I", "-c",
        "import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True,"
        " invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)",
        str(scratch / "reader.py"), str(reader_pyc)], check=True)
    # pyc mode loads the payload as a sourceless module: remove the source after
    # compiling so only the .pyc can satisfy the import (no stat/hash fallbacks).
    pyc_target = dirs["pyc"] / "probe_payload.pyc"
    subprocess.run([python, "-I", "-c",
        "import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True,"
        " invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)",
        str(dirs["pyc"] / "probe_payload.py"), str(pyc_target)], check=True)
    (dirs["pyc"] / "probe_payload.py").unlink()
    result["reader_pyc_sha256"] = sha(reader_pyc)
    result["payload_pyc_sha256"] = sha(pyc_target)
    shutil.copy2(scratch / "reader.py", outdir / "reader.py")
    before = {m: tree_listing(d) for m, d in dirs.items()}

    def run(mode, wrapper=()):
        cmd = [*wrapper, python, "-I", "-B", str(reader_pyc), str(dirs[mode]), mode]
        done = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return done

    samples = {m: [] for m in MODES}
    for i in range(count):
        for mode in MODES:
            done = run(mode)
            sample = eval(done.stdout.strip().splitlines()[-1], {"__builtins__": {}})
            check = {"i": i, "mode": mode,
                     "tree_unchanged": tree_listing(dirs[mode]) == before[mode],
                     "reader_pyc_unchanged": sha(reader_pyc) == result["reader_pyc_sha256"],
                     "loader": sample["loader"], "origin": Path(sample["origin"]).name}
            if mode == "pyc":
                check["payload_pyc_unchanged"] = sha(pyc_target) == result["payload_pyc_sha256"]
                check["valid"] = (check["tree_unchanged"] and check["reader_pyc_unchanged"]
                                  and check["payload_pyc_unchanged"] and sample["loader"] == "SourcelessFileLoader")
            else:
                check["payload_unchanged"] = sha(dirs[mode] / "probe_payload.py") == result["payload_sha256"]
                check["valid"] = (check["tree_unchanged"] and check["reader_pyc_unchanged"]
                                  and check["payload_unchanged"] and sample["loader"] == "SourceFileLoader")
            result["validity"].append(check)
            samples[mode].append(sample)

    for mode in MODES:
        flat = []
        for s in samples[mode]:
            f = {}
            for group in ("lib", "categories", "rollup", "status"):
                for k, v in s[group].items():
                    f[f"{group}:{k}"] = v
            flat.append(f)
        keys = sorted({k for f in flat for k in f})
        result["modes"][mode] = {"samples_kib": flat,
                                 "median_kib": {k: statistics.median(f.get(k, 0) for f in flat) for k in keys}}

    if use_gdb:
        gdb_cmds = scratch / "count.gdb"
        gdb_cmds.write_text("\n".join([
            "set pagination off", "set breakpoint pending on", "set print thread-events off",
            "break _PySerc_CompileSource", "commands", "silent", "printf \"HIT serc\\n\"", "continue", "end",
            "break _PyAST_Compile", "commands", "silent", "printf \"HIT astcompile\\n\"", "continue", "end",
            "run", "quit"]) + "\n")
        for mode in MODES:
            cmd = ["gdb", "-q", "-batch", "-x", str(gdb_cmds),
                   "-ex", "", "--args", python, "-I", "-B", str(reader_pyc), str(dirs[mode]), mode]
            done = subprocess.run(cmd, capture_output=True, text=True)
            hits = [l.split()[1] for l in done.stdout.splitlines() if l.startswith("HIT ")]
            result["gdb"][mode] = {"exit": done.returncode,
                                   "serc_calls": hits.count("serc"),
                                   "ast_compile_calls": hits.count("astcompile")}
        lazy = scratch / "lazy.gdb"
        lazy.write_text("\n".join([
            "set pagination off", "set breakpoint pending on", "set print thread-events off",
            "start",
            "rbreak ^<serc::target::cpython_3_16_0a1.*{closure#0} as core::ops::function::FnOnce<()>>::call_once$",
            "commands 2-99", "silent", "info symbol $pc", "continue", "end",
            "continue", "quit"]) + "\n")
        for mode in MODES:
            cmd = ["gdb", "-q", "-batch", "-x", str(lazy),
                   "--args", python, "-I", "-B", str(reader_pyc), str(dirs[mode]), mode]
            done = subprocess.run(cmd, capture_output=True, text=True)
            inits = [l.split(" in section")[0] for l in done.stdout.splitlines() if "{closure#0}" in l and " in section" in l]
            result["gdb"].setdefault(mode, {})["lazy_inits"] = inits
            (outdir / f"gdb-lazy-{mode}.log").write_text(done.stdout + done.stderr)

result["all_valid"] = all(c["valid"] for c in result["validity"])
json.dump(result, sys.stdout, indent=1, sort_keys=True); print()
