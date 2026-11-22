# J-ES-2 deterministic instruction-count screen (preregistered 2026-11-22, before any run)

Purpose: get host-independent CPU evidence for J-ES-2 while timing is blocked on a
qualified host. These are instruction counts (`Ir`), which do not depend on host load
(Status line 581). They are not timing, are not counted toward any gate, and cannot be an
acceptance.

Stages (JIT, no pystats, built with `perf.py build --jit`):
- parent `perf-ir-parent` at `da74796` (the parent of `bdf86ef`);
- candidate `perf-ir-jes2` at `0c3b192`.

Command, per stage and kernel, run from `wt-cloc-parent`:

    LD_LIBRARY_PATH=$STAGE/lib PYTHON_JIT=1 valgrind --tool=cachegrind --cache-sim=no \
      --smc-check=all --cachegrind-out-file=$OUT/$name-$kernel-$run.cg \
      $STAGE/bin/python3.16 -S -c 'import sys; from benchmarks.workloads import jit_kernels; print(jit_kernels.run(sys.argv[1]))' $kernel

Kernels: `scimark_sparse_mat_mult`, `scimark_fft`, `nbody`. Two runs per (stage, kernel).
Metric: the total `I refs` line. The printed result must match across all runs of a kernel.

Validity: within each stage, the two runs of each kernel must differ by ≤ 0.1%. If any pair
fails, the screen is INVALID and nothing is concluded.

Predictions (candidate vs parent, run-1 values):
- I1: sparse `Ir` falls by ≥ 1.0%.
- I2: FFT `Ir` stays within ±0.5% (negative control).
- I3: nbody `Ir` stays within ±0.5% (unrelated control).

Outcome is recorded either way. I1 failing means removing 5.0 M periodic checks and generic
stores did not reduce executed instructions measurably. That is recorded against J-ES-2's
structural claim; it does not reopen or reject the candidate, whose status is set only by
P1–P4 and timing.
