# Compile-corpus Callgrind caller attribution

This is attribution evidence only. The single Callgrind corpus run completed on
`perf-musl-base2` using the retry command and the measurement-only Alpine
`musl-dbg=1.2.6-r3` package. The corpus reported `compile 2104 2093` with digest
`93465d79b16faddee2c06d732e755d663015e55c55d7b97b90baee55c136b0cb`.

## Total comparison

Callgrind collected **37,793,824,901 Ir**. The prior base2 Cachegrind corpus
total was **37,964,424,962 Ir**; Callgrind is **−0.449368%**, outside the
requested 0.1% tolerance. Caller counts below are the raw Callgrind
attribution, but the total mismatch means this profile does not satisfy the
requested comparability gate. No second profile run was made.

## mallocng family

The inclusive caller tables use the callgraph edges at the public allocator
entry points (`malloc`, `free`, `calloc`, `realloc`), so nested mallocng
internals are included without summing their overlapping internal call edges.
The two-level-up table uses the recorded caller context and retains Serc/Ruff
symbols.

Public entry-point calls: `malloc` **13,969,906**; `free` **13,970,164**;
`calloc` **834,565**; `realloc` **1,583,384**.

Top immediate callers by inclusive allocator Ir:

| Caller | Inclusive Ir |
|---|---:|
| `<alloc::raw_vec::RawVecInner>::finish_grow` | 1,286,778,043 |
| `<serc::assembler::ir::OptimizationPipeline>::optimize` | 417,716,792 |
| `<hashbrown::raw::RawTableInner>::fallible_with_capacity::<alloc::alloc::Global>` | 328,290,384 |
| `<serc::assembler::ir::LayoutAssembler>::finish` | 319,721,502 |
| `<serc::assembler::ir::LayoutAssembler>::unextended_exception_table` | 239,121,692 |
| `core::ptr::drop_glue::<ruff_python_ast::generated::Expr>` | 218,713,644 |
| `core::ptr::drop_glue::<alloc::boxed::Box<serc::code_object::CodeObject>>` | 217,500,158 |
| `serc::indentation::cpython_layout_error` | 204,613,255 |
| `<serc::assembler::ir::OptimizationPipeline>::duplicate_exit_blocks_with_store_origins` | 186,414,965 |
| `<ruff_python_parser::parser::Parser>::parse_lhs_expression` | 182,789,327 |
| `<serc::assembler::ir::OptimizationPipeline>::optimize_load_fast_with_retained_entries_inner::<true, true>` | 171,817,697 |
| `<serc::assembler::ir::Assembler>::into_optimization_pipeline` | 156,688,091 |
| `thin_vec::drop_non_singleton::<ruff_python_ast::generated::Stmt>` | 151,163,905 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 129,871,101 |
| `<serc::assembler::ir::LayoutAssembler>::exception_table` | 128,616,087 |
| `<serc::assembler::ir::OptimizationPipeline>::push_cold_blocks_to_end` | 126,958,816 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 112,547,338 |
| `serc::assembler::layout::inherit_exception_owners` | 111,150,709 |
| `<serc::assembler::ir::OptimizationPipeline>::thread_forward_jumps_with_source_gates::<true, true>` | 109,314,222 |
| `<serc::compiler::Compiler>::remove_unused_constants` | 101,864,620 |

Top immediate callers by call count:

| Caller | Calls | Inclusive Ir |
|---|---:|---:|
| `<alloc::raw_vec::RawVecInner>::finish_grow` | 3,508,163 | 1,286,778,043 |
| `<hashbrown::raw::RawTableInner>::fallible_with_capacity::<alloc::alloc::Global>` | 1,656,492 | 328,290,384 |
| `core::ptr::drop_glue::<ruff_python_ast::generated::Expr>` | 1,388,193 | 218,713,644 |
| `core::ptr::drop_glue::<alloc::boxed::Box<serc::code_object::CodeObject>>` | 1,339,155 | 217,500,158 |
| `<ruff_python_parser::parser::Parser>::parse_lhs_expression` | 948,969 | 182,789,327 |
| `thin_vec::drop_non_singleton::<ruff_python_ast::generated::Stmt>` | 936,441 | 151,163,905 |
| `<serc::assembler::ir::OptimizationPipeline>::optimize` | 675,772 | 417,716,792 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 651,663 | 112,547,338 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 622,647 | 129,871,101 |
| `<serc::assembler::ir::OptimizationPipeline>::duplicate_exit_blocks_with_store_origins` | 587,944 | 186,414,965 |
| `core::ptr::drop_glue::<serc::code_object::Constant>` | 563,092 | 91,247,345 |
| `<serc::compiler::syntax::declarations::Validator>::record` | 510,317 | 80,284,271 |
| `<serc::assembler::ir::LayoutAssembler>::finish` | 463,473 | 319,721,502 |
| `<serc::compiler::scope::FunctionPlan>::build` | 435,128 | 84,346,223 |
| `<serc::compiler::source::CompilationContext>::record_code_assembly` | 407,742 | 72,495,999 |
| `__rustc::__rust_alloc` | 392,545 | 61,355,557 |
| `<serc::assembler::ir::OptimizationPipeline>::push_cold_blocks_to_end` | 373,579 | 126,958,816 |
| `thin_vec::header_with_capacity::<ruff_python_ast::generated::Expr>` | 345,434 | 60,134,295 |
| `thin_vec::drop_non_singleton::<ruff_python_ast::generated::Expr>` | 345,434 | 50,443,147 |
| `<ruff_python_parser::parser::Parser>::parse_simple_statement` | 345,318 | 58,788,116 |

Top Serc/Ruff functions two levels up by inclusive allocator Ir:

| Function | Inclusive Ir | Calls |
|---|---:|---:|
| `<serc::assembler::ir::OptimizationPipeline>::optimize` | 1,025,797,455 | 2,976,334 |
| `<serc::assembler::ir::Assembler>::finish_code_with_constants` | 1,018,285,713 | 1,428,594 |
| `<serc::assembler::ir::LayoutAssembler>::finish` | 494,522,592 | 671,196 |
| `<serc::compiler::Compiler>::finish_inner` | 360,571,270 | 1,598,242 |
| `serc::compile_normalized` | 237,946,974 | 45,564 |
| `<ruff_python_parser::parser::Parser>::parse_conditional_expression_or_higher_impl` | 225,306,239 | 1,028,568 |
| `<serc::compiler::Compiler>::remove_unused_constants` | 196,568,945 | 716,355 |
| `ClearExpressions::visit_expr` | 183,012,943 | 1,224,044 |
| `core::ptr::drop_glue::<serc::code_object::Constant>` | 170,754,663 | 1,073,565 |
| `core::ptr::drop_glue::<serc::ParsedModule>` | 151,163,905 | 936,441 |
| `<alloc::raw_vec::RawVec<...exception_owners::ConstantPassCluster>>::grow_one` | 130,098,834 | 163,121 |
| `<serc::compiler::scope::ScopeArena>::build_scopes` | 123,423,816 | 622,692 |
| `<alloc::raw_vec::RawVec<...Option<serc::assembler::ir::Label>>>::grow_one` | 120,773,288 | 399,716 |
| `<hashbrown::raw::RawTable<(serc::compiler::scope::NameId, ())>>::reserve_rehash` | 113,924,717 | 658,716 |
| `<serc::compiler::Compiler>::compile_plain_function_definition` | 99,826,979 | 614,239 |

## `strstr`

| Caller | Inclusive Ir | Calls |
|---|---:|---:|
| `_Py_CompileString` | 248,880,586 | 2,104 |
| `_PyRun_String` | 246 | 1 |

The `_Py_CompileString` call site is `serc_routes` in
`rust-cpython/overlay/Python/pythonrun.c:1745`; it searches for `"\\N{"`.

## `memcpy`

Top callers by inclusive Ir (with call counts):

| Caller | Inclusive Ir | Calls |
|---|---:|---:|
| `__libc_realloc` | 293,262,324 | 1,521,442 |
| `<serc::assembler::ir::OptimizationPipeline>::optimize` | 154,687,227 | 235,919 |
| `<serc::assembler::block_graph::BlockGraph>::copy_items` | 142,931,559 | 951,185 |
| `<serc::assembler::ir::OptimizationPipeline>::remove_redundant_forward_jumps` | 102,688,267 | 10,814 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 89,951,564 | 498,867 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 77,497,190 | 503,798 |
| `_PyBytes_FromStringAndSize` | 64,879,979 | 544,535 |
| `<serc::compiler::Compiler>::finish_inner` | 56,726,958 | 97,469 |
| `<serc::assembler::ir::Assembler>::finish_code_with_constants` | 56,416,784 | 97,469 |
| `<serc::compiler::Compiler>::new` | 45,521,292 | 194,952 |
| `<serc::marshal::writer::Writer>::bytes_with_object` | 40,249,814 | 296,636 |
| `<serc::assembler::fusion_owner_slots::FusionOwnerCertificates>::retain_pipeline` | 29,830,207 | 227,648 |
| `PyUnicode_FromKindAndData` | 29,777,646 | 465,843 |
| `w_pstring` | 28,166,383 | 191,693 |
| `<serc::compiler::Compiler>::compile_plain_function_definition` | 26,385,198 | 153,841 |

The visible harness marshal path is represented by CPython marshal writer
callers such as `w_pstring` and `_PyBytes_FromStringAndSize`; `compile_corpus.py`
hashes `marshal.dumps(co)` via `h.update(...)` at line 17. The top `memcpy`
callers otherwise point to Serc/Ruff compilation and allocator internals.
No substantial direct `memcpy` caller in the harness SHA-256 update path was
visible in the top caller table.

## `serc::indentation::cpython_layout_error`

The Callgrind graph records **2,063 calls** from `serc::compile_normalized`
with **250,036,480 inclusive Ir**. The corpus completed 2,093 files
successfully. The source invokes this check after parsing and before later
validation/code generation (`serc/src/lib.rs:1272`); `false` continues the
Serc path, while `true` requests CPython fallback. Thus the check is on the
successful compile path, although this profile cannot correlate its individual
calls with per-file outcomes.
