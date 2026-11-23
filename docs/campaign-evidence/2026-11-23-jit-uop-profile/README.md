# JIT kernels' cost by uop, after J-BITCAST-1 (attribution only)

The stage is `perf-jbitcast1-cand` (`92a1111`). For each kernel there is one callgrind run with
`--dump-instr=yes` and `PYTHON_JIT_MAP` set, using the pinned Alpine image, `musl-dbg` and
`--network none` (`run-callgrind*.{py,sh}`, `callgrind/`). `support/uop_attr.py` maps each
JIT-page instruction address to the uop range written in the jitmap (no ranges overlap). It
then sums each uop's self `Ir` and the inclusive `Ir` of the calls it makes. Only 126 `Ir` of
JIT-page code falls outside a mapped uop. The raw tables are in `uop-attr-*.txt`.

These figures are attributions, not comparisons. "Share" is relative to the stage's net kernel
`Ir` from `2026-11-23-j-bitcast/run/net-kernel.txt`: nbody 0.723 G, fft 1.732 G and sparse 2.454 G.

## sparse_mat_mult (net 2.454 G)

| Uops | Self `Ir` | Call `Ir` | Share |
| --- | ---: | ---: | ---: |
| array subscripts: `_BINARY_OP_SUBSCR_ARRAY_{I_UNBOXED,D_UNBOXED,D_UNBOXED_INDEX}` | 399 M | 374 M | 31.5% |
| `_STORE_FAST_FUSED_{INT,FLOAT}`, which frees the replaced value | 233 M | 150 M | 15.6% |
| `_ITER_NEXT_RANGE`, which boxes each range int | 140 M | 207 M | 14.1% |
| `_BOX_FLOAT` | 85 M | 155 M | 9.8% |
| object-type-version guards (3 register variants) | 116 M | 0 | 4.7% |

## scimark_fft (net 1.732 G)

| Uops | Self `Ir` | Call `Ir` | Share |
| --- | ---: | ---: | ---: |
| `_STORE_SUBSCR_ARRAY_D` (boxed float store) | 147 M | 174 M | 18.5% |
| `_BINARY_OP_SUBSCR_ARRAY_D`, `_..._D_UNBOXED_INDEX` | 84 M | 145 M | 13.2% |
| `_BINARY_OP_r23`, generic | 10 M | 102 M | 6.5% |
| `_BINARY_OP_ADD_INT`, `_BINARY_OP_MULTIPLY_INT_INPLACE_RIGHT` | 46 M | 123 M | 9.8% |
| `_BOX_FLOAT` | 34 M | 63 M | 5.6% |

## nbody (net 0.723 G)

| Uops | Self `Ir` | Call `Ir` | Share |
| --- | ---: | ---: | ---: |
| `_STORE_FAST_FUSED_FLOAT_OR_NULL` (two variants) | 82 M | 17 M | 13.7% |
| list subscripts: `_STORE_SUBSCR_LIST_INT_FLOAT`, `_BINARY_OP_SUBSCR_LIST_INT_UNBOXED` | 115 M | 16 M | 18.2% |
| `_BOX_FLOAT` | 28 M | 50 M | 10.8% |
| `_UNPACK_SEQUENCE_{LIST,TUPLE}` | 57 M | 0 | 7.9% |
| `_BINARY_OP_POWER_FLOAT` | 5 M | 52 M | 7.8% |

## Readings

- **Array element access** is the largest single cost in sparse, at 31.5%, and 31.7% in fft
  when the store is included. Each unboxed subscript uop also has 16–31 `Ir` of self cost per
  execution, beyond the 25-`Ir` helper call. J-ARRAY-1, inlining the helpers, addresses the
  call part. It waits for the owner because the Track J slots are full.
- **Range iteration and fused stores** box and then free the loop index and the replaced
  local: 30% of sparse. Unboxing the range iterator's value would remove the boxing call in
  `_ITER_NEXT_RANGE` and the int free in `_STORE_FAST_FUSED_INT`. That is a loop-type
  extension, and J-ES-3 closed those for this round.
- **nbody** has no single dominant uop. Its costs spread over list stores, fused local stores
  and float boxing.
