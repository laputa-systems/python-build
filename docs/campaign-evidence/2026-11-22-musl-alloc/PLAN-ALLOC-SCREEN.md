# OD23 allocator screen: musl lane, whole-process mimalloc vs jemalloc

Owner direction (2026-11-22, OD23): the musl lane uses the latest mimalloc and/or
jemalloc, not musl's native malloc. This screen picks the lane's baseline allocator
before the musl rebaseline. Written before any allocator stage is measured.

## Arms

All three are built at the same `campaign/musl-lane` head, with `--jit --serc`, in the
pinned Alpine 3.24.2 container (production LLVM 23.1.5, Rust `nightly-2026-09-15`).

| Stage | Allocator | Input |
| --- | --- | --- |
| `perf-musl-base` | musl 1.2.6 mallocng | reference only; cannot be selected (owner direction) |
| `perf-musl-mi` | mimalloc v3.7.0, MIT | release source, SHA-256 pinned in `linux-musl-toolchain.lock.json` |
| `perf-musl-je` | jemalloc 5.4.0, BSD-2-Clause | release source, SHA-256 pinned in the same lock |

The allocator is linked whole-archive into `libpython3.16.so`, which exports the full
malloc family, so it serves C, Rust staticlib and Rust extension allocations through
symbol interposition. CPython's object allocator (pymalloc) is unchanged. Upstream
defaults; no tuning options. Each stage carries a runtime proof that the allocator serves
the process.

## Measurements (C-ALLOC-1 recipe, each arm against `perf-musl-base`)

- Cachegrind `Ir`: two runs each of the stdlib compile corpus (Serc), and of the
  JIT_SET kernels fft, nbody and sparse_mat_mult with the JIT on. The 0.1% repeatability
  rule applies; one fresh pair may be rerun on all arms (C-TLS-2 amendment A). A second
  miss records that workload as non-repeatable, and it does not count toward S1.
- Allocator-family self `Ir` share per workload: informational.
- A4 residency probe (`2026-11-22-probe2`): private-memory median, `.pyc` and source modes.
- A5: peak-RSS median over three `/usr/bin/time -f %M` corpus runs.

## Pass rules (per arm, against the base)

- S0 correctness, any failure rejects the arm: compile parity (path-only
  `_sysconfigdata` differences normalised), `--serc-verify` on the 4 modules, the 7
  suites with failure IDs equal to the base's, fuzz seed 0 (2,000 programs, 0
  mismatches), the four-thread compile smoke, and ctypes `CDLL` of the stage's libpython
  from the container's Alpine `python3`.
- S1 CPU: the geometric mean of the `Ir` ratios over the corpus and the three kernels is
  at least 1% lower, and no single workload is more than 1% higher. Operation counts and
  result digests are unchanged.
- S2 memory: A4 private-memory median at most +1 MiB in both modes, and A5 peak RSS at
  most +10%.

## Decision

- Exactly one arm passes S0–S2: it becomes the lane's baseline allocator and the default
  of `perf.py build --allocator`.
- Both pass: the owner chooses, from these results.
- Neither passes: the results go to the owner. musl's native malloc is not selected by
  default, because the owner ruled it out.

These are instruction counts and memory on an unqualified host. They select a lane
baseline; they are not timing claims. Cachegrind does not model cache misses, lock
contention or page faults, which is where allocators also differ. Timing waits for a
qualified host.

## After selection

Rebaseline on the selected allocator: J-ES-2, J-TLS-1, C-TLS-2 and C-REL-3, each parent
against candidate, under their original predictions. J-TLS-1 and C-TLS-2 are expected to
be null on musl, since musl has no `__tls_get_addr` slow path; a null result is recorded
and their revert is proposed to the owner. J-ES-3 then builds and runs under its
preregistered plan. C-ALLOC-1/2 stay retired; this screen supersedes the Serc-only
allocator mechanism.
