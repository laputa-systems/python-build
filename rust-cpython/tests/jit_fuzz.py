"""Differential JIT fuzzer: generated hot loops must behave identically with the JIT on and off.

Usage: python3 rust-cpython/tests/jit_fuzz.py --python BUILD/bin/python3 [--programs N] [--seed S]

Each generated program runs in a batch process twice, under PYTHON_JIT=1 and PYTHON_JIT=0. Values
(floats by bit pattern), exception logs, finalizer order, and reference counts of objects the
program creates must match exactly, as must the set of lines that tracing reports for generated
code on the program's own thread; other threads' lines, such as a joining main thread's, are
timing-dependent and ignored. Monitoring lines are collected and must be identical between the
two JIT-off runs, but they are not compared across the JIT: the JIT reports LINE events only at
side exits, so a mid-loop set_events sees fewer lines than the interpreter on the pristine
control too. Event counts are not compared: on a pristine JIT-off interpreter, the LINE count at
a set_events boundary varies by one loop iteration between identical runs. Each program also
reports pymalloc block growth over the loop's last fifth, after the tracing and monitoring
windows; the JIT side may exceed the interpreter side by at most LEAK_BLOCKS, since a refcount
leak grows with the loop and the program's own retention cancels between the two sides. The
JIT-off side runs twice, and a difference between those runs fails as a nondeterministic
program. On the JIT side, at least MIN_JIT_COVERAGE of the programs must run their loop in JIT
code at least once, or the run fails as untested.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

LOOP_ITERATIONS = 20000
JIT_WARMUP_ITERATIONS = 4096
BATCH_SIZE = 50
MIN_JIT_COVERAGE = 0.9
BATCH_TIMEOUT_SECONDS = 600
JIT_VARIANT_FIELDS = ("monitor_lines", "leak_blocks")

VARIABLES = ("a", "b", "c", "acc")
VALUES = (
    "0.0", "-0.0", "0.25", "-3.5", "1.5", "2.0**53", "1e308", "5e-324", "math.inf", "-math.inf",
    "math.nan", "0", "1", "-1", "7", "2**30 - 1", "2**30", "-2**30", "2**62", "-(2**63)",
    "True", "False", "FSub(2.5)", "FSub(-0.5)", "ISub(3)", "ISub(-9)",
)
BINARY = ("+", "-", "*", "/", "//", "%")
COMPARE = ("<", "<=", "==", "!=", ">", ">=")

PRELUDE = '''\
import gc
import json
import math
import struct
import sys
import threading

EVENTS = []


class FSub(float):
    def __del__(self):
        EVENTS.append(("FSub", float.hex(self)))


class ISub(int):
    def __del__(self):
        EVENTS.append(("ISub", int(self)))


class Box:
    __slots__ = ("v",)


def show(value):
    if type(value) is float:
        return ["float", float.hex(value)]
    if type(value) in (int, bool):
        return [type(value).__name__, repr(value)]
    if isinstance(value, float):
        return [type(value).__name__, float.hex(value)]
    if isinstance(value, int):
        return [type(value).__name__, int(value)]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [show(item) for item in value]]
    if isinstance(value, dict):
        return ["dict", sorted((key, show(item)) for key, item in value.items())]
    return [type(value).__name__, repr(value)]


JITTED = [False]


TRACE_LINES = set()
PROGRAM_THREAD = [None]


def tracer(frame, event, arg):
    if event == "line" and frame.f_code.co_filename == __file__ \
            and threading.get_ident() == PROGRAM_THREAD[0]:
        TRACE_LINES.add((frame.f_code.co_qualname, frame.f_lineno - frame.f_code.co_firstlineno))
    return tracer


MONITOR_TOOL = 4
MONITOR_LINES = set()


def monitor_line(code, line):
    if code.co_filename == __file__ and threading.get_ident() == PROGRAM_THREAD[0]:
        MONITOR_LINES.add((code.co_qualname, line - code.co_firstlineno))


def report(index, function, args):
    EVENTS.clear()
    TRACE_LINES.clear()
    MONITOR_LINES.clear()
    JITTED[0] = False
    leak_blocks = None
    try:
        value = function(LOOP_ITERATIONS, *args)
        leak_blocks = value[-1]
        result = ["ok", show(value[:-1])]
    except Exception as error:
        result = ["raised", type(error).__name__, str(error)]
    sys.settrace(None)
    gc.collect()
    print(json.dumps({"program": index, "result": result, "events": list(EVENTS),
                      "trace_lines": sorted(TRACE_LINES), "monitor_lines": sorted(MONITOR_LINES),
                      "leak_blocks": leak_blocks, "jitted": JITTED[0]}, sort_keys=True), flush=True)
'''


@dataclass(frozen=True)
class Program:
    index: int
    source: str


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.depth = 0

    def line(self, text: str) -> None:
        self.lines.append("    " * self.depth + text)


def _clamp(writer: _Writer, name: str) -> None:
    writer.line(f"if type({name}) is int and not -4611686018427387904 < {name} < 4611686018427387904:")
    writer.line(f"    {name} %= 1000003")


def _guarded(writer: _Writer, statement: str) -> None:
    writer.line("try:")
    writer.line(f"    {statement}")
    writer.line("except (ZeroDivisionError, OverflowError, TypeError, ValueError) as error:")
    writer.line("    errors[type(error).__name__] = errors.get(type(error).__name__, 0) + 1")


RAISING_DIVISOR_SHARE = 0.2
LEAK_BLOCKS = 1000
LEAK_NOISE_BLOCKS = 100


def _binary(rng: random.Random, left: str, right: str) -> str:
    operator = rng.choice(BINARY)
    if operator in ("/", "//", "%") and rng.random() >= RAISING_DIVISOR_SHARE:
        right = f"({right} or 1)"
    return f"{left} {operator} {right}"


def _statement(rng: random.Random, writer: _Writer, index: int, features: set[str]) -> None:
    target = rng.choice(VARIABLES)
    left, right = rng.choice(VARIABLES), rng.choice((*VARIABLES, *VALUES, "k"))
    kind = rng.choice(("arith", "arith", "arith", "compare", "cond", "helper", "container",
                       "attribute", "alias", "generator", "comprehension", "closure",
                       "drop", "flocals", "settrace", "monitor"))
    if kind == "arith":
        _guarded(writer, f"{target} = {_binary(rng, left, right)}")
        _clamp(writer, target)
    elif kind == "compare":
        writer.line(f"if {left} {rng.choice(COMPARE)} {right}:")
        writer.line("    hits += 1")
    elif kind == "cond":
        writer.line(f"if k % {rng.randint(2, 7)} == {rng.randint(0, 1)}:")
        writer.depth += 1
        _guarded(writer, f"{target} = {_binary(rng, left, rng.choice(VALUES))}")
        _clamp(writer, target)
        writer.depth -= 1
    elif kind == "helper":
        _guarded(writer, f"{target} = helper_{index}({left}, {right})")
        _clamp(writer, target)
    elif kind == "container":
        writer.line(f"buf[k % 4] = {left}")
        writer.line(f"{target} = buf[(k + 1) % 4]")
    elif kind == "attribute":
        writer.line(f"box.v = {left}")
        _guarded(writer, f"{target} = {_binary(rng, 'box.v', right)}")
        _clamp(writer, target)
    elif kind == "alias":
        _guarded(writer, f"{target} = {_binary(rng, left, left)}")
        _clamp(writer, target)
        writer.line(f"keep = {left}")
    elif kind == "generator":
        _guarded(writer, f"{target} = next(gen)")
    elif kind == "comprehension":
        _guarded(writer, f"{target} = sum([item * 0.5 for item in ({left}, {right}) "
                         "if type(item) in (int, float, bool)])")
    elif kind == "closure":
        writer.line(f"cell[0] = {left}")
        _guarded(writer, f"{target} = bump()")
        _clamp(writer, target)
    elif kind == "drop":
        writer.line(f"if k % {rng.randint(50, 500)} == 0:")
        writer.line(f"    {target} = FSub(k * 0.5) if k % 2 else ISub(k)")
    elif kind == "flocals":
        features.add("flocals")
        writer.line(f"if k == {rng.randint(JIT_WARMUP_ITERATIONS, LOOP_ITERATIONS - 1)}:")
        writer.line(f"    sys._getframe().f_locals[{target!r}] = {rng.choice(VALUES)}")
    elif kind == "settrace":
        features.add("settrace")
        start = rng.randint(LOOP_ITERATIONS // 2, LOOP_ITERATIONS * 3 // 4)
        writer.line(f"if k == {start}:")
        writer.line("    sys.settrace(tracer)")
        writer.line("    sys._getframe().f_trace = tracer")
        writer.line(f"elif k == {start + rng.randint(5, 200)}:")
        writer.line("    sys.settrace(None)")
        writer.line("    sys._getframe().f_trace = None")
    elif kind == "monitor":
        features.add("monitor")
        start = rng.randint(LOOP_ITERATIONS // 2, LOOP_ITERATIONS * 3 // 4)
        writer.line(f"if k == {start}:")
        writer.line("    sys.monitoring.set_events(MONITOR_TOOL, sys.monitoring.events.LINE)")
        writer.line(f"elif k == {start + rng.randint(5, 200)}:")
        writer.line("    sys.monitoring.set_events(MONITOR_TOOL, 0)")


def generate(seed: int, index: int) -> Program:
    rng = random.Random(seed * 1_000_003 + index)
    features: set[str] = set()
    writer = _Writer()
    helper_op = rng.choice(BINARY[:3])
    writer.line(f"def helper_{index}(x, y):")
    writer.line(f"    return x {helper_op} (y if type(y) is not int else y % 97)")
    writer.line("")
    writer.line(f"def gen_{index}(seed):")
    writer.line("    value = seed")
    writer.line("    while True:")
    writer.line(f"        value = value {rng.choice(('+', '-'))} 0.5 if type(value) is float else 0.5")
    writer.line("        yield value")
    writer.line("")
    writer.line(f"def program_{index}(n, a, b, c):")
    writer.depth = 1
    writer.line("PROGRAM_THREAD[0] = threading.get_ident()")
    writer.line(f"acc = {rng.choice(VALUES)}")
    writer.line("errors = {}")
    writer.line("hits = 0")
    writer.line("keep = None")
    writer.line("buf = [a, b, c, acc]")
    writer.line("box = Box()")
    writer.line("box.v = a")
    writer.line(f"gen = gen_{index}({rng.choice(VALUES)})")
    writer.line("cell = [a]")
    writer.line("def bump():")
    writer.line("    cell[0] = cell[0] + 1 if type(cell[0]) in (int, bool) else cell[0] * 1.0")
    writer.line("    return cell[0]")
    writer.line("for k in range(n):")
    writer.depth = 2
    writer.line("JITTED[0] = JITTED[0] or sys._jit.is_active()")
    writer.line("if k == n * 4 // 5:")
    writer.line("    gc.collect()")
    writer.line("    leak_baseline = sys.getallocatedblocks()")
    for _ in range(rng.randint(3, 9)):
        _statement(rng, writer, index, features)
    writer.depth = 1
    writer.line("gc.collect()")
    writer.line("leak_blocks = sys.getallocatedblocks() - leak_baseline")
    writer.line("tracked = [x for x in (a, b, c, acc, keep, box.v, *buf) if type(x) in (FSub, ISub)]")
    writer.line("refs = [sys.getrefcount(x) for x in tracked]")
    writer.line("return [acc, a, b, c, hits, errors, refs, buf, leak_blocks]")
    writer.depth = 0
    args = ", ".join(rng.choice(VALUES) for _ in range(3))
    threaded = rng.random() < 0.15
    writer.line("")
    if threaded:
        writer.line(f"def threaded_{index}(n, *args):")
        writer.line("    box = []")
        writer.line(f"    worker = threading.Thread(target=lambda: box.append(program_{index}(n, *args)))")
        writer.line("    worker.start()")
        writer.line("    worker.join()")
        writer.line("    return box[0]")
        writer.line(f"report({index}, threaded_{index}, ({args},))")
    else:
        writer.line(f"report({index}, program_{index}, ({args},))")
    return Program(index, "\n".join(writer.lines) + "\n")


def batch_source(programs: Sequence[Program]) -> str:
    body = "\n".join(program.source for program in programs)
    return (PRELUDE + f"LOOP_ITERATIONS = {LOOP_ITERATIONS}\n"
            "sys.monitoring.use_tool_id(MONITOR_TOOL, 'jit-fuzz')\n"
            "sys.monitoring.register_callback(MONITOR_TOOL, sys.monitoring.events.LINE, monitor_line)\n"
            + body)


def parse_reports(stdout: str) -> dict[int, dict[str, object]]:
    reports: dict[int, dict[str, object]] = {}
    for line in stdout.splitlines():
        record = json.loads(line)
        reports[int(record["program"])] = record
    return reports


@dataclass(frozen=True)
class Mismatch:
    program: int
    jit: dict[str, object] | None
    interpreter: dict[str, object] | None


def compare(jit: dict[int, dict[str, object]],
            interpreter: dict[int, dict[str, object]],
            *, ignore: Sequence[str] = ()) -> list[Mismatch]:
    ignored = {"jitted", *ignore}
    mismatches = []
    for program in sorted(set(jit) | set(interpreter)):
        left, right = jit.get(program), interpreter.get(program)
        strip = lambda record: None if record is None else {
            key: value for key, value in record.items() if key not in ignored}
        if strip(left) != strip(right):
            mismatches.append(Mismatch(program, left, right))
    return mismatches


def leak_mismatches(left: dict[int, dict[str, object]],
                    right: dict[int, dict[str, object]], *,
                    tolerance: int, excess_only: bool) -> list[Mismatch]:
    mismatches = []
    for program in sorted(set(left) & set(right)):
        first, second = left[program], right[program]
        first_blocks, second_blocks = first.get("leak_blocks"), second.get("leak_blocks")
        if not isinstance(first_blocks, int) or not isinstance(second_blocks, int):
            continue
        difference = first_blocks - second_blocks
        exceeded = difference > tolerance if excess_only else abs(difference) > tolerance
        if exceeded:
            mismatches.append(Mismatch(program, first, second))
    return mismatches


def batch_command(python: Path, source: Path, jit: bool) -> tuple[list[str], dict[str, str]]:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(Path.home()),
        "TMPDIR": str(source.parent),
        "PYTHONHASHSEED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_JIT": "1" if jit else "0",
    }
    return [str(python), "-s", "-P", str(source)], env


def _run_batch(python: Path, source: Path, jit: bool) -> str:
    argv, env = batch_command(python, source, jit)
    completed = subprocess.run(argv, env=env, capture_output=True, check=False,
                               text=True, timeout=BATCH_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise RuntimeError(f"{source.name} exited {completed.returncode} with "
                           f"PYTHON_JIT={'1' if jit else '0'}:\n{completed.stderr[-4000:]}")
    return completed.stdout


@dataclass(frozen=True)
class FuzzResult:
    programs: int
    mismatches: list[Mismatch]
    nondeterministic: list[Mismatch]
    programs_jitted: int
    sources: dict[int, str]


def fuzz(python: Path, *, programs: int, seed: int, jobs: int, scratch: Path) -> FuzzResult:
    generated = [generate(seed, index) for index in range(programs)]
    batches = [generated[start:start + BATCH_SIZE] for start in range(0, programs, BATCH_SIZE)]
    paths = []
    for number, batch in enumerate(batches):
        path = scratch / f"batch-{seed}-{number:04d}.py"
        path.write_text(batch_source(batch), encoding="utf-8")
        paths.append(path)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        jit_out = list(pool.map(lambda path: _run_batch(python, path, True), paths))
        interpreter_out = list(pool.map(lambda path: _run_batch(python, path, False), paths))
        repeat_out = list(pool.map(lambda path: _run_batch(python, path, False), paths))
    jit_reports: dict[int, dict[str, object]] = {}
    interpreter_reports: dict[int, dict[str, object]] = {}
    for stdout in jit_out:
        jit_reports.update(parse_reports(stdout))
    repeat_reports: dict[int, dict[str, object]] = {}
    for stdout in interpreter_out:
        interpreter_reports.update(parse_reports(stdout))
    for stdout in repeat_out:
        repeat_reports.update(parse_reports(stdout))
    nondeterministic = compare(interpreter_reports, repeat_reports, ignore=("leak_blocks",))
    nondeterministic += leak_mismatches(interpreter_reports, repeat_reports,
                                        tolerance=LEAK_NOISE_BLOCKS, excess_only=False)
    nondeterministic.sort(key=lambda mismatch: mismatch.program)
    unstable = {mismatch.program for mismatch in nondeterministic}
    covered = sum(1 for record in jit_reports.values() if record["jitted"] is True)
    mismatches = [mismatch for mismatch in compare(jit_reports, interpreter_reports,
                                                   ignore=JIT_VARIANT_FIELDS)
                  if mismatch.program not in unstable]
    mismatches += [mismatch for mismatch in
                   leak_mismatches(jit_reports, interpreter_reports,
                                   tolerance=LEAK_BLOCKS, excess_only=True)
                   if mismatch.program not in unstable]
    mismatches.sort(key=lambda mismatch: mismatch.program)
    return FuzzResult(programs, mismatches, nondeterministic, covered,
                      {program.index: program.source for program in generated})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--programs", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--keep", type=Path, help="write each mismatching program here")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="jit-fuzz-") as temporary:
        result = fuzz(args.python, programs=args.programs, seed=args.seed, jobs=args.jobs,
                      scratch=Path(temporary))
    coverage = result.programs_jitted / result.programs
    for mismatch in result.mismatches[:5]:
        fields = sorted(key for key in {*(mismatch.jit or {}), *(mismatch.interpreter or {})}
                        if key != "jitted"
                        and (mismatch.jit or {}).get(key) != (mismatch.interpreter or {}).get(key))
        print(f"MISMATCH program {mismatch.program} (seed {args.seed}) differs in {', '.join(fields)}")
        for key in fields:
            left, right = (mismatch.jit or {}).get(key), (mismatch.interpreter or {}).get(key)
            if isinstance(left, list) and isinstance(right, list):
                left_items = {json.dumps(item) for item in left}
                right_items = {json.dumps(item) for item in right}
                print(f"  {key} only with JIT:    {sorted(left_items - right_items)[:20]}")
                print(f"  {key} only without JIT: {sorted(right_items - left_items)[:20]}")
            else:
                print(f"  jit {key}:         {json.dumps(left)[:600]}")
                print(f"  interpreter {key}: {json.dumps(right)[:600]}")
        if args.keep is not None:
            args.keep.mkdir(parents=True, exist_ok=True)
            (args.keep / f"program-{args.seed}-{mismatch.program}.py").write_text(
                batch_source([Program(mismatch.program, result.sources[mismatch.program])]),
                encoding="utf-8")
    print(json.dumps({"programs": result.programs, "seed": args.seed,
                      "mismatches": len(result.mismatches),
                      "nondeterministic": len(result.nondeterministic),
                      "jit_coverage": round(coverage, 4)}, sort_keys=True))
    if result.mismatches:
        return 1
    if result.nondeterministic:
        print(f"FAIL  {len(result.nondeterministic)} generated programs differ between two JIT-off "
              f"runs (first: {result.nondeterministic[0].program}); the generator is at fault")
        return 1
    if coverage < MIN_JIT_COVERAGE:
        print(f"FAIL  JIT coverage {coverage:.1%} is below {MIN_JIT_COVERAGE:.0%}; "
              "the JIT did not run, so nothing was tested")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
