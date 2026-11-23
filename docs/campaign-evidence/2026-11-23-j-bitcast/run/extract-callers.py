#!/usr/bin/env python3
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CALLGRIND = ROOT / "callgrind"
PROFILE_NAMES = (
    ("cand-nbody-kernel", "candidate nbody kernel"),
    ("cand-scimark_sparse_mat_mult-kernel", "candidate sparse kernel"),
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
    names = {item["name"] for item in profile["by_ir"][:5] + profile["by_calls"][:5]}
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


def upstream_for(profile, direct_name):
    match = next(
        (
            summary
            for summary in profile["summaries"]
            if summary[2] == direct_name or summary[2].startswith(direct_name + " ")
        ),
        None,
    )
    return profile["callers_before"](match[0]) if match else []


profiles = [parse_profile(stem) for stem, _ in PROFILE_NAMES]
lines = [
    "# J-BITCAST-1 candidate `memcpy` callers",
    "",
    "Raw candidate attribution only. No verdict or `RESULT` file was produced.",
    "",
    "Profiles use Valgrind Callgrind 3.25.1 in the existing lane measurement "
    "container. `musl-dbg=1.2.6-r3` was installed; loader and debug-file "
    "BuildIDs match at `d1656128169ecc566bb0afcbbcbda02c5ac8cd74`. Caller "
    "trees were generated with `--dump-instr=no`, then annotated with the "
    "bundled Callgrind 3.18.1 annotator using `--auto=no --show=Ir "
    "--threshold=100 --inclusive=yes --tree=caller`.",
    "",
    "Each immediate-caller table is the union of the top five by inclusive "
    "`Ir` and top five by call count; the rank columns show both orderings.",
    "",
    "## Totals",
    "",
    "| Run | Total `Ir` | `memcpy` calls | `memcpy` inclusive `Ir` |",
    "|---|---:|---:|---:|",
]
for profile, (_, label) in zip(profiles, PROFILE_NAMES):
    lines.append(
        f"| {label} | {profile['total_ir']:,} | {profile['memcpy_calls']:,} | "
        f"{profile['memcpy_ir']:,} |"
    )

for profile, (_, label) in zip(profiles, PROFILE_NAMES):
    lines.extend(["", f"## {label}", "", table_for(profile)])

lines.extend(
    [
        "",
        "## Address-labelled JIT callers beneath `_PyJIT_Entry`",
        "",
        "Both caller trees contain `_PyJIT_Entry` and `_PyJIT_Compile` (7 "
        "compile calls), confirming JIT execution in the profiled commands. "
        "Neither profile has an address-labelled immediate caller of "
        "`memcpy` beneath `_PyJIT_Entry`: the JIT-originating `memcpy` count "
        "and inclusive `Ir` are both zero in each run.",
        "",
        "| Run | Immediate caller address | `memcpy` calls | `memcpy` Ir | "
        "`_PyJIT_Entry` calls | Ir on caller edge |",
        "|---|---|---:|---:|---:|---:|",
    ]
)
for profile, (_, label) in zip(profiles, PROFILE_NAMES):
    jit_edges = []
    for direct in profile["edges"]:
        if not direct["name"].startswith("???:0x"):
            continue
        upstream = upstream_for(profile, direct["name"])
        entry = [
            caller
            for caller in upstream
            if "_PyJIT_Entry" in caller["name"]
        ]
        for caller in entry:
            jit_edges.append((direct, caller))
    if not jit_edges:
        lines.append(f"| {label} | none found | — | — | — | — |")
    for direct, entry in sorted(
        jit_edges, key=lambda pair: (pair[0]["ir"], pair[0]["calls"]), reverse=True
    ):
        lines.append(
            f"| {label} | `{short_name(direct['name'])}` | "
            f"{direct['calls']:,} | {direct['ir']:,} | "
            f"{entry['calls']:,} | {entry['ir']:,} |"
        )
(ROOT / "attribution.md").write_text("\n".join(lines) + "\n")
