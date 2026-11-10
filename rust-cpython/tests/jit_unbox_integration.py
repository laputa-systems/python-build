import json
import os
import subprocess
import sys

PROGRAM = r'''
import _opcode
import gc
import json
import sys
import threading

WARM = 6000
EVENTS = []
TRACE_LINES = set()
MONITOR_LINES = set()
TOOL = 4


class FSub(float):
    def __del__(self):
        EVENTS.append(("FSub", float.hex(self)))


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def show(value):
    if isinstance(value, float):
        return [type(value).__name__, float.hex(value)]
    if isinstance(value, (list, tuple)):
        return [show(item) for item in value]
    if isinstance(value, dict):
        return sorted((key, show(item)) for key, item in value.items())
    return [type(value).__name__, repr(value)]


def tracer(frame, event, arg):
    if event == "line" and frame.f_code.co_filename == show.__code__.co_filename:
        TRACE_LINES.add((frame.f_code.co_name, frame.f_lineno - frame.f_code.co_firstlineno))
    return tracer


def monitor_line(code, line):
    if code.co_filename == show.__code__.co_filename:
        MONITOR_LINES.add((code.co_name, line - code.co_firstlineno))


def uops(function):
    names = set()
    code = function.__code__
    for offset in range(0, len(code.co_code), 2):
        try:
            executor = _opcode.get_executor(code, offset)
        except (ValueError, RuntimeError):
            continue
        names.update(entry[0] for entry in executor)
    return sorted(names)


def merged(n, a, b, c, d):
    acc = 0.0
    hits = 0
    for k in range(n):
        f = float(k)
        x = a * f + b * c
        acc = acc + x
        if a * f + d * d <= 2000.0:
            hits += 1
        y = (f ** 2.0) * a - b * d + c * f
        acc = acc - y
    return acc, hits


def exits(n, a, b, c, values):
    acc = 0.0
    for k in range(n):
        f = float(k)
        d = values[k]
        x = a * f + c * d
        acc = acc + x
    return acc


def deopts(n, a, b, steps):
    acc = 0.0
    errors = []
    for k in range(n):
        f = float(k)
        g = steps[k]
        try:
            q = (a * f + b * f) / (f - g)
            acc = acc + q
        except ZeroDivisionError as error:
            frame = error.__traceback__.tb_frame
            errors.append((k, str(error), frame.f_locals["f"], frame.f_locals["g"]))
    return acc, errors


def owned(n, a, b, c, items, point):
    acc = 0.0
    for k in range(n):
        acc = acc + (a * items[k] + b * c)
        acc = acc - (items[k] * b + c * a)
        acc = acc + (point.x * b + c * point.y)
    return acc


def branches(n, a, b, c, flags):
    acc = 0.0
    for k in range(n):
        f = float(k)
        flag = flags[k]
        acc = acc + ((a * f + b * f) + (1.5 if flag else 2.5) + -(c * f + a))
    return acc


def traced(n, a, b, c):
    acc = 0.0
    for k in range(n):
        if k == WARM + 100:
            sys.settrace(tracer)
            sys._getframe().f_trace = tracer
        elif k == WARM + 140:
            sys.settrace(None)
            sys._getframe().f_trace = None
        f = float(k)
        acc = acc + (a * f + b * c) * (c * f - a * b)
    return acc


def monitored(n, a, b, c):
    acc = 0.0
    for k in range(n):
        if k == WARM + 100:
            sys.monitoring.set_events(TOOL, sys.monitoring.events.LINE)
        elif k == WARM + 140:
            sys.monitoring.set_events(TOOL, 0)
        f = float(k)
        acc = acc + (a * f + b * c) * (c * f - a * b)
    return acc


def collected(n, a, b, c):
    acc = 0.0
    referents = []
    for k in range(n):
        if k == WARM + 100:
            gc.collect()
            frame = sys._getframe()
            referents = sorted(type(item).__name__ for item in gc.get_referents(frame))
        f = float(k)
        acc = acc + (a * f + b * c) * (c * f - a * b)
    return acc, referents


def generated(n, a, b, c):
    for k in range(n):
        f = float(k)
        yield a * f + b * c
        yield (a * f - c) < (b * f + c)


def threaded(n, a, b, c):
    box = []
    worker = threading.Thread(target=lambda: box.append(merged(n, a, b, c, a)))
    worker.start()
    worker.join()
    return box[0]


def run(n):
    a, b, c, d = float("1.25"), float("-0.75"), float("3.5"), float("0.125")
    values = [float(k % 7) + 0.5 for k in range(n)]
    values[WARM + 50] = FSub(2.25)
    values[WARM + 60] = 3
    values[WARM + 70] = FSub(-1.0)
    steps = [float(k) + 1.0 for k in range(n)]
    for k in range(WARM + 10, n, 97):
        steps[k] = float(k)
    flags = [True] * n
    items = [float(k % 11) + 0.25 for k in range(n)]
    items[WARM + 80] = FSub(1.5)
    point = Point(float("2.5"), float("-1.75"))
    for k in range(WARM + 5, n, 89):
        flags[k] = False
    sys.monitoring.use_tool_id(TOOL, "jit-unbox")
    sys.monitoring.register_callback(TOOL, sys.monitoring.events.LINE, monitor_line)
    def counts():
        return [sys.getrefcount(value) for value in (a, b, c, d, point.x, point.y, *items)]

    before = counts()
    results = {}
    probes = {}
    for name, function, args in (("merged", merged, (a, b, c, d)), ("exits", exits, (a, b, c, values)),
                                 ("deopts", deopts, (a, b, steps)), ("branches", branches, (a, b, c, flags)),
                                 ("owned", owned, (a, b, c, items, point))):
        results[name] = function(n, *args)
        probes[name] = uops(function)
    del function, args
    results.update({
        "traced": traced(n, a, b, c),
        "monitored": monitored(n, a, b, c),
        "collected": collected(n, a, b, c),
        "generated": list(generated(n, a, b, c)),
        "threaded": threaded(n, a, b, c),
    })
    del values
    gc.collect()
    after = counts()
    print(json.dumps({"results": show(results), "events": EVENTS,
                      "refcounts_moved": [[index, x - y] for index, (x, y) in enumerate(zip(after, before))
                                          if x != y],
                      "trace_lines": sorted(TRACE_LINES), "monitor_lines": sorted(MONITOR_LINES),
                      "uops": probes}, sort_keys=True))


run(WARM + 1200)
'''

EXPECTED_UOPS = {
    "merged": {"_BINARY_OP_ADD_FLOAT_UNBOXED", "_BINARY_OP_MULTIPLY_FLOAT_UNBOXED",
               "_BINARY_OP_SUBTRACT_FLOAT_UNBOXED", "_COMPARE_OP_FLOAT_UNBOXED",
               "_UNBOX_FLOAT_NOS", "_UNBOX_FLOAT_TOS", "_BOX_FLOAT"},
    "exits": {"_BINARY_OP_ADD_FLOAT_UNBOXED", "_BOX_FLOAT_STACK"},
    "deopts": {"_BINARY_OP_TRUEDIV_FLOAT_UNBOXED", "_BOX_FLOAT_STACK"},
    "branches": {"_BINARY_OP_ADD_FLOAT_UNBOXED", "_BOX_FLOAT_STACK", "_CLEAR_FLOAT_STACK"},
    "owned": {"_BINARY_OP_ADD_FLOAT_UNBOXED", "_BINARY_OP_MULTIPLY_FLOAT_UNBOXED",
              "_UNBOX_FLOAT_NOS", "_UNBOX_FLOAT_TOS"},
}
JIT_ONLY_FIELDS = ("uops", "monitor_lines")


def run(jit: bool) -> dict[str, object]:
    env = dict(os.environ, PYTHON_JIT="1" if jit else "0")
    completed = subprocess.run([sys.executable, "-c", PROGRAM], env=env,
                               capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        raise AssertionError(f"PYTHON_JIT={int(jit)} exited {completed.returncode}:\n"
                             f"{completed.stderr[-4000:]}")
    return json.loads(completed.stdout)


def main() -> int:
    jit, interpreter, again = run(True), run(False), run(False)
    failures = []
    for field in sorted(set(interpreter) - set(JIT_ONLY_FIELDS)):
        if jit[field] != interpreter[field]:
            failures.append(f"{field} differs with the JIT:\n  jit {jit[field]}\n  off {interpreter[field]}")
    if interpreter != again:
        failures.append("the JIT-off runs disagree; the program is not deterministic")
    if interpreter["monitor_lines"] != again["monitor_lines"]:
        failures.append("monitoring lines differ between JIT-off runs")
    if interpreter["refcounts_moved"]:
        failures.append(f"operand refcounts moved: {interpreter['refcounts_moved']}")
    for name, expected in EXPECTED_UOPS.items():
        missing = expected - set(jit["uops"][name])
        if missing:
            failures.append(f"{name} never ran unboxed: missing {sorted(missing)}")
    for failure in failures:
        print(f"FAIL  {failure}")
    if not failures:
        print(f"OK    {len(EXPECTED_UOPS)} unboxed kernels match with the JIT on and off")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
