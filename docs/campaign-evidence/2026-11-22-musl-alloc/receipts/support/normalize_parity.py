import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path("/home/ubuntu/repos/python-build/wt-integ/docs/campaign-evidence/2026-11-22-musl-alloc/receipts")
LANE = Path("/home/ubuntu/repos/python-build/wt-musl/rust-cpython")
PARITY = ROOT / "correctness/parity"
PATHS = re.compile(
    rb"/home/ubuntu/repos/python-build/wt-musl/rust-cpython/"
    rb"(?:stage-perf-musl-(?:base|mi|je)|work/perf/perf-musl-(?:base|mi|je))"
)
COMPILE_HASH = (
    "import hashlib,marshal,sys; "
    "code=compile(open(sys.argv[1],'rb').read(),sys.argv[2],'exec',dont_inherit=True); "
    "print(hashlib.sha256(marshal.dumps(code)).hexdigest())"
)


def normalized(source):
    return PATHS.sub(b"<MUSL-STAGE>", source)


def compile_digest(arm, source, rel, directory):
    source_path = directory / f"{arm}.py"
    source_path.write_bytes(source)
    stage = LANE / f"stage-perf-musl-{arm}"
    env = dict(os.environ, LD_LIBRARY_PATH=str(stage / "lib"))
    result = subprocess.run(
        [str(stage / "bin/python3.16"), "-c", COMPILE_HASH, str(source_path), rel],
        check=True, capture_output=True, text=True, env=env,
    )
    return result.stdout.strip()


def main():
    raw = {arm: json.loads((PARITY / f"{arm}.json").read_text()) for arm in ("base", "mi", "je")}
    report = [
        "Compile parity comparison; raw digest JSON is preserved in correctness/parity/.",
        f"base entries: {len(raw['base'])}",
    ]
    copies = PARITY / "sysconfigdata"
    copies.mkdir(parents=True, exist_ok=True)

    for arm in ("mi", "je"):
        candidate = raw[arm]
        differences = sorted(
            key for key in raw["base"].keys() | candidate.keys()
            if raw["base"].get(key) != candidate.get(key)
        )
        sysconfig_differences = [key for key in differences if "_sysconfigdata" in key]
        other_differences = [key for key in differences if key not in sysconfig_differences]
        report.append(
            f"{arm}: entries={len(candidate)} raw_differences={len(differences)} "
            f"sysconfigdata={sysconfig_differences} other_differences={other_differences}"
        )
        for rel in sysconfig_differences:
            sources = {}
            for side in ("base", arm):
                stage = LANE / f"stage-perf-musl-{side}"
                source_path = stage / "lib/python3.16" / rel
                source = source_path.read_bytes()
                (copies / f"{side}-{Path(rel).name}").write_bytes(source)
                sources[side] = normalized(source)
            equal_source = sources["base"] == sources[arm]
            with tempfile.TemporaryDirectory(prefix="parity-normalize-") as temporary:
                directory = Path(temporary)
                digests = {
                    side: compile_digest(side, source, rel, directory)
                    for side, source in sources.items()
                }
            report.append(
                f"{arm}: {rel} normalized_source_equal={equal_source} "
                f"normalized_compile_hashes={digests} "
                f"normalized_compile_equal={digests['base'] == digests[arm]}"
            )
    (ROOT / "parity-comparison.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
