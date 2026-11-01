"""Output identity for traces built under adversarial tier-2 conditions.

J8 acceptance probe. Every case must print identical lines under the
candidate JIT interpreter and under each reference interpreter (the JIT-off
sibling and the incumbent JIT build): the register-cache allocation of a trace
may change which machine code runs, never what the program observes. Run:

    python3 rust-cpython/tests/parity_jit_stack_allocation.py \
        --candidate <candidate python> --reference <python> [--reference <python>]

Exit 0 when every reference matches the candidate line for line, 1 otherwise.
The candidate must report an active JIT.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

HOT = 20000


def emit() -> None:
    import gc
    import sys

    out = []

    def say(*parts):
        out.append(" ".join(str(part) for part in parts))

    def mixed_types():
        total = 0
        values = [3, 4.5, True, "ab", 2**70, None]
        for i in range(HOT * 3):
            value = values[0] if i < HOT else values[(i // 997) % 5]
            try:
                total = total + value
            except TypeError:
                total = total + len(str(value))
            if isinstance(total, str):
                total = len(total)
        return total

    say("mixed-types", mixed_types())

    class Counter(int):
        pass

    def subclass_switch():
        total = 0
        for i in range(HOT * 2):
            item = i if i < HOT else Counter(i)
            total += item * 2 - (item >> 1)
        return total

    say("subclass-switch", subclass_switch())

    def deep_stack():
        acc = 0.0
        for i in range(HOT * 2):
            a, b, c, d = i, i + 1, i + 2, i + 3
            acc += ((a * b - c) + (d * a - b)) * ((c + d) - (a * 0.5)) / (1.0 + (b % 7))
            pair = (a, (b, (c, (d, a))))
            acc += pair[1][1][1][0] - pair[0]
        return acc

    say("deep-stack", repr(deep_stack()))

    def raise_mid_trace():
        caught = 0
        for i in range(HOT * 2):
            try:
                x = (i * 3 + 1) // (i % 11 - 5 + (i % 11 == 5) * 0)
                caught += x
            except ZeroDivisionError:
                caught -= 1
        return caught

    say("raise-mid-trace", raise_mid_trace())

    def containers():
        data = []
        for i in range(HOT):
            data.append((i, float(i), str(i % 13), [i, i + 1]))
        total = 0
        for index, real, text, pair in data:
            total += index + int(real) + len(text) + pair[0] + pair[1]
        return total

    say("containers", containers())

    def generators():
        def gen(n):
            for i in range(n):
                received = yield i * 2
                if received:
                    i += received
        total = 0
        g = gen(HOT)
        next(g)
        for i in range(HOT - 1):
            total += g.send(i % 3)
        return total

    say("generators", generators())

    log = []

    class Noisy:
        def __init__(self, tag):
            self.tag = tag

        def __del__(self):
            log.append(self.tag)

    def finalization_order():
        for i in range(HOT):
            Noisy(i % 5)
        gc.collect()
        return len(log), sum(log), log[:12]

    say("finalization-order", finalization_order())

    def with_settrace():
        events = [0, 0]

        def tracer(frame, event, arg):
            events[0] += 1
            return tracer

        total = 0
        for i in range(HOT * 3):
            if i == HOT:
                sys.settrace(tracer)
            elif i == HOT + 50:
                sys.settrace(None)
            total += i % 7
        return total, events[0] > 0

    say("settrace-mid-trace", with_settrace())

    def with_monitoring():
        seen = []
        tool = sys.monitoring.DEBUGGER_ID
        sys.monitoring.use_tool_id(tool, "parity")
        total = 0
        try:
            for i in range(HOT * 3):
                if i == HOT:
                    sys.monitoring.register_callback(
                        tool, sys.monitoring.events.LINE, lambda code, line: seen.append(1))
                    sys.monitoring.set_events(tool, sys.monitoring.events.LINE)
                elif i == HOT + 10:
                    sys.monitoring.set_events(tool, 0)
                total += i % 5
        finally:
            sys.monitoring.set_events(tool, 0)
            sys.monitoring.free_tool_id(tool)
        return total, len(seen) > 0

    say("monitoring-mid-trace", with_monitoring())

    def watched_global_changes():
        namespace = {"scale": 3}
        exec("def f(n):\n    t = 0\n    for i in range(n):\n        t += i * scale\n    return t", namespace)
        results = [namespace["f"](HOT)]
        namespace["scale"] = 5
        results.append(namespace["f"](HOT))
        namespace["scale"] = 2.5
        results.append(namespace["f"](HOT))
        return results

    say("global-change", watched_global_changes())

    class Late:
        def __del__(self):
            print("late-finalizer-ran", file=sys.stdout)

    def exit_with_hot_code():
        global late
        late = Late()
        total = 0
        for i in range(HOT):
            total += i
        return total

    say("exit-with-hot-code", exit_with_hot_code())
    print("jit-enabled", hasattr(sys, "_jit") and sys._jit.is_enabled())
    print("\n".join(out))


def run(python: str, jit_required: bool) -> list[str]:
    result = subprocess.run([python, __file__, "--emit"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{python} failed:\n{result.stderr}")
    lines = result.stdout.splitlines()
    if jit_required and "jit-enabled True" not in lines:
        raise SystemExit(f"{python} did not report an enabled JIT")
    return [line for line in lines if not line.startswith("jit-enabled")]


def main() -> int:
    if sys.argv[1:] == ["--emit"]:
        emit()
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--reference", action="append", required=True)
    arguments = parser.parse_args()
    candidate = run(arguments.candidate, jit_required=True)
    failures = 0
    for reference in arguments.reference:
        expected = run(reference, jit_required=False)
        if candidate != expected:
            failures += 1
            print(f"MISMATCH against {reference}")
            for mine, theirs in zip(candidate, expected):
                if mine != theirs:
                    print(f"  candidate: {mine}\n  reference: {theirs}")
            if len(candidate) != len(expected):
                print(f"  line counts differ: {len(candidate)} vs {len(expected)}")
        else:
            print(f"OK {len(candidate)} lines identical against {reference}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
