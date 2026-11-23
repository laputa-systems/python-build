import shutil
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/home/ubuntu/exp-calloc3")
LIMIT = 1_000_000
records = []

for path in sorted(RUN.rglob("*")):
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= LIMIT:
        continue
    relative = path.relative_to(RUN)
    destination = EXTERNAL / "externalized" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    records.append(f"run/{relative} -> {destination}")

for path in sorted(EXTERNAL.rglob("*")):
    if path.is_file():
        relative = path.relative_to(EXTERNAL)
        if relative.parts[0] == "externalized":
            continue
        records.append(f"external evidence/input: {path}")

(RUN / "external-files.txt").write_text(
    "Files stored outside the run directory:\n" + "\n".join(records) + "\n",
    encoding="utf-8",
)
print(f"externalized {sum(' -> ' in line for line in records)} oversized run files")
