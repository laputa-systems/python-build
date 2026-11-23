import re
from pathlib import Path


RUN = Path(__file__).resolve().parents[1]
SOURCE = Path(
    "/home/ubuntu/repos/python-build/wt-calloc3-run/docs/campaign-evidence/"
    "2026-11-23-c-alloc3/run/raw.sha256"
)
REUSED = re.compile(
    r"parent-(?:corpus|nbody|scimark_fft|scimark_sparse_mat_mult|"
    r"setup-nbody|setup-scimark_fft|setup-scimark_sparse_mat_mult)-[12]\."
    r"(?:cg\.xz|annotate\.txt|exit|stderr|stdout)$"
)

lines = [
    "Reused C-ALLOC-3 parent Cachegrind and setup-only receipts; these runs were not repeated.",
    f"Source manifest: {SOURCE}",
]
for line in SOURCE.read_text().splitlines():
    digest, path = line.split("  ", 1)
    if (
        "/cachegrind/" in path or "/externalized/cachegrind/" in path
    ) and REUSED.search(path):
        lines.append(f"{digest}  {path}")

(RUN / "reused-parent-files.txt").write_text("\n".join(lines) + "\n")
print(f"listed {len(lines) - 2} reused parent receipt files")
