import hashlib
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/home/ubuntu/exp-calloc3")
files = []
for root in (RUN, EXTERNAL):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            if root == RUN and path.name == "raw.sha256":
                continue
            files.append(path)
lines = []
for path in sorted(set(files), key=lambda item: str(item)):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lines.append(f"{digest}  {path}")
(RUN / "raw.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"hashed {len(lines)} files")
