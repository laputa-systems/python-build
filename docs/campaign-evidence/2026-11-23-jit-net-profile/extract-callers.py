#!/usr/bin/env python3
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CALLGRIND = ROOT / "callgrind"
PROFILE_NAMES = (
    ("parent-nbody-kernel", "nbody kernel"),
    ("parent-nbody-setup", "nbody setup-only"),
    ("parent-scimark_sparse_mat_mult-kernel", "sparse kernel"),
    ("parent-scimark_sparse_mat_mult-setup", "sparse setup-only"),
)
SUMMARY_RE = re.compile(r"^\s*([\d,]+)\s+\([^)]*\)\s+\*\s+(.*)$")
CALLER_RE = re.compile(
    r"^\s*([\d,]+)\s+\([^)]*\)\s+<\s+(.*?)\s+\(([\d,]+)x\)(?:\s+\[(.*)\])?\s*$"
)
PROGRAM_TOTAL_RE = re.compile(r"^\s*([\d,]+)\s+\([^)]*\)\s+PROGRAM TOTALS$")
JIT_CALLER_RE = re.compile(
    r"(_jit_|jit_opt|Python/jit|Python/optimizer|optimizer.*\.c|"
    r"_PyJIT|_PyJit|_PyOptimizer|jit_stencils)",
    re.IGNORECASE,
)


def short_name(name):
    if name.startswith("???:0x"):
        return "JIT code @ " + name.split(":", 1)[1].split(" ", 1)[0]
    name = name.replace(
        "/home/buildozer/aports/main/musl/src/musl-1.2.6/", "musl/"
    )
    marker = "/source/cpython-"
    if marker in name:
        suffix = name.split(marker, 1)[1]
        name = "CPython/" + suffix.split("/", 1)[1]
    return name.replace("???:", "")


def parse_profile(stem):
    lines = (CALLGRIND / f"{stem}.caller-tree.txt").read_text().splitlines()
    summaries = []
    for index, line in enumerate(lines):
        match = SUMMARY_RE.match(line)
        if match:
            summaries.append(
                (index, int(match.group(1).replace(",", "")), match.group(2))
            )

    def callers_before(summary_index):
        previous = max(
            (index for index, _, _ in summaries if index < summary_index),
            default=-1,
        )
        callers = []
        for line in lines[previous + 1 : summary_index]:
            match = CALLER_RE.match(line)
            if match:
                callers.append(
                    {
                        "ir": int(match.group(1).replace(",", "")),
                        "name": match.group(2),
                        "calls": int(match.group(3).replace(",", "")),
                    }
                )
        return callers

    total_ir = next(
        int(match.group(1).replace(",", ""))
        for line in lines
        if (match := PROGRAM_TOTAL_RE.match(line))
    )
    memcpy = next(
        summary
        for summary in summaries
        if ":memcpy " in summary[2]
    )
    callers = callers_before(memcpy[0])
    by_ir = sorted(callers, key=lambda item: (item["ir"], item["calls"]), reverse=True)
    by_calls = sorted(
        callers, key=lambda item: (item["calls"], item["ir"]), reverse=True
    )

    function_calls = {}
    for marker in (
        "Python/jit.c:_PyJIT_Compile",
        "Python/optimizer.c:_PyOptimizer_Optimize",
        "Python/optimizer.c:make_cold_executor",
    ):
        matching = [summary for summary in summaries if marker in summary[2]]
        function_calls[marker] = sum(
            item["calls"]
            for summary in matching
            for item in callers_before(summary[0])
        )

    jit_callers = [item for item in callers if JIT_CALLER_RE.search(item["name"])]

    return {
        "stem": stem,
        "total_ir": total_ir,
        "memcpy_ir": memcpy[1],
        "memcpy_calls": sum(item["calls"] for item in callers),
        "edges": callers,
        "by_ir": by_ir,
        "by_calls": by_calls,
        "summaries": summaries,
        "callers_before": callers_before,
        "jit_callers": jit_callers,
        "function_calls": function_calls,
    }


def table_for(profile):
    ir_ranks = {item["name"]: rank for rank, item in enumerate(profile["by_ir"], 1)}
    call_ranks = {
        item["name"]: rank for rank, item in enumerate(profile["by_calls"], 1)
    }
    names = {
        item["name"]
        for item in profile["by_ir"][:15] + profile["by_calls"][:15]
    }
    ordered = sorted(
        (item for item in profile["edges"] if item["name"] in names),
        key=lambda item: (
            ir_ranks.get(item["name"], 10_000),
            call_ranks.get(item["name"], 10_000),
        ),
    )
    result = [
        "| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in ordered:
        result.append(
            f"| `{short_name(item['name'])}` | {item['ir']:,} | "
            f"{item['calls']:,} | {ir_ranks.get(item['name'], '—')} | "
            f"{call_ranks.get(item['name'], '—')} |"
        )
    return "\n".join(result)


def level_two_for(profile):
    result = [
        "| Direct `memcpy` caller | Calls to `memcpy` | `memcpy` Ir | "
        "Caller above it | Calls to direct caller | Ir on edge |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for direct in profile["by_ir"][:3]:
        match = next(
            (
                summary
                for summary in profile["summaries"]
                if summary[2] == direct["name"]
                or summary[2].startswith(direct["name"] + " ")
            ),
            None,
        )
        upstream = (
            sorted(
                profile["callers_before"](match[0]),
                key=lambda item: (item["ir"], item["calls"]),
                reverse=True,
            )
            if match
            else []
        )
        if not upstream:
            result.append(
                f"| `{short_name(direct['name'])}` | {direct['calls']:,} | "
                f"{direct['ir']:,} | *(no caller row)* | — | — |"
            )
        for caller in upstream:
            result.append(
                f"| `{short_name(direct['name'])}` | {direct['calls']:,} | "
                f"{direct['ir']:,} | `{short_name(caller['name'])}` | "
                f"{caller['calls']:,} | {caller['ir']:,} |"
            )
    return "\n".join(result)


profiles = [parse_profile(stem) for stem, _ in PROFILE_NAMES]
by_stem = {profile["stem"]: profile for profile in profiles}
lines = [
    "# JIT net-kernel `memcpy` callers",
    "",
    "Attribution only. No verdict or product-code changes were made.",
    "",
    "The profiles were collected with Valgrind Callgrind 3.25.1 in the existing "
    "measurement container. The matching `musl-dbg=1.2.6-r3` package was "
    "installed there; the loader and debug file both had BuildID "
    "`d1656128169ecc566bb0afcbbcbda02c5ac8cd74`. Alpine's Valgrind APK does "
    "not include `callgrind_annotate`, so the profiles were annotated with the "
    "host's Callgrind `callgrind_annotate` 3.18.1 Perl script. It read the "
    "3.25.1 profiles successfully. The full caller trees use "
    "`--auto=no --show=Ir --threshold=100 --inclusive=yes --tree=caller`; "
    "threshold 100 emits all functions.",
    "",
    "The caller-edge `Ir` is the `memcpy` cost attributed to that immediate "
    "caller. Each table contains the union of the top 15 by `Ir` and the top "
    "15 by call count; rank columns identify each ordering.",
    "",
    "## Totals",
    "",
    "| Run | Total `Ir` | `memcpy` calls | `memcpy` inclusive `Ir` |",
    "|---|---:|---:|---:|",
]
for stem, label in PROFILE_NAMES:
    profile = by_stem[stem]
    lines.append(
        f"| {label} | {profile['total_ir']:,} | {profile['memcpy_calls']:,} | "
        f"{profile['memcpy_ir']:,} |"
    )
lines.extend(
    [
        "",
        "Simple kernel-minus-setup-only `memcpy Ir` differences are "
        f"{by_stem['parent-nbody-kernel']['memcpy_ir'] - by_stem['parent-nbody-setup']['memcpy_ir']:,} "
        "for nbody and "
        f"{by_stem['parent-scimark_sparse_mat_mult-kernel']['memcpy_ir'] - by_stem['parent-scimark_sparse_mat_mult-setup']['memcpy_ir']:,} "
        "for sparse; the corresponding call-count differences are "
        f"{by_stem['parent-nbody-kernel']['memcpy_calls'] - by_stem['parent-nbody-setup']['memcpy_calls']:,} "
        "and "
        f"{by_stem['parent-scimark_sparse_mat_mult-kernel']['memcpy_calls'] - by_stem['parent-scimark_sparse_mat_mult-setup']['memcpy_calls']:,}. "
        "These are arithmetic differences between separate process runs.",
    ]
)

for stem, label in PROFILE_NAMES:
    profile = by_stem[stem]
    lines.extend(
        [
            "",
            f"## {label}",
            "",
            table_for(profile),
        ]
    )

lines.extend(["", "## Top callers' callers", ""])
for stem, label in PROFILE_NAMES:
    profile = by_stem[stem]
    lines.extend(
        [
            f"### {label}",
            "",
            "The direct callers are the top three by inclusive `Ir`; the next "
            "column gives their callers one level farther up the tree.",
            "",
            level_two_for(profile),
            "",
        ]
    )

lines.extend(
    [
        "## JIT and executor callers",
        "",
        "| Run | Direct JIT/executor caller edges | Edge calls | Edge `Ir` | "
        "`_PyJIT_Compile` calls | `_PyOptimizer_Optimize` calls | "
        "`make_cold_executor` calls |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
)
for stem, label in PROFILE_NAMES:
    profile = by_stem[stem]
    jit = profile["jit_callers"]
    function_calls = profile["function_calls"]
    lines.append(
        f"| {label} | {len(jit)} | "
        f"{sum(item['calls'] for item in jit):,} | "
        f"{sum(item['ir'] for item in jit):,} | "
        f"{function_calls['Python/jit.c:_PyJIT_Compile']:,} | "
        f"{function_calls['Python/optimizer.c:_PyOptimizer_Optimize']:,} | "
        f"{function_calls['Python/optimizer.c:make_cold_executor']:,} |"
    )

for stem, label in PROFILE_NAMES:
    profile = by_stem[stem]
    if profile["jit_callers"]:
        lines.extend(
            [
                "",
                f"### {label}: selected direct JIT/executor callers",
                "",
                "| Caller | Calls | `memcpy` Ir |",
                "|---|---:|---:|",
            ]
        )
        for item in sorted(
            profile["jit_callers"], key=lambda value: (value["ir"], value["calls"]), reverse=True
        )[:8]:
            lines.append(
                f"| `{short_name(item['name'])}` | {item['calls']:,} | "
                f"{item['ir']:,} |"
            )

lines.extend(
    [
        "",
        "In both kernel profiles, the largest direct callers are unsymbolized "
        "address functions in the Callgrind tree. Their level-two caller is "
        "`_PyJIT_Entry`, linking these `memcpy` calls to generated JIT code. "
        "The setup-only profiles have no direct JIT/executor caller edges "
        "matching the listed function families and no `_PyJIT_Compile` calls.",
        "",
    ]
)
(ROOT / "attribution.md").write_text("\n".join(lines))
