import re
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/retry/correctness")
ARMS = ("base", "mi", "je")

failures = {}
for arm in ARMS:
    text = (ROOT / "suites" / f"{arm}-capi-verbose.log").read_text()
    failures[arm] = sorted(
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"^FAIL: (.+)$", line))
    )

lines = [
    "CPython suite-level failures from the preregistered runs:",
    "base, mi, je: test_capi and test_sys.",
    "The test_sys ID in all three is test_remote_exec_deleted_libpython.",
    "",
    "test_capi individual failure IDs from verbose diagnostic reruns:",
]
for arm in ARMS:
    lines.append(f"{arm} ({len(failures[arm])}):")
    lines.extend(f"  {failure}" for failure in failures[arm])
for arm in ("mi", "je"):
    lines.append(f"\n{arm} relative to base:")
    lines.append(f"  base-only: {sorted(set(failures['base']) - set(failures[arm]))}")
    lines.append(f"  {arm}-only: {sorted(set(failures[arm]) - set(failures['base']))}")

(ROOT / "failure-id-diff.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote failure-id-diff.txt: {len(failures['base'])}/{len(failures['mi'])}/{len(failures['je'])} CAPI IDs")
