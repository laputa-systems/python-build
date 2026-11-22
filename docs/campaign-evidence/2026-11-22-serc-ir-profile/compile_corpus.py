import hashlib, marshal, sys
from pathlib import Path
mode, root = sys.argv[1], Path(sys.argv[2])
files = sorted(p for p in root.rglob("*.py") if "site-packages" not in p.parts)
n = ok = 0
h = hashlib.sha256()
for p in files:
    src = p.read_bytes()
    n += 1
    if mode == "read":
        continue
    try:
        co = compile(src, str(p), "exec", dont_inherit=True)
    except (SyntaxError, ValueError):
        continue
    ok += 1
    h.update(marshal.dumps(co))
print(mode, n, ok, h.hexdigest())
