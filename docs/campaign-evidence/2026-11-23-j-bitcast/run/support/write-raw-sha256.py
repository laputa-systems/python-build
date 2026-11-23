import hashlib
import json
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/home/ubuntu/exp-jbitcast1")
files = [
    path
    for path in RUN.rglob("*")
    if path.is_file() and not path.is_symlink() and path.name != "raw.sha256"
]
for directory in ("cachegrind", "profiles", "externalized", "stencil-output"):
    files.extend(
        path
        for path in (EXTERNAL / directory).rglob("*")
        if path.is_file() and not path.is_symlink()
    )
for filename in ("container-command.json", "container.stderr", "container.stdout"):
    path = EXTERNAL / filename
    if path.is_file() and not path.is_symlink():
        files.append(path)
reused = RUN / "reused-parent-files.txt"
if reused.exists():
    for line in reused.read_text().splitlines()[2:]:
        if "  " in line:
            _, name = line.split("  ", 1)
            path = Path(name)
            if not path.is_file():
                raise FileNotFoundError(path)
            files.append(path)
provenance = json.loads((RUN / "stage-provenance.json").read_text())
for stage in provenance.values():
    path = Path(stage["libpython"])
    if not path.is_file():
        raise FileNotFoundError(path)
    files.append(path)
for line in (RUN / "b1-stencil.txt").read_text().splitlines():
    if line.startswith("Header: "):
        path = Path(line.removeprefix("Header: "))
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append(path)
lines = []
for path in sorted(set(files), key=lambda item: str(item)):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lines.append(f"{digest}  {path}")
(RUN / "raw.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"hashed {len(lines)} files")
