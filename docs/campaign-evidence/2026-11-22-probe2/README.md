# Corrected Serc residency probe (OD22 VM, 2026-11-22)

`residency_probe2.py` replaces `../2026-11-21-cloc-1/residency_probe.py` for
new structural claims. The old probe stays in place unchanged, and so do
C-LOC-1's original numbers and its retirement.

**Two defects in the old probe** (owner steering, 2026-11-22):

1. Its "`.pyc` control" ran the reader with `python -c`. In this lane, eligible
   `-c` strings compile through Serc, so both modes compiled Serc code.
2. It copied the payload before every observation. The fresh mtime could
   invalidate the warmed `.pyc`. One old candidate `.pyc` sample matched source
   mode (7,868 KiB). That is consistent with invalidation, but the old data
   cannot prove it.

**What the corrected probe does:**

- The reader is a precompiled unchecked-hash `.pyc`, run as a file (`-I -B`).
- The payload is copied once per mode directory, with a fixed mtime, and is
  never recopied. Its bytes and SHA-256 are retained here: `payload-argparse.py`,
  `d5a6e1be…37ac`, identical to the C-LOC-1 payload.
- No `pycache_prefix` is used, so the stdlib loads from each stage's own
  `__pycache__` in every mode.
- There are three modes, alternated within each observation round.

**The `pyc` control** imports the payload as a sourceless unchecked-hash `.pyc`.
It excludes every Serc and CPython compile of the payload and of the reader. It
includes interpreter startup, stdlib `.pyc` imports, unmarshalling and payload
execution.

**Per-observation validity** (`validity` in each JSON): an observation is valid
only if all of these hold:

- the mode directory listing is unchanged;
- the reader `.pyc` hash is unchanged;
- the payload or payload-`.pyc` hash is unchanged;
- the loader is `SourcelessFileLoader` in `pyc` mode, or `SourceFileLoader` in
  source modes.

All 21 observations per build are valid (7 rounds × 3 modes).

**Compiler-call and table-initialisation evidence** (gdb twin runs, not
measurement samples):

| Mode | `_PySerc_CompileSource` calls | `_PyAST_Compile` calls | Lazy target-table inits: parent / C-LOC-1 / C-REL-3 |
| --- | --- | --- | --- |
| `pyc` | 0 | 0 | 0 / 0 / 0 |
| `source` | 1 | 0 | 1 / 1 / 2 |
| `source-x20` (import + 20 `compile()`) | 21 | 0 | 1 / 1 / 2 |

The parent and C-LOC-1 initialise the pre-existing `is_preinterned_string::STARTUP`
set. C-REL-3 initialises that set plus `STARTUP_INTERNED_STRINGS`, which has
41 elements (a 656 B `Box<[&str]>`). Initialisation happens once per process and
does not repeat across 20 further compiles. `PREINTERNED_STRINGS` (899
elements) and the codec tables were not initialised in any mode.

## Results

Medians of 7 observations, KiB. Builds: parent `perf-cloc-parent` (`da74796`),
C-LOC-1 `perf-cloc-cand` (`6e5568c`), C-REL-3 `perf-crel3-cand` (built from
`ef48b04`; see the identity note below). The libpython SHA-256 of each build is
in its JSON. The observation range is 0 KiB for every libpython and heap row,
and ≤ 68 KiB for whole-process rows.

| Metric | Mode | Parent | C-LOC-1 | C-REL-3 |
| --- | --- | --- | --- | --- |
| libpython `r-xp` Pss | pyc | 2,616 | 2,684 (+68) | 2,580 (−36) |
| libpython `r-xp` Pss | source | 9,144 | 7,868 (−1,276) | 9,108 (−36) |
| libpython `r--p` Pss | pyc | 3,188 | 3,188 | 2,624 (−564) |
| libpython `r--p` Private_Dirty | all | 564 | 564 | 320 (−244) |
| libpython `rw-p` Private_Dirty | all | 488 | 488 | 496 (+8) |
| `[heap]` Pss | pyc / source / x20 | 1,140 / 3,032 / 3,480 | 1,140 / 3,032 / 3,460 | 1,136 / 3,028 / 3,460 |
| anonymous Pss | pyc / source / x20 | 1,852 / 2,028 / 2,168 | 1,852 / 2,024 / 2,168 | 1,848 / 2,024 / 2,164 |
| process Pss (`smaps_rollup`) | pyc | 9,527 | 9,599 (+72) | 8,951 (−576) |
| process Pss | source | 18,553 | 17,215 (−1,338) | 18,105 (−448) |
| process Pss | source-x20 | 19,210 | 17,845 (−1,365) | 18,720 (−490) |
| process Private_Dirty (= Pss_Anon) | pyc / source / x20 | 4,152 / 6,268 / 6,848 | 4,152 / 6,260 / 6,828 | 3,908 / 6,024 / 6,592 |
| peak RSS (VmHWM) | pyc / source / x20 | 11,772 / 20,796 / 21,568 | 11,844 / 19,480 / 20,108 | 11,204 / 20,348 / 21,088 |

### C-LOC-1 under the corrected control (observation, not a re-decision)

The original C-LOC-1 P2 control failure (`.pyc` −764 KiB, limit 100 KiB) does
not reproduce:

- The corrected control moves +68 KiB, within the ±100 KiB limit.
- Source-mode executable Pss falls 1,276 KiB, against a ≥ 512 KiB requirement.

The parent's old control (7,736 KiB) was mostly Serc code paged in by the `-c`
reader itself. That old figure was 2,616 KiB under the corrected probe. The
decision remains as recorded: C-LOC-1 was retired at attempt 1/1, and its
budget and name are unchanged. Re-admitting it, as either a correction of a
measurement defect or a new attempt, is an owner decision. Executable-code
reduction is structural evidence only; whether `opt-level="s"` costs compile
CPU time needs a qualified timing host.

### C-REL-3 whole-process accounting

The lazy tables do not consume the saving:

- `[heap]` and anonymous Pss are equal within 4 KiB in all modes, and 20 KiB
  lower with 20 repeated compiles.
- The one extra initialised table is 656 B.
- Writable libpython Private_Dirty is +8 KiB.
- Net process-private memory falls 244 KiB in every mode. Process Pss falls
  448–576 KiB.
- Peak RSS falls 448–568 KiB.

These are structural results, not timing.

**Identity: closed.** The table above measures `ef48b04`, the transformed tables. The
candidate `8a57de4`, the generator-emitted tables, differs in 3.16 table order and offsets.

That revision was rebuilt as `perf-crel3-gen` in 620 s, with libpython SHA-256
`449ed753…fecf1`, and both checks were repeated on it:

- **Relocations:** `.data.rel.ro` `R_X86_64_RELATIVE` 24,486 → 9,059 and pages 111 → 51,
  identical to `ef48b04`.
- **Probe** (`res2-perf-crel3-gen.json`): all 21 observations valid, and the same gdb
  counts and lazy inits as `ef48b04`. Every median is within 8 KiB of the `ef48b04`
  value, and libpython rows are identical. Process-private memory is −244 KiB
  (pyc/source/x20: 3,904/6,024/6,592), and peak RSS is −576/−436/−536 KiB.
- **Compile parity:** the parent and `perf-crel3-gen` compiled the same 2,104-file stage
  `lib/python3.16` with zero differing marshal digests (`parity-stagelib-parent.json.xz`,
  `parity-perf-crel3-gen.json.xz`).

## Files

- `residency_probe2.py`, `reader.py` and `payload-argparse.py`: exact inputs.
- `res2-perf-*.json`: all samples, validity checks, gdb counts, and the
  identities of the payload, reader `.pyc` and libpython.
- `gdb-lazy-*.log.xz`: raw logs of the table-initialisation twin runs.
- `copies.sha256`: checksums for the other files.

Reproduce:

```sh
python3 residency_probe2.py <stage>/bin/python3.16 payload-argparse.py 7 <outdir> --gdb
```
