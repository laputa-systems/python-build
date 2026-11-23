#!/usr/bin/env python3
"""Run under a stage interpreter: sha256 of marshal.dumps(compile(...)) per source file.

usage: <stage-python> compile_parity.py <Lib-dir> > digests.json
Compare two outputs with: python3 -c 'import json,sys;a,b=(json.load(open(p)) for p in sys.argv[1:]);print(len(a),len(b),sorted(k for k in a.keys()|b.keys() if a.get(k)!=b.get(k))[:50])' A B
"""
import hashlib, json, marshal, sys
from pathlib import Path
root = Path(sys.argv[1]); out = {}
for path in sorted(root.rglob("*.py")):
    rel = str(path.relative_to(root))
    try:
        code = compile(path.read_bytes(), rel, "exec", dont_inherit=True)
        out[rel] = hashlib.sha256(marshal.dumps(code)).hexdigest()
    except (SyntaxError, ValueError) as error:
        out[rel] = f"error:{type(error).__name__}"
json.dump(out, sys.stdout, indent=0, sort_keys=True); print()
