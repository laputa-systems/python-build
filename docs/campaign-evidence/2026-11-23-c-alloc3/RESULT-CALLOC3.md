# C-ALLOC-3 result: retired (A4 source fails)

Plan: `PLAN-CALLOC3.md` (`a8c5e2a`), attempt 1/1. Parent `perf-calloc3-parent` at `a8c5e2a`,
candidate `perf-calloc3-cand` at `7929ad6`. Both are musl malloc, JIT + Serc, no pystats. Raw
receipts are under `run/` (`96bbbf3`; `raw.sha256`, 277 entries, 0 mismatches), and the large
files are under `/home/ubuntu/exp-calloc3/`. These are instruction counts and memory on an
unqualified host, so none is a timing claim. Every cachegrind pair, including the setup-only
pairs, repeated within 0.1%.

| Prediction | Parent | Candidate | Change | Rule | Verdict |
| --- | ---: | ---: | ---: | --- | --- |
| A1 corpus `Ir` (mean) | 37,961,426,456 | 34,580,541,699 | −8.906% | ≤ −4% | holds |
| A2 musl `src/malloc/` self `Ir` | 4,843,775,818 | 93,504,872 | −98.07% | ≤ −75% | holds |
| A3 nbody net kernel `Ir` | 1,566,286,985 | 1,565,948,812 | −0.022% | ±0.5% | holds |
| A3 sparse net kernel `Ir` | 5,129,794,585 | 5,129,953,542 | +0.003% | ±0.5% | holds |
| A4 `.pyc` private memory | 4,054 KiB | 4,062 KiB | +8 KiB | ≤ +100 KiB | holds |
| A4 source private memory | 4,494 KiB | 5,218 KiB | **+724 KiB** | ≤ +100 KiB | **fails** |
| A5 peak RSS (median of 3) | 55,944 KiB | 56,808 KiB | +1.54% | ≤ +5% | holds |

Operation counts and digests match across stages: nbody 20,000, fft 50, sparse 100,000.

## Whole versus net kernel `Ir`

Net kernel `Ir` is the whole `Ir` minus the setup-only `Ir`. The setup-only run verifies the
wheels and runs `_load()`, which includes Serc compiling the kernel's source.

| Kernel | Whole | Setup-only | Net |
| --- | ---: | ---: | ---: |
| nbody | −0.639% | −1.320% | −0.022% |
| sparse_mat_mult | −0.287% | −1.327% | +0.003% |
| fft (informational) | −0.443% | −1.318% | −0.007% |

The whole-process change is all setup. On a whole-`Ir` control, nbody would have failed again,
as C-ALLOC-2's did (−0.83%). This confirms that C-ALLOC-2's control failure came from Serc
compiling the kernel source, not from kernel work.

## Correctness (all pass)

- **Compile parity:** 2,104 entries, with one raw difference, `_sysconfigdata`. The recorded
  normaliser's pattern missed one path form. After every `wt-calloc3-{parent,cand}` and
  `calloc3-{parent,cand}` path is normalised, the two files are byte-identical, so the
  difference is path-only.
- **The 7 suites:** both stages fail in `test_capi` and `test_sys`, with the same 39 failure IDs
  and no stage-only IDs.
- **Other checks:** `--serc-verify` on 4 modules, fuzz seed 0 (2,000 programs), the four-thread
  compile and Alpine `ctypes.CDLL` all pass.

## Deviation

The precompile step exited 1 on both stages. The cause is `Lib/test` fixtures that are invalid
on purpose, so no compiler can compile them. Every other module was compiled. No prediction
depends on this step: kernel `Ir` is judged net of a setup run that contains the remaining
compile work.

## Decision

A4 source fails, so C-ALLOC-3 is retired as preregistered. This closes the Serc allocator
mechanism. mimalloc's retention under source compiles stays above the 100 KiB budget even with
`purge_delay=0`, `eager_commit=0` and `allow_thp=0`. The `.pyc` path, which is the common
production path, stays within 8 KiB, but the rule binds both modes.
