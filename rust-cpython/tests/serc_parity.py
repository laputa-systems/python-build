#!/usr/bin/env python3
"""Census Serc's 3.16 bytecode against the CPython 3.16 oracle."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "rust-cpython")]

import perf

from benchmarks.harness.inputs import (
    LINUX_CP316_LOCK_PATH,
    MACOS_CP316_LOCK_PATH,
    load_lock,
    prepare_site,
)

lb = perf.lb
TOOLING_CORPUS = REPO / "benchmarks" / "workloads" / "fixtures" / "tooling" / "python_corpus"
DEFAULT_ORACLE = lb.LANE / f"stage-{perf.CONTROL}" / "bin" / "python3.16"
OPT_LEVELS = (0, 1, 2)
MODES = ("exec", "eval", "single")
MODE_CASES = lb.SERC_OVERLAY / "crates" / "serc" / "tests" / "fixtures" / "source_modes.json"

ORACLE_SCRIPT = r'''
import json
import marshal
import os
import sys
import types

FIELDS = (
    ("co_code", "co_code"),
    ("co_consts", "co_consts"),
    ("co_names", "co_names"),
    ("co_linetable", "co_linetable"),
    ("co_exceptiontable", "co_exceptiontable"),
    ("co_flags", "flags"),
    ("co_stacksize", "stacksize"),
    ("co_varnames", "localsplus"),
    ("co_cellvars", "localsplus"),
    ("co_freevars", "localsplus"),
    ("co_nlocals", "localsplus"),
)


def code_path(code):
    return getattr(code, "co_qualname", None) or code.co_name


def compare_code(expected, actual, path):
    for attribute, name in FIELDS:
        if attribute == "co_consts":
            continue
        left = getattr(expected, attribute, None)
        right = getattr(actual, attribute, None)
        if left != right:
            return name, path
    left = expected.co_consts
    right = actual.co_consts
    if len(left) != len(right):
        return "co_consts", path
    for left_item, right_item in zip(left, right):
        left_code = isinstance(left_item, types.CodeType)
        right_code = isinstance(right_item, types.CodeType)
        if left_code != right_code:
            return "co_consts", path
        if left_code:
            found = compare_code(left_item, right_item, path + "/" + code_path(right_item))
            if found is not None:
                return found
        elif left_item != right_item:
            return "co_consts", path
    return None


def compare(expected, actual_bytes):
    try:
        actual = marshal.loads(actual_bytes)
    except Exception:
        return "serialization", ""
    if type(expected) is not type(actual):
        return "serialization", ""
    if not isinstance(expected, types.CodeType):
        return ("serialization", "") if expected != actual else None
    found = compare_code(expected, actual, code_path(expected))
    if found is not None:
        return found
    return ("serialization", code_path(expected))


def main():
    manifest, results = sys.argv[1], sys.argv[2]
    records = []
    with open(manifest, "r", encoding="utf-8") as handle:
        items = json.load(handle)
    for item in items:
        record = {"file": item["file"], "optimize": item["optimize"]}
        try:
            with open(item["path"], "rb") as handle:
                source = handle.read()
            code = compile(
                source,
                item["filename"],
                item["mode"],
                dont_inherit=True,
                optimize=item["optimize"],
            )
            expected = marshal.dumps(code)
        except Exception as error:
            record["status"] = "refused"
            record["error"] = f"{type(error).__name__}: {error}"
            records.append(record)
            continue
        pyc = item.get("pyc")
        if not pyc or not os.path.exists(pyc):
            record["status"] = "no_output"
            records.append(record)
            continue
        with open(pyc, "rb") as handle:
            body = handle.read()[16:]
        if body == expected:
            record["status"] = "match"
        else:
            name, path = compare(code, body)
            record["status"] = "mismatch"
            record["class"] = name
            record["code_path"] = path
        records.append(record)
    with open(results, "w", encoding="utf-8") as handle:
        json.dump(records, handle)


if __name__ == "__main__":
    main()
'''


def build_cli(work: Path) -> Path:
    source = work / "source"
    source.mkdir(parents=True, exist_ok=True)
    lb._apply_serc_source(source)
    toolchain, _target = lb._toolchain()
    env = lb._environment(toolchain, offline=True, build_dir=work)
    command = [
        str(lb.CARGO_HOME / "bin" / "cargo"), "build", "--release", "--locked", "--offline",
        "-p", "serc-cli", "--manifest-path", str(source / "serc" / "Cargo.toml"),
        *lb.LANE_CARGO_SOURCE_ARGS,
    ]
    lb._require_command(
        command, cwd=lb.CARGO_CONFIG_ISOLATED_CWD, env=env, log=work / "cargo-build.log"
    )
    binary = Path(env["CARGO_TARGET_DIR"]) / "release" / "serc"
    if not binary.exists():
        raise SystemExit(f"Serc CLI missing at {binary}")
    return binary


def prepare_corpus(oracle: Path, corpus: Path) -> Path:
    stdlib = oracle.parent.parent / "lib" / "python3.16"
    if not stdlib.is_dir():
        raise SystemExit(f"oracle stdlib missing at {stdlib}")
    if corpus.exists():
        shutil.rmtree(corpus)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(stdlib, corpus / "stdlib", symlinks=True, ignore=ignore)
    lock = LINUX_CP316_LOCK_PATH if lb.IS_LINUX else MACOS_CP316_LOCK_PATH
    prepare_site(oracle, corpus / "site", groups={"django"}, lock=load_lock(lock))
    shutil.copytree(TOOLING_CORPUS, corpus / "tooling", ignore=ignore)
    write_generated_corpus(corpus / "generated_try_finally")
    return corpus


def prepare_mode_sources(corpus: Path) -> dict[str, list[Path]]:
    files = {mode: [] for mode in MODES}
    for case in json.loads(MODE_CASES.read_text()):
        mode = case["mode"]
        path = corpus / "source_modes" / mode / f"{case['name']}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case["source"])
        files[mode].append(path)
    return files


GENERATED_EXITS = {
    "return_const": ["return 1"],
    "return_expr": ["return x + 1"],
    "for_return_const": ["for i in range(2):", "    return 1"],
    "for_return_name": ["for i in range(2):", "    return x"],
    "while_return_const": ["while True:", "    return 1"],
    "for_cond_return": ["for i in range(2):", "    if i:", "        return 1"],
    "for_cond_continue_return": [
        "for i in range(2):",
        "    if i:",
        "        continue",
        "    return x",
    ],
    "for_cond_break_return": ["for i in range(2):", "    if i:", "        break", "return 1"],
    "nested_try_return": [
        "try:",
        '    log.append("nested")',
        "except ValueError:",
        "    pass",
        "return 1",
    ],
}

GENERATED_INNERS = {
    "raise": ['raise KeyError("k")'],
    "pass": ["pass"],
    "raise_else": [
        "try:",
        '    raise KeyError("k")',
        "except ValueError:",
        "    pass",
        "else:",
        '    log.append("else")',
    ],
}

GENERATED_WRAPPERS = {
    "except": (["try:"], ["except KeyError:", '    log.append("caught")']),
    "finally": (["try:"], ["finally:", '    log.append("outer")']),
    "with": (["with CM(log):"], []),
    "for": (["for j in range(2):"], []),
    "while": (["while True:"], []),
}

GENERATED_CHAINS = [
    [],
    ["except"],
    ["finally"],
    ["with"],
    ["for"],
    ["while"],
    ["finally", "except"],
    ["with", "finally"],
    ["for", "finally"],
    ["while", "with"],
]

GENERATED_CLASS = (
    "class CM:\n"
    "    def __init__(self, log): self.log = log\n"
    "    def __enter__(self): return self\n"
    '    def __exit__(self, *a): self.log.append("exit"); return False\n'
)


def indent_generated(lines: list[str], amount: int) -> list[str]:
    pad = " " * amount
    return [pad + line if line else line for line in lines]


def wrap_generated(lines: list[str], kind: str) -> list[str]:
    open_lines, close_lines = GENERATED_WRAPPERS[kind]
    return indent_generated(open_lines + indent_generated(lines, 4) + close_lines, 4)


def build_generated_program(chain: list[str], inner: list[str], exit_lines: list[str]) -> str:
    core = ["try:"] + indent_generated(inner, 4) + ["finally:"] + indent_generated(exit_lines, 4)
    body = indent_generated(core, 4)
    for kind in reversed(chain):
        body = wrap_generated(body, kind)
    return GENERATED_CLASS + "\ndef f(log, x):\n" + "\n".join(body) + "\n"


def write_generated_corpus(root: Path) -> None:
    root.mkdir(parents=True)
    index = 0
    for chain in GENERATED_CHAINS:
        for inner in GENERATED_INNERS.values():
            for exit_lines in GENERATED_EXITS.values():
                index += 1
                source = build_generated_program(chain, inner, exit_lines)
                (root / f"try_finally_{index:04d}.py").write_text(source)


def pyc_path(source: Path, optimize: int, mode: str) -> Path:
    name = source.name
    separator = name.rfind(".")
    if separator == 0:
        stem = name[1:]
    elif separator > 0:
        stem = name[:separator]
    else:
        stem = name
    tag = "cpython-316" if mode == "exec" else f"cpython-316.{mode}"
    suffix = f"{tag}.pyc" if optimize == 0 else f"{tag}.opt-{optimize}.pyc"
    return source.parent / "__pycache__" / f"{stem}.{suffix}"


def classify_serc(returncode: int, stderr: str, relative: Path) -> tuple[str, str]:
    """Classify one file's Serc invocation: compiled, refused, or a harness failure."""
    if returncode == 0:
        return "compiled", ""
    if returncode == 101 or "panicked at" in stderr:
        panic = next(
            (line for line in stderr.splitlines() if "panicked at" in line or "panicked" in line),
            "serc panicked",
        )
        return "panic", panic.strip()
    markers = (f"failed to compile {relative}: ", f"failed to decode {relative}: ")
    for line in stderr.splitlines():
        for marker in markers:
            index = line.find(marker)
            if index >= 0:
                reason = line[index + len(marker) :].strip()
                if reason.startswith(("this source requires CPython's parser:",
                                      "this source requires CPython's compiler:")):
                    return "routed", reason
                return "refused", reason
    detail = next((line for line in reversed(stderr.splitlines()) if line.strip()), "")
    return "error", f"serc exited {returncode}: {detail.strip()}"


def run_serc(binary: Path, corpus: Path, files: list[Path], work: Path,
             mode: str) -> tuple[dict, dict]:
    """Compile every file in its own process so a refusal cannot abort the census."""
    outcomes: dict[tuple[str, int], tuple[str, str]] = {}
    summary = {}
    workers = min(16, os.cpu_count() or 1)
    for optimize in OPT_LEVELS:
        log = work / f"serc-{optimize}.log"
        lines: list[str] = []

        def compile_one(path: Path, optimize: int = optimize) -> tuple[Path, str, str]:
            relative = path.relative_to(corpus)
            pyc_path(path, optimize, mode).unlink(missing_ok=True)
            command = [
                str(binary),
                "compile",
                str(relative),
                "--python-version",
                "3.16",
                "--mode",
                mode,
                "--optimize",
                str(optimize),
                "--force",
                "--quiet",
            ]
            result = subprocess.run(
                command, cwd=corpus, capture_output=True, text=True,
                check=False,
            )
            return (relative, *classify_serc(result.returncode, result.stderr, relative))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for relative, status, reason in pool.map(compile_one, files):
                outcomes[(str(relative), optimize)] = (status, reason)
                if status != "compiled":
                    lines.append(f"{status}\t{relative}\t{reason}")
        log.write_text("\n".join(sorted(lines)) + ("\n" if lines else ""))
        counts = Counter(
            status for (_, level), (status, _) in outcomes.items() if level == optimize
        )
        summary[str(optimize)] = {
            "exit": counts["panic"],
            "refused": counts["refused"],
            "routed": counts["routed"],
            "error": counts["error"],
            "compiled": counts["compiled"],
        }
    return summary, outcomes


def run_oracle(oracle: Path, corpus: Path, files: list[Path], work: Path,
               mode: str) -> dict:
    items = []
    for index, path in enumerate(files):
        for optimize in OPT_LEVELS:
            items.append(
                {
                    "id": f"{index}-{optimize}",
                    "file": str(path.relative_to(corpus)),
                    "path": str(path),
                    "filename": str(path.relative_to(corpus)),
                    "optimize": optimize,
                    "mode": mode,
                    "pyc": str(pyc_path(path, optimize, mode)),
                }
            )
    manifest = work / "oracle-manifest.json"
    manifest.write_text(json.dumps(items))
    results = work / "oracle-results.json"
    script = work / "oracle-batch.py"
    script.write_text(ORACLE_SCRIPT)
    result = subprocess.run(
        [str(oracle), str(script), str(manifest), str(results)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"oracle batch failed: {result.stderr[-2000:]}")
    return json.loads(results.read_text())


def census(
    records: list[dict],
    files: list[Path],
    runs: dict,
    outcomes: dict[tuple[str, int], tuple[str, str]],
    *,
    require_serc: frozenset[str] = frozenset(),
) -> dict:
    classes = Counter()
    per_optimize = {
        str(level): {
            "total": 0,
            "exact": 0,
            "mismatch": 0,
            "refused": 0,
            "routed": 0,
            "serc_refused": 0,
            "serc_error": 0,
            "no_output": 0,
        }
        for level in OPT_LEVELS
    }
    mismatches = []
    refused = {}
    serc_refused = Counter()
    serc_routes = Counter()
    serc_failures = []
    for record in records:
        level = str(record["optimize"])
        bucket = per_optimize[level]
        bucket["total"] += 1
        status = record["status"]
        serc_status, serc_reason = outcomes.get(
            (record["file"], record["optimize"]), ("missing", "no Serc outcome recorded")
        )
        failure = None
        if status == "refused":
            bucket["refused"] += 1
            refused.setdefault(record["file"], record["error"])
            if serc_status == "compiled":
                failure = "oracle_refused"
            elif serc_status in {"refused", "routed"}:
                continue
        elif serc_status == "routed":
            bucket["routed"] += 1
            serc_routes[serc_reason] += 1
            if record["file"] not in require_serc:
                continue
            failure = "unexpected_route"
        elif serc_status == "refused":
            bucket["serc_refused"] += 1
            serc_refused[serc_reason] += 1
            failure = "serc_refused"
        elif serc_status == "compiled":
            if status == "match":
                bucket["exact"] += 1
                continue
            bucket["mismatch" if status == "mismatch" else "no_output"] += 1
            failure = record.get("class", "no_output")

        if failure is None:
            bucket["serc_error"] += 1
            failure = "serc_error"
            serc_failures.append(
                {
                    "file": record["file"],
                    "optimize": record["optimize"],
                    "status": serc_status,
                    "reason": serc_reason,
                }
            )
        classes[failure] += 1
        mismatches.append(
            {
                "file": record["file"],
                "optimize": record["optimize"],
                "class": failure,
                "code_path": record.get("code_path", ""),
            }
        )
    mismatches.sort(key=lambda item: (item["class"], item["file"], item["optimize"]))
    return {
        "total_files": len(files),
        "total_cases": len(records),
        "exact_matches": sum(bucket["exact"] for bucket in per_optimize.values()),
        "mismatch_total": sum(classes.values()),
        "classes": dict(sorted(classes.items())),
        "per_optimize": per_optimize,
        "oracle_refused": {
            "count": len(refused),
            "files": [{"file": name, "error": error} for name, error in sorted(refused.items())],
        },
        "serc_refused": {
            "count": sum(
                bucket["serc_refused"] for bucket in per_optimize.values()
            ),
            "reasons": [
                {"reason": reason, "count": count}
                for reason, count in sorted(serc_refused.items())
            ],
        },
        "serc_routes": {
            "count": sum(serc_routes.values()),
            "reasons": [{"reason": reason, "count": count}
                        for reason, count in sorted(serc_routes.items())],
        },
        "serc_errors": serc_failures,
        "mismatches": mismatches,
        "serc_exit": {level: summary["exit"] for level, summary in runs.items()},
        "serc_status": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    parser.add_argument("--work", type=Path, default=lb.WORK / "serc-census")
    parser.add_argument("--json", type=Path, help="write the census JSON here")
    parser.add_argument("--mode", choices=MODES, action="append",
                        help="source mode to compare; repeatable, defaults to all three")
    args = parser.parse_args()

    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if not args.oracle.exists():
        raise SystemExit(f"oracle interpreter missing at {args.oracle}")

    with perf.host_lease("build", "serc census"):
        binary = build_cli(work)
        corpus = prepare_corpus(args.oracle, work / "corpus")
        files = sorted(path for path in corpus.rglob("*.py") if path.is_file())
        mode_sources = prepare_mode_sources(corpus)
        results = {}
        for mode in dict.fromkeys(args.mode or MODES):
            mode_work = work / mode
            mode_work.mkdir(parents=True, exist_ok=True)
            mode_files = files + mode_sources[mode]
            runs, outcomes = run_serc(binary, corpus, mode_files, mode_work, mode)
            records = run_oracle(args.oracle, corpus, mode_files, mode_work, mode)
            results[mode] = census(
                records, mode_files, runs, outcomes,
                require_serc=frozenset(str(path.relative_to(corpus)) for path in mode_sources[mode]),
            )
    result = {
        "modes": results,
        "total_cases": sum(item["total_cases"] for item in results.values()),
        "exact_matches": sum(item["exact_matches"] for item in results.values()),
        "mismatch_total": sum(item["mismatch_total"] for item in results.values()),
        "uncovered_modes": [mode for mode, item in results.items() if not item["exact_matches"]],
    }
    result["oracle"] = str(args.oracle)
    result["corpus"] = str(corpus)
    result["serc"] = str(binary)
    result["compiler_build_overrides"] = {
        name: os.environ[name]
        for name in ("CARGO_PROFILE_RELEASE_LTO",)
        if name in os.environ
    }
    text = json.dumps(result, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text)
    summary = {key: result[key] for key in (
        "total_cases", "exact_matches", "mismatch_total", "uncovered_modes"
    )}
    summary["modes"] = {
        mode: {key: item[key] for key in (
            "total_files", "total_cases", "exact_matches", "mismatch_total", "classes",
            "per_optimize", "serc_routes", "serc_refused", "serc_exit",
        )}
        for mode, item in results.items()
    }
    print(json.dumps(summary, indent=2))
    return 0 if result["mismatch_total"] == 0 and not result["uncovered_modes"] else 1


if __name__ == "__main__":
    sys.exit(main())
