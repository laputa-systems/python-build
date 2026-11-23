# JIT net-kernel `memcpy` callers

Attribution only. No verdict or product-code changes were made.

The profiles were collected with Valgrind Callgrind 3.25.1 in the existing measurement container. The matching `musl-dbg=1.2.6-r3` package was installed there; the loader and debug file both had BuildID `d1656128169ecc566bb0afcbbcbda02c5ac8cd74`. Alpine's Valgrind APK does not include `callgrind_annotate`, so the profiles were annotated with the host's Callgrind `callgrind_annotate` 3.18.1 Perl script. It read the 3.25.1 profiles successfully. The full caller trees use `--auto=no --show=Ir --threshold=100 --inclusive=yes --tree=caller`; threshold 100 emits all functions.

The caller-edge `Ir` is the `memcpy` cost attributed to that immediate caller. Each table contains the union of the top 15 by `Ir` and the top 15 by call count; rank columns identify each ordering.

## Totals

| Run | Total `Ir` | `memcpy` calls | `memcpy` inclusive `Ir` |
|---|---:|---:|---:|
| nbody kernel | 2,982,076,796 | 18,699,216 | 647,474,342 |
| nbody setup-only | 1,416,222,742 | 149,968 | 16,667,168 |
| sparse kernel | 6,559,265,575 | 60,055,556 | 2,053,826,694 |
| sparse setup-only | 1,429,332,827 | 151,352 | 16,802,442 |

Simple kernel-minus-setup-only `memcpy Ir` differences are 630,807,174 for nbody and 2,037,024,252 for sparse; the corresponding call-count differences are 18,549,248 and 59,904,204. These are arithmetic differences between separate process runs.

## nbody kernel

| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |
|---|---:|---:|---:|---:|
| `JIT code @ 0x0000000006ace000` | 525,579,140 | 15,458,210 | 1 | 1 |
| `JIT code @ 0x0000000006595000` | 90,378,596 | 2,658,194 | 2 | 2 |
| `JIT code @ 0x0000000006405000` | 14,691,060 | 432,090 | 3 | 3 |
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 4,951,440 | 11,960 | 4 | 6 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 1,999,414 | 17,057 | 5 | 5 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 1,652,078 | 26,659 | 6 | 4 |
| `musl/src/malloc/mallocng/realloc.c:__libc_realloc` | 1,451,456 | 9,601 | 7 | 8 |
| `CPython/Objects/codeobject.c:_PyCode_New` | 546,188 | 4,688 | 8 | 12 |
| `<serc::assembler::block_graph::BlockGraph>::copy_items` | 539,988 | 4,764 | 9 | 11 |
| `CPython/Objects/unicodeobject.c:unicode_decode_utf8` | 529,875 | 9,992 | 10 | 7 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 406,058 | 3,527 | 11 | 14 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 405,226 | 3,553 | 12 | 13 |
| `CPython/Objects/unicodeobject.c:_PyUnicode_JoinArray` | 377,857 | 8,038 | 13 | 9 |
| `CPython/Objects/dictobject.c:dictresize` | 374,860 | 2,118 | 14 | 17 |
| `CPython/Objects/unicodeobject.c:_copy_characters` | 308,095 | 7,811 | 15 | 10 |
| `CPython/Objects/longobject.c:PyLong_AsNativeBytes` | 83,980 | 2,470 | 26 | 15 |

## nbody setup-only

| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |
|---|---:|---:|---:|---:|
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 4,951,440 | 11,960 | 1 | 3 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 1,999,400 | 17,056 | 2 | 2 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 1,652,111 | 26,660 | 3 | 1 |
| `musl/src/malloc/mallocng/realloc.c:__libc_realloc` | 1,390,378 | 9,537 | 4 | 5 |
| `CPython/Objects/codeobject.c:_PyCode_New` | 546,186 | 4,688 | 5 | 9 |
| `<serc::assembler::block_graph::BlockGraph>::copy_items` | 539,988 | 4,764 | 6 | 8 |
| `CPython/Objects/unicodeobject.c:unicode_decode_utf8` | 529,793 | 9,990 | 7 | 4 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 406,058 | 3,527 | 8 | 11 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 405,226 | 3,553 | 9 | 10 |
| `CPython/Objects/unicodeobject.c:_PyUnicode_JoinArray` | 377,857 | 8,038 | 10 | 6 |
| `CPython/Objects/dictobject.c:dictresize` | 374,860 | 2,118 | 11 | 14 |
| `CPython/Objects/unicodeobject.c:_copy_characters` | 307,359 | 7,795 | 12 | 7 |
| `<serc::compiler::Compiler>::finish_inner` | 273,224 | 476 | 13 | 39 |
| `<serc::assembler::ir::Assembler>::finish_code_with_constants` | 273,224 | 476 | 14 | 40 |
| `CPython/Objects/dictobject.c:clone_combined_dict_keys` | 266,458 | 1,172 | 15 | 22 |
| `<serc::marshal::writer::Writer>::string_payload_with_ascii::<true>` | 93,425 | 2,371 | 21 | 13 |
| `CPython/Objects/longobject.c:PyLong_AsNativeBytes` | 83,980 | 2,470 | 23 | 12 |
| `<serc::compiler::source::CompilationContext>::record_code_assembly` | 62,628 | 1,681 | 25 | 15 |

## sparse kernel

| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |
|---|---:|---:|---:|---:|
| `JIT code @ 0x0000000006070000` | 2,016,369,456 | 59,304,984 | 1 | 1 |
| `JIT code @ 0x00000000063ff000` | 20,359,200 | 598,800 | 2 | 2 |
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 4,951,440 | 11,960 | 3 | 5 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 2,021,977 | 17,481 | 4 | 4 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 1,652,078 | 26,659 | 5 | 3 |
| `musl/src/malloc/mallocng/realloc.c:__libc_realloc` | 1,474,758 | 9,654 | 6 | 7 |
| `CPython/Objects/codeobject.c:_PyCode_New` | 549,798 | 4,719 | 7 | 11 |
| `<serc::assembler::block_graph::BlockGraph>::copy_items` | 539,988 | 4,764 | 8 | 10 |
| `CPython/Objects/unicodeobject.c:unicode_decode_utf8` | 538,412 | 10,152 | 9 | 6 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 406,058 | 3,527 | 10 | 13 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 405,226 | 3,553 | 11 | 12 |
| `CPython/Objects/dictobject.c:dictresize` | 382,688 | 2,195 | 12 | 16 |
| `CPython/Objects/unicodeobject.c:_PyUnicode_JoinArray` | 377,863 | 8,038 | 13 | 8 |
| `CPython/Objects/unicodeobject.c:_copy_characters` | 309,756 | 7,857 | 14 | 9 |
| `<serc::assembler::ir::Assembler>::finish_code_with_constants` | 273,224 | 476 | 15 | 41 |
| `<serc::marshal::writer::Writer>::string_payload_with_ascii::<true>` | 93,425 | 2,371 | 24 | 15 |
| `CPython/Objects/longobject.c:PyLong_AsNativeBytes` | 84,082 | 2,473 | 26 | 14 |

## sparse setup-only

| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |
|---|---:|---:|---:|---:|
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 4,951,440 | 11,960 | 1 | 3 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 2,018,973 | 17,480 | 2 | 2 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 1,652,111 | 26,660 | 3 | 1 |
| `musl/src/malloc/mallocng/realloc.c:__libc_realloc` | 1,447,436 | 9,600 | 4 | 5 |
| `CPython/Objects/codeobject.c:_PyCode_New` | 549,796 | 4,719 | 5 | 9 |
| `<serc::assembler::block_graph::BlockGraph>::copy_items` | 539,988 | 4,764 | 6 | 8 |
| `CPython/Objects/unicodeobject.c:unicode_decode_utf8` | 536,677 | 10,121 | 7 | 4 |
| `<serc::assembler::block_graph::BlockGraph>::into_items` | 406,058 | 3,527 | 8 | 11 |
| `<serc::assembler::block_graph::BlockGraph>::from_items` | 405,226 | 3,553 | 9 | 10 |
| `CPython/Objects/dictobject.c:dictresize` | 382,688 | 2,195 | 10 | 14 |
| `CPython/Objects/unicodeobject.c:_PyUnicode_JoinArray` | 377,863 | 8,038 | 11 | 6 |
| `CPython/Objects/unicodeobject.c:_copy_characters` | 309,037 | 7,841 | 12 | 7 |
| `<serc::compiler::Compiler>::finish_inner` | 273,224 | 476 | 13 | 39 |
| `<serc::assembler::ir::Assembler>::finish_code_with_constants` | 273,224 | 476 | 14 | 40 |
| `CPython/Objects/dictobject.c:clone_combined_dict_keys` | 267,394 | 1,178 | 15 | 22 |
| `<serc::marshal::writer::Writer>::string_payload_with_ascii::<true>` | 93,425 | 2,371 | 21 | 13 |
| `CPython/Objects/longobject.c:PyLong_AsNativeBytes` | 84,082 | 2,473 | 23 | 12 |
| `<serc::compiler::source::CompilationContext>::record_code_assembly` | 62,628 | 1,681 | 26 | 15 |

## Top callers' callers

### nbody kernel

The direct callers are the top three by inclusive `Ir`; the next column gives their callers one level farther up the tree.

| Direct `memcpy` caller | Calls to `memcpy` | `memcpy` Ir | Caller above it | Calls to direct caller | Ir on edge |
|---|---:|---:|---|---:|---:|
| `JIT code @ 0x0000000006ace000` | 15,458,210 | 525,579,140 | `_PyJIT_Entry` | 800 | 1,257,890,265 |
| `JIT code @ 0x0000000006595000` | 2,658,194 | 90,378,596 | `_PyJIT_Entry` | 3,602 | 217,283,947 |
| `JIT code @ 0x0000000006405000` | 432,090 | 14,691,060 | `_PyJIT_Entry` | 3,601 | 32,961,247 |

### nbody setup-only

The direct callers are the top three by inclusive `Ir`; the next column gives their callers one level farther up the tree.

| Direct `memcpy` caller | Calls to `memcpy` | `memcpy` Ir | Caller above it | Calls to direct caller | Ir on edge |
|---|---:|---:|---|---:|---:|
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 11,960 | 4,951,440 | `zlib_rs::crc32::crc32` | 269 | 5,092,087 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 17,056 | 1,999,400 | `CPython/Objects/bytesobject.c:PyBytes_FromStringAndSize` | 17,585 | 3,875,022 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 17,056 | 1,999,400 | `CPython/Objects/bytesobject.c:bytes_subscript` | 853 | 118,075 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `CPython/Python/marshal.c:r_object'2` | 27,552 | 7,741,230 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `_posixpath_rs::normpath_impl::<u32>::{closure#0}` | 16 | 10,287 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `_posixpath_rs::join_impl::<u32>::{closure#0}` | 18 | 9,537 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `_posixpath_rs::split` | 4 | 1,957 |

### sparse kernel

The direct callers are the top three by inclusive `Ir`; the next column gives their callers one level farther up the tree.

| Direct `memcpy` caller | Calls to `memcpy` | `memcpy` Ir | Caller above it | Calls to direct caller | Ir on edge |
|---|---:|---:|---|---:|---:|
| `JIT code @ 0x0000000006070000` | 59,304,984 | 2,016,369,456 | `_PyJIT_Entry` | 4,017 | 4,977,405,869 |
| `JIT code @ 0x00000000063ff000` | 598,800 | 20,359,200 | `_PyJIT_Entry` | 80 | 50,311,418 |
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 11,960 | 4,951,440 | `zlib_rs::crc32::crc32` | 269 | 5,092,087 |

### sparse setup-only

The direct callers are the top three by inclusive `Ir`; the next column gives their callers one level farther up the tree.

| Direct `memcpy` caller | Calls to `memcpy` | `memcpy` Ir | Caller above it | Calls to direct caller | Ir on edge |
|---|---:|---:|---|---:|---:|
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 11,960 | 4,951,440 | `zlib_rs::crc32::crc32` | 269 | 5,092,087 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 17,480 | 2,018,973 | `CPython/Objects/bytesobject.c:PyBytes_FromStringAndSize` | 18,364 | 3,950,801 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 17,480 | 2,018,973 | `CPython/Objects/bytesobject.c:bytes_subscript` | 853 | 118,054 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `CPython/Python/marshal.c:r_object'2` | 27,552 | 7,739,319 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `_posixpath_rs::normpath_impl::<u32>::{closure#0}` | 16 | 10,287 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `_posixpath_rs::join_impl::<u32>::{closure#0}` | 18 | 9,540 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 26,660 | 1,652,111 | `_posixpath_rs::split` | 4 | 1,957 |

## JIT and executor callers

| Run | Direct JIT/executor caller edges | Edge calls | Edge `Ir` | `_PyJIT_Compile` calls | `_PyOptimizer_Optimize` calls | `make_cold_executor` calls |
|---|---:|---:|---:|---:|---:|---:|
| nbody kernel | 18 | 535 | 91,207 | 7 | 5 | 2 |
| nbody setup-only | 0 | 0 | 0 | 0 | 0 | 0 |
| sparse kernel | 21 | 113 | 24,990 | 7 | 5 | 2 |
| sparse setup-only | 0 | 0 | 0 | 0 | 0 | 0 |

### nbody kernel: selected direct JIT/executor callers

| Caller | Calls | `memcpy` Ir |
|---|---:|---:|
| `CPython/Python/jit_unwind.c:_PyJitUnwind_GdbRegisterCode` | 14 | 23,070 |
| `_jit_opt_rs::unbox::plan_unboxing` | 329 | 14,666 |
| `CPython/Python/optimizer.c:_PyOptimizer_Optimize` | 4 | 13,380 |
| `_PyJitOpt_PlanLeafCalls` | 5 | 9,024 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__STORE_SUBSCR_LIST_INT_FLOAT_r32` | 18 | 4,260 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__STORE_FAST_FUSED_FLOAT_OR_NULL_r00` | 23 | 3,929 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__BINARY_OP_MULTIPLY_FLOAT_UNBOXED_r32` | 24 | 3,649 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__UNBOX_FLOAT_TOS_r33` | 24 | 3,567 |

### sparse kernel: selected direct JIT/executor callers

| Caller | Calls | `memcpy` Ir |
|---|---:|---:|
| `CPython/Python/jit_unwind.c:_PyJitUnwind_GdbRegisterCode` | 14 | 7,254 |
| `_jit_opt_rs::unbox::plan_unboxing` | 52 | 5,296 |
| `CPython/Python/optimizer.c:_PyOptimizer_Optimize` | 3 | 2,520 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__BOX_UNBOXED_SLOTS_r00` | 8 | 2,502 |
| `_PyJitOpt_PlanLeafCalls` | 5 | 2,088 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__ITER_NEXT_RANGE_r23` | 5 | 746 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__STORE_SUBSCR_ARRAY_D_r30` | 2 | 722 |
| `./jit_stencils-x86_64-unknown-linux-musl.h:emit__BINARY_OP_SUBSCR_ARRAY_I_UNBOXED_r21` | 2 | 484 |

In both kernel profiles, the largest direct callers are unsymbolized address functions in the Callgrind tree. Their level-two caller is `_PyJIT_Entry`, linking these `memcpy` calls to generated JIT code. The setup-only profiles have no direct JIT/executor caller edges matching the listed function families and no `_PyJIT_Compile` calls.
