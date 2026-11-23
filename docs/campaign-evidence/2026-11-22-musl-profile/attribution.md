# musl base2 Cachegrind attribution

Measurement-only analysis; no stage rebuild or experiment verdict. Cachegrind totals were compared with the earlier base2 retry pair mean. The current host is unqualified.

## Top 25 resolved functions

### corpus

| # | Function | Ir | Share |
|---:|---|---:|---:|
| 1 | `PROGRAM TOTALS` | 37,964,424,962 | 100.00% |
| 2 | `???:sha2::sha256::compress256` | 3,227,853,480 | 8.50% |
| 3 | `???:<serc::assembler::ir::OptimizationPipeline>::optimize` | 1,296,026,527 | 3.41% |
| 4 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/string/memcpy.c:memcpy` | 1,254,043,251 | 3.30% |
| 5 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/meta.h:__libc_malloc_impl` | 1,249,132,446 | 3.29% |
| 6 | `???:<serc::assembler::ir::LayoutAssembler>::finish` | 901,966,175 | 2.38% |
| 7 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/meta.h:get_meta` | 861,144,227 | 2.27% |
| 8 | `???:<ruff_python_parser::lexer::Lexer>::next_token` | 757,103,947 | 1.99% |
| 9 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/free.c:__libc_free` | 637,727,289 | 1.68% |
| 10 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/meta.h:__libc_free` | 512,672,643 | 1.35% |
| 11 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/malloc.c:__libc_malloc_impl` | 472,653,998 | 1.24% |
| 12 | `???:<serc::assembler::ir::OptimizationPipeline>::optimize_load_fast_with_retained_entries_inner::<true, true>` | 443,167,536 | 1.17% |
| 13 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/string/memcmp.c:memcmp` | 402,119,029 | 1.06% |
| 14 | `???:<ruff_python_parser::lexer::Lexer>::lex_identifier` | 374,373,527 | 0.99% |
| 15 | `???:<serc::compiler::Compiler>::compile_suite_inner` | 355,685,898 | 0.94% |
| 16 | `???:<serc::assembler::ir::OptimizationPipeline>::remove_redundant_nops_scoped` | 340,406,121 | 0.90% |
| 17 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/malloc.c:alloc_slot` | 329,902,252 | 0.87% |
| 18 | `???:<serc::assembler::ir::OptimizationPipeline>::duplicate_exit_blocks_with_store_origins` | 322,749,824 | 0.85% |
| 19 | `???:<serc::assembler::ir::OptimizationPipeline>::remove_redundant_forward_jumps` | 280,273,668 | 0.74% |
| 20 | `???:<serc::compiler::Compiler>::source_location` | 275,059,169 | 0.72% |
| 21 | `???:<serc::cpython_only::RulesScan as ruff_python_ast::visitor::Visitor>::visit_expr` | 273,850,292 | 0.72% |
| 22 | `???:serc::indentation::cpython_layout_error` | 248,642,233 | 0.65% |
| 23 | `???:serc::assembler::passes::locals::fuse_borrowed_superinstructions` | 245,485,328 | 0.65% |
| 24 | `???:<ruff_python_parser::parser::Parser>::parse_lhs_expression` | 239,605,966 | 0.63% |
| 25 | `???:<serc::assembler::ir::LayoutAssembler>::unextended_exception_table` | 235,284,864 | 0.62% |

### nbody

| # | Function | Ir | Share |
|---:|---|---:|---:|
| 1 | `PROGRAM TOTALS` | 3,010,336,716 | 100.00% |
| 2 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/string/memcpy.c:memcpy` | 648,339,173 | 21.54% |
| 3 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Modules/_hacl/Hacl_Hash_SHA2.c:sha256_update` | 490,431,648 | 16.29% |
| 4 | `???:sha2::sha256::compress256` | 478,209,839 | 15.89% |
| 5 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/generated_cases.c.h:_PyEval_EvalFrameDefault` | 39,719,708 | 1.32% |
| 6 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:_PyFloat_FromDoubleTstate` | 39,293,638 | 1.31% |
| 7 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/math/pow.c:pow` | 30,803,234 | 1.02% |
| 8 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/object.c:_Py_NewReference` | 28,662,777 | 0.95% |
| 9 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyFloat_ExactDeallocTstate` | 26,844,768 | 0.89% |
| 10 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyFloat_FromDoubleTstate` | 19,646,797 | 0.65% |
| 11 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:_PyFloat_PowExactTstate` | 13,601,224 | 0.45% |
| 12 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:_PyFloat_ExactDeallocTstate` | 11,931,008 | 0.40% |
| 13 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/pyhash.c:siphash13` | 9,552,510 | 0.32% |
| 14 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_object.h:_PyEval_EvalFrameDefault` | 8,774,176 | 0.29% |
| 15 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_stackref.h:_PyEval_EvalFrameDefault` | 8,696,467 | 0.29% |
| 16 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/meta.h:__libc_malloc_impl` | 8,476,998 | 0.28% |
| 17 | `???:<serc::assembler::ir::OptimizationPipeline>::optimize` | 8,425,055 | 0.28% |
| 18 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/dictobject.c:unicodekeys_lookup_unicode` | 7,839,065 | 0.26% |
| 19 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/marshal.c:r_object` | 7,772,731 | 0.26% |
| 20 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/ceval.c:_PyEval_EvalFrameDefault` | 7,433,051 | 0.25% |
| 21 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/dictobject.c:_Py_dict_lookup` | 6,461,074 | 0.21% |
| 22 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/obmalloc.c:_PyObject_Free` | 6,062,688 | 0.20% |
| 23 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/meta.h:get_meta` | 5,821,456 | 0.19% |
| 24 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/thread/__tls_get_addr.c:__tls_get_addr` | 5,425,235 | 0.18% |
| 25 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/gc.c:visit_reachable` | 5,094,284 | 0.17% |

### SciMark FFT

| # | Function | Ir | Share |
|---:|---|---:|---:|
| 1 | `PROGRAM TOTALS` | 4,328,543,994 | 100.00% |
| 2 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/string/memcpy.c:memcpy` | 870,027,353 | 20.10% |
| 3 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Modules/_hacl/Hacl_Hash_SHA2.c:sha256_update` | 490,431,648 | 11.33% |
| 4 | `???:sha2::sha256::compress256` | 479,060,617 | 11.07% |
| 5 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/specialize.c:_PyArray_ReadDoubleItem` | 81,045,048 | 1.87% |
| 6 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/generated_cases.c.h:_PyEval_EvalFrameDefault` | 80,120,907 | 1.85% |
| 7 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:_PyFloat_FromDoubleTstate` | 65,095,992 | 1.50% |
| 8 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/object.c:_Py_NewReference` | 56,556,477 | 1.31% |
| 9 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/specialize.c:_PyArray_WriteDoubleItem` | 54,069,550 | 1.25% |
| 10 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:_PyCompactLong_Add` | 46,314,745 | 1.07% |
| 11 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/cpython/longintrepr.h:_PyCompactLong_Add` | 34,220,315 | 0.79% |
| 12 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyFloat_FromDoubleTstate` | 32,536,820 | 0.75% |
| 13 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/object.c:_Py_Dealloc` | 31,839,370 | 0.74% |
| 14 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/thread/__tls_get_addr.c:__tls_get_addr` | 27,667,715 | 0.64% |
| 15 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:PyLong_AsNativeBytes` | 23,402,674 | 0.54% |
| 16 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:float_dealloc` | 22,557,952 | 0.52% |
| 17 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyFloat_ExactDeallocTstate` | 21,602,079 | 0.50% |
| 18 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:_PyCompactLong_Multiply` | 20,511,677 | 0.47% |
| 19 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:float_dealloc` | 18,052,704 | 0.42% |
| 20 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_stackref.h:_PyEval_EvalFrameDefault` | 16,651,992 | 0.38% |
| 21 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/ceval.c:_Py_DeallocTstate` | 16,150,176 | 0.37% |
| 22 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:_PyLong_FromCompactTstate` | 14,780,322 | 0.34% |
| 23 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/cpython/longintrepr.h:_PyCompactLong_Multiply` | 14,676,623 | 0.34% |
| 24 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_object.h:_PyEval_EvalFrameDefault` | 12,906,222 | 0.30% |
| 25 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/abstract.c:binary_op1` | 12,822,328 | 0.30% |

### sparse matrix multiply

| # | Function | Ir | Share |
|---:|---|---:|---:|
| 1 | `PROGRAM TOTALS` | 6,586,710,216 | 100.00% |
| 2 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/string/memcpy.c:memcpy` | 2,054,693,160 | 31.19% |
| 3 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Modules/_hacl/Hacl_Hash_SHA2.c:sha256_update` | 490,431,648 | 7.45% |
| 4 | `???:sha2::sha256::compress256` | 478,625,304 | 7.27% |
| 5 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/specialize.c:_PyArray_ReadDoubleItem` | 240,047,904 | 3.64% |
| 6 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/specialize.c:_PyArray_ReadIntItem` | 124,823,904 | 1.90% |
| 7 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:_PyLong_FromCompactTstate` | 97,418,592 | 1.48% |
| 8 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/object.c:_Py_NewReference` | 95,757,345 | 1.45% |
| 9 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:_PyFloat_FromDoubleTstate` | 70,460,998 | 1.07% |
| 10 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyLong_FromCompactTstate` | 46,290,186 | 0.70% |
| 11 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyFloat_ExactDeallocTstate` | 45,276,264 | 0.69% |
| 12 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyLong_ExactDeallocTstate` | 44,555,544 | 0.68% |
| 13 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_freelist.h:_PyFloat_FromDoubleTstate` | 35,225,087 | 0.53% |
| 14 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:_PyLong_ExactDeallocTstate` | 29,707,133 | 0.45% |
| 15 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/generated_cases.c.h:_PyEval_EvalFrameDefault` | 25,638,736 | 0.39% |
| 16 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_long.h:_PyLong_FromCompactTstate` | 20,573,416 | 0.31% |
| 17 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/floatobject.c:_PyFloat_ExactDeallocTstate` | 20,122,784 | 0.31% |
| 18 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/longobject.c:PyLong_AsLongAndOverflow` | 11,274,021 | 0.17% |
| 19 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/object.h:_PyArray_ReadDoubleItem` | 10,001,996 | 0.15% |
| 20 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Include/internal/pycore_long.h:_PyLong_ExactDeallocTstate` | 9,902,214 | 0.15% |
| 21 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Python/pyhash.c:siphash13` | 9,649,949 | 0.15% |
| 22 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/thread/__tls_get_addr.c:__tls_get_addr` | 9,442,730 | 0.14% |
| 23 | `/home/buildozer/aports/main/musl/src/musl-1.2.6/src/malloc/mallocng/meta.h:__libc_malloc_impl` | 8,461,335 | 0.13% |
| 24 | `/home/ubuntu/repos/python-build/wt-allocretry/rust-cpython/work/perf/perf-musl-base2/source/cpython-e891a9117985888df3e5d48145cf07ddda098335/Objects/rangeobject.c:make_range_object` | 8,451,238 | 0.13% |
| 25 | `???:<serc::assembler::ir::OptimizationPipeline>::optimize` | 8,425,055 | 0.13% |

## Earlier-bucket comparison

The current Cachegrind annotation resolves loader/musl functions using matching `musl-dbg`; the prior unknown counts are from the retry receipt. Anonymous-executable Callgrind address mapping was confirmed for nbody. FFT and sparse Callgrind files are preserved for further address-range analysis; no mapped JIT subtotal is asserted for those two here.

| Workload | Earlier `???:???` Ir | Current unresolved `Ir` | Current total Ir |
|---|---:|---:|---:|
| corpus | 5,321,625,822 | 89,921,635 | 37,964,424,962 |
| nbody | 766,426,013 | 721,438,367 | 3,010,336,716 |
| SciMark FFT | 1,152,621,074 | 1,107,155,582 | 4,328,543,994 |
| sparse matrix multiply | 2,093,053,101 | 2,048,123,033 | 6,586,710,216 |

The new totals differ from the prior base2 pair means by +0.001630% (corpus), +0.004044% (nbody), −0.001707% (FFT), and −0.003657% (sparse), all within 0.1%.

Callgrind nbody recorded 3,008,235,429 Ir; 706,411,500 instruction references mapped to anonymous executable ranges in the captured `/proc/<pid>/maps` (JIT-enabled `jit_kernels.run`). Cachegrind’s remaining `???:???` for nbody was 721,438,367 Ir. The direct mapping substantiates the JIT attribution for most of that bucket; the non-mapped remainder remains unresolved.
