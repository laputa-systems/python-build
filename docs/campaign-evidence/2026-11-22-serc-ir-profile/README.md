# Serc compile instruction profile (screen, no prototype)

Workload: `compile_corpus.py compile <stage>/lib/python3.16`. It compiles 2,104 stdlib
files with `compile()`; 2,093 succeed. Run under cachegrind `--cache-sim=no` on `-nodebug`
stage copies (recipe from `PLAN-JES2-IR.md`, Addendum B). Ubuntu 22.04, glibc 2.35.
Instruction counts only.

| Stage | Compiler | Total `Ir` |
| --- | --- | ---: |
| `perf-ir-jes2` (`serc: false`) | C | 54,457,751,957 |
| `perf-crel3-gen` (`serc: true`) | Serc | 37,811,564,328 |
| read-only control (`perf-ir-jes2`) | none | 529,024,302 |

The two stages are different builds, so the C vs Serc ratio is descriptive only.

Serc profile buckets (share of total `Ir`):
- `serc` 42.7%; `ruff_python_parser` 6.9%; `hashbrown` 3.0%.
- The harness's own `sha256` digest is 8.5%. It is identical work in every stage, and
  predictions must exclude it.
- glibc `malloc`/`free` family 11.7% (`_int_malloc` 4.1%, `_int_free` 3.2%, `malloc` 1.5%,
  `free` 0.9%, `malloc_consolidate` 0.8%, …). Serc's Rust allocations use the `System`
  allocator; the staticlib installs no `#[global_allocator]`.
- TLS lookups 5.3% (`_dl_update_slotinfo`, `update_get_addr`, `__tls_get_addr`).
  J-TLS-1 targets part of this.
- Top single functions: `OptimizationPipeline::optimize` 3.4%,
  `LayoutAssembler::finish` 2.4%, `Lexer::next_token` 2.0%,
  `indentation::cpython_layout_error` 0.66% (an error-path name on the success path).

Lead (not yet a hypothesis): Serc's allocation cost. Installing a staticlib-wide allocator
affects every Rust crate in libpython. That includes code that runs without an attached
thread state, and lazy tables that outlive interpreters, so `AttachedThreadAllocator` does
not apply. A design is required before any prediction is written.

## Screen: `indentation::cpython_layout_error` (no prototype)

This is not an error path. It is Serc's routing check, and it runs on every compile
(`lib.rs` calls it after parsing). It decides whether a module's tab/space layout must be
handed to CPython. The check makes two scans over the whole file:
- `contains(b'\t')`;
- the backslash-continuation `windows(2)` scan, which runs only when the file has tabs.

It also walks every token once, and scans backwards to the line start at each logical
line. Its whole cost is 0.66% of the corpus, which is below the useful threshold even if
it were removed entirely. Removing it would also require proving that tab-free files can
skip the check, which affects routing exactness. Screened out.
