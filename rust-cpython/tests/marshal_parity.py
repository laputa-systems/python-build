"""Byte-identity differential for `marshal.dumps`: two perf builds.

The Rust marshal writer (`_marshal_rs`) must serialize to the same bytes as
the pristine control's C writer for every supported object graph: the default
version 6 for code objects, and every version 0-5 for plain values.

Usage:

    python3 rust-cpython/tests/marshal_parity.py \
        --baseline perf-upstream --candidate perf-rust

Build names resolve exactly like `perf.py` resolves a build: the name gets a
`perf-` prefix and its completed report must name the installed stage, whose
interpreter runs the probe. One batch subprocess runs per build.

The batch compiles every `.py` under the assembled source's `Lib/` (including
`Lib/test/`) at optimize 0, 1 and 2, marshals the code object, and digests
each result. Files the baseline itself cannot compile are listed and skipped
on both sides. It also dumps a fixed set of non-code values covering nested
containers with shared and repeated objects, interned and non-interned
strings, floats, complex numbers, bytes, sets and frozensets, integers of many
sizes, and the singleton values, at versions 0-5.

Exit 0 when every item matches, 1 on any mismatch or probe failure.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

LANE = Path(__file__).resolve().parent.parent
OPTIMIZE_LEVELS = (0, 1, 2)
VERSIONS = range(6)
BASELINE_LABEL = "baseline"
CANDIDATE_LABEL = "candidate"


def _load_perf():
    spec = importlib.util.spec_from_file_location("lane_perf", LANE / "perf.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load rust-cpython/perf.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["lane_perf"] = module
    spec.loader.exec_module(module)
    return module


def _resolve(perf, name: str) -> dict[str, str]:
    side = perf.resolve(name)
    return {"name": name, "ref": side["ref"], "python": str(side["python"]),
            "source": str(perf._paths(name)["source_parent"])}


def _source_lib(source_parent: Path) -> Path:
    for child in source_parent.iterdir():
        if child.is_dir() and (child / "configure").is_file() and (child / "Lib").is_dir():
            return child / "Lib"
    raise SystemExit(f"no assembled CPython source with Lib/ beneath {source_parent}")


def _encode(blob: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(blob).hexdigest(), "length": len(blob)}


def _emit(lib: Path) -> int:
    import marshal

    def dump(value, version=None, *, detail=False):
        try:
            blob = marshal.dumps(value) if version is None else marshal.dumps(value, version)
        except Exception as error:  # noqa: BLE001 - recorded, never raised
            return {"error": type(error).__name__}
        encoded = _encode(blob)
        if detail:
            encoded["hex"] = blob.hex()
        return encoded

    results: dict[str, dict[str, object]] = {"files": {}, "values": {}, "skip": []}
    for level in OPTIMIZE_LEVELS:
        for path in sorted(p for p in lib.rglob("*.py") if p.is_file()):
            relative = path.relative_to(lib).as_posix()
            item = f"opt{level}:{relative}"
            try:
                code = compile(path.read_bytes(), relative, "exec", optimize=level)
            except Exception as error:  # noqa: BLE001 - baseline cannot compile it either
                results["skip"].append(f"{item}:{type(error).__name__}")
                continue
            results["files"][item] = dump(code)

    shared_list = [1, 2, 3]
    shared_dict = {"a": 1, "b": [2]}
    shared_tuple = ("shared", 1)
    code_value = compile("def f():\n    return (1, 'a')\n", "<t>", "exec")
    values = {
        "shared-list-twice": [shared_list, shared_list],
        "shared-list-thrice": (shared_list, [shared_list], {"k": shared_list}),
        "shared-dict-twice": {"x": shared_dict, "y": shared_dict},
        "shared-tuple-twice": (shared_tuple, shared_tuple),
        "nested-shared": {"k": [shared_list, {"d": shared_dict}, (shared_tuple, shared_tuple)]},
        "small-int-repeated": (7, 7, 7, 0, 0, -1, -1),
        "int-sizes": (0, 1, -1, 255, 256, 2**31 - 1, 2**31, -2**31, -2**31 - 1, 2**63 - 1,
                      2**63, -2**63, 2**64, 2**100 + 7, -2**100 - 7, 2**1000, -2**1000),
        "floats": (0.0, -0.0, 1.5, float("inf"), float("-inf"), float("nan"), 1e308, 5e-324),
        "complex": (complex(1.0, 2.0), complex(-0.0, float("nan")), 1j),
        "bytes": (b"", b"x", b"xy", b"z" * 300, b"\x00\xff"),
        "strings": ("", "a", "abc", "héllo", "\U0001f600", "q" * 255, "q" * 256, "q" * 300),
        "interned": (sys.intern("shared_name"), sys.intern("shared_name"),
                     sys.intern("marshal_\u20ac")),
        "sets": (set(), frozenset(), {1, 2, 3}, frozenset([1, 2, 3]), frozenset([shared_tuple])),
        "singletons": (None, None, Ellipsis, StopIteration, True, False, True),
        "containers": ((), [], {}, (1,), [1], {"a": 1}, tuple(range(300))),
        "code-twice": (code_value, code_value),
        "code-once": (compile("x = 1", "<t>", "exec"),),
    }
    for name, value in values.items():
        results["values"][f"v6:{name}"] = dump(value, detail=True)
        for version in VERSIONS:
            results["values"][f"v{version}:{name}"] = dump(value, version, detail=True)
    print(json.dumps(results, sort_keys=True))
    return 0


def _probe(side: dict[str, str], lib: Path) -> dict[str, object]:
    result = subprocess.run(
        [side["python"], str(Path(__file__).resolve()), "--emit", str(lib)],
        capture_output=True, text=True, timeout=3600, check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"{side['name']}: probe failed ({result.returncode}):\n{result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"{side['name']}: probe emitted invalid JSON: {error}")


def _compare(kind: str, baseline: dict[str, object], candidate: dict[str, object]) -> list[dict[str, object]]:
    left_items, right_items = baseline[kind], candidate[kind]
    mismatches = []
    for key in sorted(set(left_items) | set(right_items)):
        left, right = left_items.get(key), right_items.get(key)
        if left == right:
            continue
        mismatches.append({"kind": kind, "item": key, "baseline": left, "candidate": right})
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", default="perf-upstream")
    parser.add_argument("--candidate", default="perf-rust")
    parser.add_argument("--emit", metavar="LIB", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.emit:
        return _emit(Path(args.emit))

    perf = _load_perf()
    sides = {label: _resolve(perf, name)
             for label, name in ((BASELINE_LABEL, args.baseline),
                                 (CANDIDATE_LABEL, args.candidate))}
    # Both builds assemble the same pinned source; probe one Lib/ tree.
    lib = _source_lib(Path(sides[BASELINE_LABEL]["source"]))
    results = {label: _probe(side, lib) for label, side in sides.items()}
    mismatches = (_compare("files", results[BASELINE_LABEL], results[CANDIDATE_LABEL])
                  + _compare("values", results[BASELINE_LABEL], results[CANDIDATE_LABEL]))
    baseline_skips = set(results[BASELINE_LABEL]["skip"])
    candidate_skips = set(results[CANDIDATE_LABEL]["skip"])
    report = {
        "baseline": {"name": args.baseline, "python": sides[BASELINE_LABEL]["python"]},
        "candidate": {"name": args.candidate, "python": sides[CANDIDATE_LABEL]["python"]},
        "source_lib": str(lib),
        "optimize_levels": list(OPTIMIZE_LEVELS),
        "versions": list(VERSIONS),
        "code_items": len(results[BASELINE_LABEL]["files"]),
        "value_items": len(results[BASELINE_LABEL]["values"]),
        "skipped_by_baseline": sorted(baseline_skips),
        "skipped_only_by_candidate": sorted(candidate_skips - baseline_skips),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not mismatches and not report["skipped_only_by_candidate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
