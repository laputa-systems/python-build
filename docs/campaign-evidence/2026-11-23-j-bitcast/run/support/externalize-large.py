import shutil
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/home/ubuntu/exp-jbitcast1")
LIMIT = 1_000_000
records = []
records.append(
    "Reused C-ALLOC-3 parent cachegrind/setup-only receipts are listed with "
    f"SHA-256 in {RUN / 'reused-parent-files.txt'}"
)

for path in sorted(RUN.rglob("*")):
    if (
        not path.is_file()
        or path.is_symlink()
        or path.name == "external-files.txt"
        or path.stat().st_size <= LIMIT
    ):
        continue
    relative = path.relative_to(RUN)
    destination = EXTERNAL / "externalized" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    records.append(f"run/{relative} -> {destination}")

for path in sorted((EXTERNAL / "externalized").rglob("*")):
    if path.is_file():
        relative = path.relative_to(EXTERNAL / "externalized")
        records.append(f"run/{relative} -> {path}")
for directory in ("cachegrind", "profiles", "stencil-output"):
    for path in sorted((EXTERNAL / directory).rglob("*")):
        if path.is_file():
            records.append(f"external evidence/input: {path}")
for filename in ("container-command.json", "container.stderr", "container.stdout"):
    path = EXTERNAL / filename
    if path.is_file():
        records.append(f"external evidence/input: {path}")

(RUN / "external-files.txt").write_text(
    "Files stored outside the run directory:\n" + "\n".join(records) + "\n",
    encoding="utf-8",
)
print(f"externalized {sum(' -> ' in line for line in records)} oversized run files")
