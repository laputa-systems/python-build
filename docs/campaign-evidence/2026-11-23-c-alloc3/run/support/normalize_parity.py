import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build/wt-calloc3-run/docs/campaign-evidence/2026-11-23-c-alloc3/run")
PARITY = ROOT / "correctness/parity"
PATHS = re.compile(
    rb"/home/ubuntu/repos/python-build/wt-calloc3-(?:parent|cand)/rust-cpython/"
    rb"(?:stage-perf-calloc3-(?:parent|cand)|work/perf/perf-calloc3-(?:parent|cand))"
)
COMPILE_HASH = (
    "import hashlib,marshal,sys; "
    "code=compile(open(sys.argv[1],'rb').read(),sys.argv[2],'exec',dont_inherit=True); "
    "print(hashlib.sha256(marshal.dumps(code)).hexdigest())"
)


def normalized(source):
    return PATHS.sub(b"<MUSL-STAGE>", source)


def stage_for(arm):
    repo = Path("/home/ubuntu/repos/python-build") / f"wt-calloc3-{arm}"
    return repo / "rust-cpython" / f"stage-perf-calloc3-{arm}"


def compile_digest(arm, source, rel, directory):
    source_path = directory / f"{arm}.py"
    source_path.write_bytes(source)
    stage = stage_for(arm)
    env = dict(os.environ, LD_LIBRARY_PATH=str(stage / "lib"))
    result = subprocess.run(
        [str(stage / "bin/python3.16"), "-c", COMPILE_HASH, str(source_path), rel],
        check=True, capture_output=True, text=True, env=env,
    )
    return result.stdout.strip()


def main():
    arms = ("parent", "cand")
    raw = {arm: json.loads((PARITY / f"{arm}.json").read_text()) for arm in arms}
    differences = sorted(
        key for key in raw["parent"].keys() | raw["cand"].keys()
        if raw["parent"].get(key) != raw["cand"].get(key)
    )
    sysconfig_differences = [key for key in differences if "_sysconfigdata" in key]
    other_differences = [key for key in differences if key not in sysconfig_differences]
    report = [
        "Compile parity comparison; raw digest JSON is preserved in correctness/parity/.",
        f"parent entries: {len(raw['parent'])}",
        f"cand entries: {len(raw['cand'])}",
        f"raw differences={len(differences)} sysconfigdata={sysconfig_differences} "
        f"other_differences={other_differences}",
    ]
    copies = PARITY / "sysconfigdata"
    copies.mkdir(parents=True, exist_ok=True)

    for rel in sysconfig_differences:
        sources = {}
        for arm in arms:
            source_path = stage_for(arm) / "lib/python3.16" / rel
            source = source_path.read_bytes()
            (copies / f"{arm}-{Path(rel).name}").write_bytes(source)
            sources[arm] = normalized(source)
        equal_source = sources["parent"] == sources["cand"]
        with tempfile.TemporaryDirectory(prefix="parity-normalize-") as temporary:
            directory = Path(temporary)
            digests = {
                arm: compile_digest(arm, sources[arm], rel, directory)
                for arm in arms
            }
        report.append(
            f"{rel} normalized_source_equal={equal_source} "
            f"normalized_compile_hashes={digests} "
            f"normalized_compile_equal={digests['parent'] == digests['cand']}"
        )
    (PARITY / "parity-comparison.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
