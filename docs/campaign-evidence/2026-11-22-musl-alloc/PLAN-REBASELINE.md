# OD23 musl rebaseline: merged candidates and J-ES-3 on the selected allocator

Written before any rebaseline stage is built. It runs after the allocator screen
(`PLAN-ALLOC-SCREEN.md`) selects the lane allocator, written `A` below. If the screen
selects nothing, this plan waits for the owner.

## Method: leave-one-out at the musl head

The glibc parents no longer apply. Each merged candidate is re-measured as its marginal
effect at the current `campaign/musl-lane` head `H`: the parent is `H` with that
candidate's merge reverted (`H − X`), and the candidate is `H`. J-ES-3 is not merged, so
its parent is `H` and its candidate is `H` with `d1b8554` cherry-picked (`H + JES3`).

All stages use allocator `A`, in the pinned Alpine container. This measures each change
in the presence of the others, which can differ from its original stacking order; the
result records that. A revert that does not apply cleanly is resolved only mechanically.
A conflict that needs a design choice stops the rebaseline for that candidate.

| Candidate | Merge | Parent | Candidate | Build flags |
| --- | --- | --- | --- | --- |
| C-REL-3 | PR #20 | `H − CREL3` | `H` | `--jit --serc` |
| J-ES-2 | PR #21 | `H − JES2` | `H` | `--jit --pystats` for P1–P3; `--jit --serc` for I1–I3 |
| J-TLS-1 | PR #22 | `H − JTLS1` | `H` | `--jit --serc` for T1–T3 and T5; `--jit` (no Serc) for T4 |
| C-TLS-2 | PR #23 | `H − CTLS2` | `H` | `--jit --serc` |
| J-ES-3 | not merged | `H` | `H + JES3` | `--jit --pystats` for P1 and P3; `--jit --serc` for P2 |

## Predictions

Each candidate is held to its original preregistered predictions, thresholds and
correctness gates, unchanged:

- C-REL-3: `2026-11-21-crel-3/PLAN-CREL3.md` P1–P3.
- J-ES-2: `2026-11-21-j-es/PLAN-JES2.md` P1–P4, plus I1–I3 from `RESULT-JES2-IR.md`.
- J-TLS-1: `2026-11-22-j-tls/PLAN-JTLS1.md` T1–T5 and its correctness suites. Its glibc
  slow-path functions do not exist on musl, so T5's dynamic-call criterion counts calls
  to musl's `__tls_get_addr`.
- C-TLS-2: `2026-11-22-c-tls/PLAN-CTLS2.md` S1–S5 and its correctness gates. S2's
  function set becomes musl's `__tls_get_addr`.
- J-ES-3: `2026-11-22-j-es3/PLAN-JES3.md` P1–P4, attempt 1/1.

Expected before measuring: J-TLS-1 and C-TLS-2 are null or much smaller on musl, because
musl's `__tls_get_addr` is a short path. J-ES-2 and C-REL-3 do not depend on libc and are
expected to hold.

## Decision

- **A merged candidate holds:** it stays merged, and its glibc result is superseded by
  the musl one.
- **A merged candidate fails a performance prediction:** this is recorded as null on
  musl, and its revert is proposed to the owner. Nothing merged is reverted without the
  owner's decision.
- **A merged candidate fails correctness on musl:** this is a defect to fix before
  anything else proceeds.
- **J-ES-3:** its plan's decision rules apply unchanged.

These are instruction counts, structural counters and memory, all on an unqualified host.
None of them is a timing claim.
