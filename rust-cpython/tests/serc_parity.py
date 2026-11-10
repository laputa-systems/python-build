#!/usr/bin/env python3
"""Census Serc's 3.16 bytecode against the CPython 3.16 oracle."""

from __future__ import annotations

import argparse
import concurrent.futures
import http.server
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = Path(
    "/Users/josh/d/python-build/.work/wt/c0/.work/c0/archive/serc-bc563167.tar.gz"
)
DEFAULT_ORACLE = Path(
    "/Users/josh/d/python-build/.work/wt/r0/rust-cpython/stage-perf-upstream/bin/python3.16"
)
DEFAULT_LLVM = Path("/Users/josh/d/python-build/.work/wt/c0/.work/c0/llvm/prefix")
DEFAULT_CARGO_HOME = Path("/Users/josh/d/python-build/.work/wt/c0/.work/c0/cargo-home")
OPT_LEVELS = (0, 1, 2)
INDEX_PORT = 18736
INDEX_SOURCE = f"sparse+http://127.0.0.1:{INDEX_PORT}/"
CARGO_SOURCE_ARGS = [
    "--config",
    'source.crates-io.replace-with="local"',
    "--config",
    f'source.local.registry="{INDEX_SOURCE}"',
]

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
                "exec",
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


class IndexHandler(http.server.BaseHTTPRequestHandler):
    upstream = "https://index.crates.io/"

    def do_GET(self):
        url = self.upstream + self.path.lstrip("/")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as error:
            self.send_response(error.code)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):
        pass


def start_index_proxy():
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", INDEX_PORT), IndexHandler)
    except OSError:
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def assemble(archive: Path, overlay: Path, source: Path) -> None:
    marker = source / ".serc-assembled"
    stamp = f"{archive.stat().st_size}:{archive.stat().st_mtime_ns}"
    if not marker.is_file() or marker.read_text() != stamp:
        if source.exists():
            shutil.rmtree(source)
        source.mkdir(parents=True)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(source, filter="data")
        roots = [entry for entry in source.iterdir() if entry.is_dir()]
        if len(roots) != 1:
            raise SystemExit(f"unexpected archive layout in {source}")
        for entry in roots[0].iterdir():
            shutil.move(str(entry), str(source / entry.name))
        roots[0].rmdir()
        marker.write_text(stamp)
    for path in sorted(overlay.rglob("*")):
        if path.is_file():
            target = source / path.relative_to(overlay)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def build_environment(cargo_home: Path, llvm: Path, work: Path) -> dict:
    work = work.resolve()
    cargo_home = cargo_home.resolve()
    llvm = llvm.resolve()
    llvm_bin = llvm / "bin"
    env = dict(os.environ)
    env.update(
        {
            "RUSTUP_TOOLCHAIN": "nightly-2026-09-15",
            "CARGO_HOME": str(cargo_home),
            "CARGO_TARGET_DIR": str(work / "cargo-target"),
            "TMPDIR": str(work / "tmp"),
            "CC": str(llvm_bin / "clang"),
            "CXX": str(llvm_bin / "clang++"),
            "AR": str(llvm_bin / "llvm-ar"),
            "RANLIB": str(llvm_bin / "llvm-ranlib"),
            "HOST_CC": str(llvm_bin / "clang"),
            "HOST_CXX": str(llvm_bin / "clang++"),
            "HOST_AR": str(llvm_bin / "llvm-ar"),
        }
    )
    sdk = subprocess.run(
        ["xcrun", "--show-sdk-path"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if sdk:
        env["SDKROOT"] = sdk
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    return env


def cargo(source: Path, env: dict, args: list[str], log: Path, offline: bool) -> int:
    command = ["cargo", *args, *CARGO_SOURCE_ARGS]
    if offline:
        command.append("--offline")
    result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True)
    log.write_text(result.stdout + result.stderr)
    return result.returncode


def build_cli(source: Path, env: dict, work: Path) -> Path:
    log = work / "cargo-build.log"
    build = ["build", "--release", "-p", "serc-cli", "--locked"]
    if cargo(source, env, build, log, offline=True) != 0:
        server = start_index_proxy()
        try:
            if cargo(source, env, ["fetch", "--locked"], work / "cargo-fetch.log", offline=False) != 0:
                raise SystemExit(f"Serc fetch failed; see {work / 'cargo-fetch.log'}")
            if cargo(source, env, build, log, offline=True) != 0:
                raise SystemExit(f"Serc build failed; see {log}")
        finally:
            if server is not None:
                server.shutdown()
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
    shutil.copytree(
        stdlib,
        corpus,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return corpus


def pyc_path(source: Path, optimize: int) -> Path:
    name = source.name
    separator = name.rfind(".")
    if separator == 0:
        stem = name[1:]
    elif separator > 0:
        stem = name[:separator]
    else:
        stem = name
    suffix = "cpython-316.pyc" if optimize == 0 else f"cpython-316.opt-{optimize}.pyc"
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
                return "refused", line[index + len(marker) :].strip()
    detail = next((line for line in reversed(stderr.splitlines()) if line.strip()), "")
    return "refused", f"serc exited {returncode}: {detail.strip()}"


def run_serc(binary: Path, corpus: Path, files: list[Path], work: Path) -> tuple[dict, dict]:
    """Compile every file in its own process so a refusal cannot abort the census."""
    outcomes: dict[tuple[str, int], tuple[str, str]] = {}
    summary = {}
    workers = min(16, os.cpu_count() or 1)
    for optimize in OPT_LEVELS:
        log = work / f"serc-{optimize}.log"
        lines: list[str] = []

        def compile_one(path: Path) -> tuple[Path, str, str]:
            relative = path.relative_to(corpus)
            command = [
                str(binary),
                "compile",
                str(relative),
                "--python-version",
                "3.16",
                "--optimize",
                str(optimize),
                "--force",
                "--quiet",
            ]
            result = subprocess.run(
                command, cwd=corpus, capture_output=True, text=True
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
            "compiled": counts["compiled"],
        }
    return summary, outcomes


def run_oracle(oracle: Path, corpus: Path, files: list[Path], work: Path) -> dict:
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
                    "pyc": str(pyc_path(path, optimize)),
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
    )
    if result.returncode != 0:
        raise SystemExit(f"oracle batch failed: {result.stderr[-2000:]}")
    return json.loads(results.read_text())


def census(
    records: list[dict],
    files: list[Path],
    runs: dict,
    outcomes: dict[tuple[str, int], tuple[str, str]],
) -> dict:
    classes = Counter()
    per_optimize = {
        str(level): {
            "total": 0,
            "exact": 0,
            "mismatch": 0,
            "refused": 0,
            "serc_refused": 0,
            "serc_error": 0,
            "no_output": 0,
        }
        for level in OPT_LEVELS
    }
    mismatches = []
    refused = {}
    serc_refused = Counter()
    serc_failures = []
    for record in records:
        level = str(record["optimize"])
        bucket = per_optimize[level]
        bucket["total"] += 1
        status = record["status"]
        if status == "refused":
            bucket["refused"] += 1
            refused.setdefault(record["file"], record["error"])
            continue
        serc_status, serc_reason = outcomes.get(
            (record["file"], record["optimize"]), ("compiled", "")
        )
        if serc_status != "compiled":
            bucket["serc_refused" if serc_status == "refused" else "serc_error"] += 1
            if serc_status == "refused":
                serc_refused[serc_reason] += 1
            else:
                serc_failures.append(
                    {
                        "file": record["file"],
                        "optimize": record["optimize"],
                        "status": serc_status,
                        "reason": serc_reason,
                    }
                )
            continue
        if status == "match":
            bucket["exact"] += 1
        else:
            bucket["mismatch" if status == "mismatch" else "no_output"] += 1
            name = record.get("class", "no_output")
            classes[name] += 1
            mismatches.append(
                {
                    "file": record["file"],
                    "optimize": record["optimize"],
                    "class": name,
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
        "serc_errors": serc_failures,
        "mismatches": mismatches,
        "serc_exit": {level: summary["exit"] for level, summary in runs.items()},
        "serc_status": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=DEFAULT_ARCHIVE,
        help="pinned Serc archive",
    )
    parser.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    parser.add_argument("--llvm", type=Path, default=DEFAULT_LLVM)
    parser.add_argument("--cargo-home", type=Path, default=DEFAULT_CARGO_HOME)
    parser.add_argument("--work", type=Path, default=REPO / ".work" / "c1")
    parser.add_argument("--json", type=Path, help="write the census JSON here")
    parser.add_argument(
        "--overlay", type=Path, default=REPO / "rust-cpython" / "serc-overlay"
    )
    args = parser.parse_args()

    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if not args.cargo_home.exists():
        shutil.copytree(args.cargo_home, work / "cargo-home")
    cargo_home = work / "cargo-home" if (work / "cargo-home").exists() else args.cargo_home
    source = work / "serc-src"

    if not args.oracle.exists():
        raise SystemExit(f"oracle interpreter missing at {args.oracle}")
    sys.path.insert(0, str(REPO / "rust-cpython"))
    import perf

    with perf.host_lease("build", "serc census"):
        assemble(args.archive, args.overlay, source)
        env = build_environment(cargo_home, args.llvm, work)
        binary = build_cli(source, env, work)
        corpus = prepare_corpus(args.oracle, work / "corpus" / "lib" / "python3.16")
        files = sorted(path for path in corpus.rglob("*.py") if path.is_file())
        runs, outcomes = run_serc(binary, corpus, files, work)
        records = run_oracle(args.oracle, corpus, files, work)
    result = census(records, files, runs, outcomes)
    result["oracle"] = str(args.oracle)
    result["corpus"] = str(corpus)
    result["serc"] = str(binary)
    text = json.dumps(result, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text)
    print(json.dumps({key: result[key] for key in (
        "total_files", "total_cases", "exact_matches", "mismatch_total",
        "classes", "per_optimize", "serc_refused", "serc_exit",
    )}, indent=2))
    return 0 if result["mismatch_total"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
