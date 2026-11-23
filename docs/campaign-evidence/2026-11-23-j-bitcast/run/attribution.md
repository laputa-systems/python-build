# J-BITCAST-1 candidate `memcpy` callers

Raw candidate attribution only. No verdict or `RESULT` file was produced.

Profiles use Valgrind Callgrind 3.25.1 in the existing lane measurement container. `musl-dbg=1.2.6-r3` was installed; loader and debug-file BuildIDs match at `d1656128169ecc566bb0afcbbcbda02c5ac8cd74`. Caller trees were generated with `--dump-instr=no`, then annotated with the bundled Callgrind 3.18.1 annotator using `--auto=no --show=Ir --threshold=100 --inclusive=yes --tree=caller`.

Each immediate-caller table is the union of the top five by inclusive `Ir` and top five by call count; the rank columns show both orderings.

## Totals

| Run | Total `Ir` | `memcpy` calls | `memcpy` inclusive `Ir` |
|---|---:|---:|---:|
| candidate nbody kernel | 2,139,255,418 | 150,621 | 16,786,980 |
| candidate sparse kernel | 3,883,363,252 | 151,756 | 17,076,118 |

## candidate nbody kernel

| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |
|---|---:|---:|---:|---:|
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 4,951,440 | 11,960 | 1 | 3 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 1,983,090 | 17,057 | 2 | 2 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 1,650,276 | 26,659 | 3 | 1 |
| `musl/src/malloc/mallocng/realloc.c:__libc_realloc` | 1,450,292 | 9,599 | 4 | 5 |
| `CPython/Objects/codeobject.c:_PyCode_New` | 546,188 | 4,688 | 5 | 9 |
| `CPython/Objects/unicodeobject.c:unicode_decode_utf8` | 525,088 | 9,992 | 7 | 4 |

## candidate sparse kernel

| Caller | `memcpy` Ir | Calls | Ir rank | Call rank |
|---|---:|---:|---:|---:|
| `zlib_rs::crc32::braid::crc32_braid::<5>` | 4,951,440 | 11,960 | 1 | 3 |
| `CPython/Objects/bytesobject.c:_PyBytes_FromStringAndSize` | 2,005,410 | 17,481 | 2 | 2 |
| `CPython/Objects/unicodeobject.c:PyUnicode_FromKindAndData` | 1,650,276 | 26,659 | 3 | 1 |
| `musl/src/malloc/mallocng/realloc.c:__libc_realloc` | 1,475,772 | 9,653 | 4 | 5 |
| `CPython/Objects/codeobject.c:_PyCode_New` | 549,798 | 4,719 | 5 | 9 |
| `CPython/Objects/unicodeobject.c:unicode_decode_utf8` | 533,354 | 10,152 | 7 | 4 |

## Address-labelled JIT callers beneath `_PyJIT_Entry`

Both caller trees contain `_PyJIT_Entry` and `_PyJIT_Compile` (7 compile calls), confirming JIT execution in the profiled commands. Neither profile has an address-labelled immediate caller of `memcpy` beneath `_PyJIT_Entry`: the JIT-originating `memcpy` count and inclusive `Ir` are both zero in each run.

| Run | Immediate caller address | `memcpy` calls | `memcpy` Ir | `_PyJIT_Entry` calls | Ir on caller edge |
|---|---|---:|---:|---:|---:|
| candidate nbody kernel | none found | — | — | — | — |
| candidate sparse kernel | none found | — | — | — | — |
