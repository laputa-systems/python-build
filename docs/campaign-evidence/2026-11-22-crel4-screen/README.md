# C-REL-4 screen: remaining `.data.rel.ro` relocations after C-REL-3 (no prototype)

Build: `perf-crel3-gen`, which is C-REL-3 as merged. It has 9,059 `R_X86_64_RELATIVE`
relocations in `.data.rel.ro`, over 51 pages.

| Source | Relocations | Removable without owner decision? |
| --- | ---: | --- |
| CPython C tables (opcode targets, termios/langinfo constants, static exceptions, mimalloc, slot names, …) | ~3,100 | No. These are upstream C data, outside Serc. |
| Rust staticlib (one LTO CGU) | 5,930 | |
| … of which pointers to `.rodata.str1` | 4,620 | ~4,012 are panic `Location` file names. C-REL-2 removed exactly these, and the owner keeps file:line. |
| … the rest (vtables, `stacker::grow` closures, method tables, cst pools) | ~1,300 | Spread over hundreds of small objects. |

**Ceiling for a further Serc relocation candidate:** about 600 string pointers plus a few
hundred others, so at most ~1,000 relocations (~8 KiB, a handful of pages). C-REL-3
removed 15,427 relocations for −244 KiB. This screen therefore projects well under 50 KiB
per process. That is below the ±100 KiB band used to judge C-LOC-1, so no prototype is
opened.

Attribution files: `relro-attribution-crel3-gen.txt` (by libpython symbol; local Rust
symbols are stripped) and `relro-staticlib-attribution.txt` (by relocation section in the
staticlib).
