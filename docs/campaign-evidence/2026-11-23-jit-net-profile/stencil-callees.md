# Out-of-line calls from JIT code after J-BITCAST-1 (attribution only)

Inputs: the J-BITCAST-1 candidate callgrind profiles,
`/home/ubuntu/exp-jbitcast1/profiles/cand-{nbody,scimark_sparse_mat_mult}-kernel.callgrind.xz`
(checksums in `2026-11-23-j-bitcast/run/raw.sha256`). `support/cgjitcallees.py` sums the
calls made directly by JIT code pages and each callee's inclusive `Ir`. These are attribution
figures, not comparisons. Net kernel `Ir` on this stage is 2.454 G for sparse and 0.723 G for
nbody.

## sparse_mat_mult: 1.04 G inclusive `Ir` (42% of net) in callees

| Callee | Calls | Inclusive `Ir` | Per call |
| --- | ---: | ---: | ---: |
| `_PyArray_ReadDoubleItem` | 9,983,964 | 249,599,100 | 25.0 |
| `_PyLong_FromCompactTstate` | 5,370,768 | 214,542,591 | 39.9 |
| `_PyFloat_FromDoubleTstate` | 4,991,982 | 154,751,442 | 31.0 |
| `_PyArray_ReadIntItem` | 5,183,788 | 129,594,700 | 25.0 |
| `_PyLong_ExactDeallocTstate` | 4,796,584 | 86,338,512 | 18.0 |
| `_PyFloat_ExactDeallocTstate` | 4,991,982 | 64,895,766 | 13.0 |
| `_Py_CallBuiltinClass_StackRef` | 95,903 | 41,322,967 | 430.9 |
| everything else | | 97.6 M | |

## nbody: 0.17 G inclusive `Ir` (23% of net) in callees

| Callee | Calls | Inclusive `Ir` | Per call |
| --- | ---: | ---: | ---: |
| `_PyFloat_FromDoubleTstate` | 2,585,958 | 80,164,698 | 31.0 |
| `_PyFloat_PowExactTstate` | 192,397 | 51,754,793 | 269.0 |
| `_PyFloat_ExactDeallocTstate` | 2,747,140 | 35,712,820 | 13.0 |

## Readings

- **Array element access:** 15.5% of sparse's net `Ir`. The helpers are 25 instructions,
  including the frame-pointer prologue and the typecode check. Each call site also spills
  and reloads three cached stack registers (`_BINARY_OP_SUBSCR_ARRAY_D_UNBOXED_INDEX_r11`).
  This is J-ARRAY-1, which waits for the owner because the JIT slots are full.
- **Box and free pairs:** 21% of sparse's net `Ir` and 16% of nbody's. Sparse boxes 5.4 M
  ints and 5.0 M floats, and frees about as many. The values are boxed only to be freed again.
  The remedy is wider unboxing, not a faster boxing call.
- **nbody's JIT code itself** has 554 M `Ir` (77% of net) and calls out rarely. Splitting
  that cost by uop needs per-address `Ir` mapped through the JIT map; that is the next profile.
