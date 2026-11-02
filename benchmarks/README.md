# CPython benchmark laboratory

`benchmarks/` compares compatible CPython interpreters across standardized
benchmarks and repository-owned application workloads. The result is a set of
separate timing, resident-memory, and allocation measurements, plus a report
that shows which workloads improved and which resource gates passed.

The project supports macOS arm64 and Linux x86-64/arm64 only. Windows, Intel
macOS, other Linux architectures, and every other platform are unsupported.
This harness currently runs its full Linux profile on x86-64 and its native
macOS profile on arm64; Linux arm64 benchmark support has not been implemented.

The full timing, process-memory, and allocation profiles run on Linux amd64,
including the `x86_64-unknown-linux-musl` python-build artifact. Native Apple
Silicon macOS supports paired timing and external process-tree RSS and sampled
physical-footprint passes for Mach-O interpreters. Physical footprint is Apple's
charged dirty-memory ledger, not USS or PSS; its sequential samples can miss
short-lived children. The kernel root lifetime peaks are separate from the
sampled tree values. The allocation pass remains unavailable. The
harness consumes built interpreters; it does not alter the product build.

## Three independent passes

The same workload is run separately for each measurement. Instrumentation can
change interpreter behavior, so measurements from one pass never stand in for
another.

| Pass | Measures | Instrumentation |
| --- | --- | --- |
| Timing | Wall latency, throughput, repeatability, and kernel CPU user/system seconds per operation | No memory polling, Memray, allocator tracing, or `perf record` |
| Memory | Process-tree peak and steady resident footprint | External Linux `/proc` or macOS `libproc` for sampled RSS and physical footprint; not used in timing runs |
| Allocations | Allocation count and bytes, heap high-water mark, allocator and native origins | Memray in a reduced, semantically equivalent run |

Allocation-pass elapsed time is diagnostic only. Do not compare it with normal
timing results. Memory and allocation profiles explain resource changes that a
timing score alone cannot show.

## Memory-only workload execution

`bench.py run --memory-only` runs the existing smoke or realworld memory
passes with a quick or standard profile. It omits timed rounds and CPU
accounting, including the direct-child CPU ledger for cold ZIP imports.
Memory sampling still covers the process tree with the same iteration counts,
round counts, sampling interval, RSS floors, and correctness digests. Missing
RSS or incomplete process-tree observations fail the memory-only record.

Preparation remains explicit: third-party site bytecode is compiled before
measurement, the first-request Django fixture is prepared and made immutable,
and each interpreter runs one uninstrumented warmup. Timed rounds use fresh
children and provide no reusable interpreter state; they are skipped rather
than used as preparation. Removing them changes run order and may change host
cache conditions, so calibrate this mode separately from earlier paired runs.

Raw workloads and comparisons declare `measurement_mode: "memory-only"`.
Timing is `not_measured` with no samples or invented values, and compact
attempt evidence requires memory and output completeness without CPU records.
Legacy records without this mode retain mandatory timing evidence. Unknown
measurement modes fail validation. RSS still has the same diagnostic coverage
limitations as other RSS runs; this mode does not claim PSS or unique memory.

`rust-cpython/perf.py --memory-only` selections pass this execution mode to the
workload harness. Its replicated memory acceptance policy remains the authority
for memory-phase decisions; the shared report retains its RSS coverage limits.

## Prepare and run

From the repository root, `make bench` is the end-to-end product comparison.
On Linux amd64 it fetches the locked product and benchmark inputs, prepares the
offline benchmark image, builds and packages the product when its archive is
absent, then runs the repository-owned application suite against pinned PBS.
The default profile is `standard` (timing plus process-tree memory); set
`BENCH_PROFILE=rigorous` to add allocation tracing.

On native Apple Silicon macOS, the same entrypoint fetches the matching PBS
archive, builds and packages the macOS product when absent, and runs the
dependency-free smoke suite locally with separate timing and RSS passes. It skips Docker and the
Linux-only wheelhouse. The result records macOS version, model, Apple CPU name,
performance/efficiency core counts, and memory; automatic baseline names are
keyed to that runner identity. The macOS comparison has no offline network
boundary and does not report allocation results. `make bench-full`
remains Linux amd64 only.

`make bench-full` adds the complete pinned pyperformance suite, which can take
substantially longer. Set `BENCH_PYPERFORMANCE_SELECTION` to a pyperformance
group or benchmark name to run a smaller selection; it defaults to `all`.

Product input fetching and benchmark preparation may use the network. Linux
measurements run with networking disabled in the benchmark container; macOS
measurements are local. The benchmark result and runner-specific baseline
snapshot are written under `benchmarks/results/` and `benchmarks/baselines/`.

To prepare or run the components separately, first check the host and fetch
the locked benchmark inputs. Fetching is the network-enabled preparation step;
measurements use the prepared inputs offline.

```sh
python3 benchmarks/bench.py doctor
python3 benchmarks/bench.py fetch --target x86_64-unknown-linux-musl
python3 benchmarks/bench.py prepare
```

For native Apple Silicon, fetch the macOS reference archive, the production
CPython 3.15 macro inputs, and the experimental CPython 3.16 Django inputs:

```sh
python3 benchmarks/bench.py fetch --target aarch64-apple-darwin
```

The profiles trade run time for coverage. `quick` is for iteration, `standard`
is the regular real-world timing and process-memory comparison, and `rigorous`
adds repeated Memray allocation passes. Allocation verdicts are marked
`not_gated` in quick and standard results.

```sh
# Short smoke run
python3 benchmarks/bench.py run \
  --preset pbs --suite smoke --profile quick

# Real application workloads
python3 benchmarks/bench.py run \
  --preset pbs --suite realworld --profile standard

# Full pyperformance and real-world run
python3 benchmarks/bench.py run \
  --preset pbs --suite full --profile rigorous

# Limit a run while investigating one workload or category
python3 benchmarks/bench.py run \
  --preset pbs --suite realworld --profile quick \
  --workload django_wsgi_request
python3 benchmarks/bench.py run \
  --preset pbs --suite realworld --profile quick --category tooling
```

Use `--help` for the exact options available to a command. Runs execute inside
the pinned benchmark image with Docker networking disabled by default;
`--container` makes that choice explicit. `--local` is a diagnostic mode and
does not provide the offline network boundary. The same image accepts either
interpreter at run time; it does not contain a baked-in baseline.

The native macOS path runs selected repository-owned workloads locally, without
Docker isolation, CPU affinity, allocation tracing, or Linux `perf` diagnostics. Use offline
workloads and label this measurement mode in any performance conclusion:

```sh
python3 benchmarks/bench.py run \
  --baseline rust-cpython/stage-no-rust/bin/python3.16 \
  --candidate rust-cpython/stage/bin/python3.16 \
  --baseline-label "Rust-for-CPython 3.16 without _base64" \
  --candidate-label "Rust-for-CPython 3.16 with _base64" \
  --baseline-kind custom --candidate-kind custom \
  --suite smoke --profile standard --local
```

Use `--timing-only` when an RSS pass is unwanted. The smoke suite includes small and large Base64 operations. The no-Rust
interpreter uses public `base64.b64encode`; the candidate calls
`_base64.standard_b64encode` directly. Both validate identical output. Other
smoke workloads reveal interpreter-wide changes such as startup and
serialization overhead. This is a targeted same-source comparison, not a
general macOS benchmark profile.
For a selected stdlib-only workload such as `--suite realworld --workload
zlib_decode_1m`, the runner prepares no third-party wheel site and needs no
wheelhouse. Package workloads still require their locked inputs.

The controller selects package inputs from both interpreter identities, their
platform, and the selected workload groups. The legacy CPython 3.14 Linux musl
route uses the full [`inputs.lock.json`](inputs.lock.json) set. The product's
CPython 3.15 route uses separate macOS arm64 and Linux x86-64 musl locks for
the `macros` group. Both interpreters must match the lock's version, platform,
and CPython ABI. Package workloads fail before measurement if no lock covers
their platform or workload group.

## Compare interpreters

The runner has explicit baseline and candidate inputs. Each can be a Python
executable or a compatible packaged artifact such as a `.tar.gz`:

```sh
python3 benchmarks/bench.py run \
  --baseline /opt/cpython-upstream/bin/python3 \
  --candidate dist/x86_64-unknown-linux-musl/python.tar.gz \
  --baseline-label "upstream CPython" \
  --baseline-kind upstream \
  --candidate-label "python-build CPython 3.15.1" \
  --suite realworld --profile standard
```

This is how to supply an upstream CPython baseline: provide the executable or
artifact you built or obtained, built and configured comparably to the
candidate. Set both `--baseline-label "upstream CPython"` and
`--baseline-kind upstream` only for an upstream build; the kind is recorded in
the result and distinguishes this baseline from PBS. The harness does not
build upstream CPython. It checks interpreter compatibility and records both
identities. Use
`--allow-cross-version` only for an intentional cross-version investigation;
the report labels that research comparison. Cross-version runs import from
source on both sides, since a shared precompiled bytecode tree would favor
one interpreter's cache format.

The `pbs` preset resolves the pinned Astral PBS 20260610 artifact and the
python-build artifact for the native target. Linux amd64 uses the musl pair in
the isolated benchmark container. Native Apple Silicon uses the macOS PBS
archive and requires `--local`. Reports label the reference
**Astral PBS**, never upstream CPython. The pinned PBS artifacts contain
CPython 3.14.6, so they are not same-version references for the 3.15.1
product. The runner rejects that pair unless `--allow-cross-version` is
explicitly supplied; such a report is a research comparison and establishes
no product parity claim. Only a comparable upstream CPython build, explicitly
labeled `upstream CPython`, can establish the upstream memory parity guarantee.

Use the same comparison machinery to calibrate a Python against itself:

```sh
python3 benchmarks/bench.py self-compare --python /path/to/python \
  --suite realworld --profile rigorous
```

Rigorous self-comparison characterizes timing, memory, and allocation
repeatability so noise allowances are based on observed variation. To
regenerate a report from an existing result directory, run:

```sh
python3 benchmarks/bench.py compare benchmarks/results/<run>/run
```

Every completed `run` or `self-compare` updates a compact baseline JSON file
under `benchmarks/baselines/`. Its stable path is keyed by runner hardware,
baseline interpreter kind/version, suite/profile, and selected workload names.
On macOS, that runner identity includes the Apple CPU and model plus core and
memory counts. Repeating the same comparison updates that file in place. `--record-baseline
PATH` selects a specific file under `benchmarks/baselines/` instead:

```sh
python3 benchmarks/bench.py self-compare --python image-python \
  --suite realworld --profile standard \
  --record-baseline benchmarks/baselines/linux-amd64-self-control.json
```

An existing result can refresh its automatically selected baseline without
repeating measurements:

```sh
python3 benchmarks/bench.py record-baseline benchmarks/results/<run>/run
```

The snapshot records per-workload timing, process-tree memory, allocation
rounds when collected, interpreter identity, input-lock and image identity,
and runner CPU, memory, kernel, affinity, and load details. It leaves raw
sampler data and Memray captures in the ignored run directory. Baseline files
are atomically refreshed after successful measurements. A self-comparison is
labeled `self_control_calibration`; it characterizes the runner and harness,
but it does not substitute for a product-versus-upstream baseline.

For a comparison that needs a compact export, pass `--evidence` to `run` or
`self-compare` with a new path under `benchmarks/results/`:

```sh
python3 benchmarks/bench.py run \
  --baseline /path/to/control/python3.14 \
  --candidate /path/to/candidate/python3.14 \
  --baseline-kind custom --candidate-kind custom \
  --suite realworld --profile standard --workload catalog_search_form \
  --local --output benchmarks/results/search-run \
  --evidence benchmarks/results/search-run-evidence.json
```

This writes one compact, Git-ready JSON file with every timing and memory
attempt's output digest, wall time, kernel CPU, and available memory counters,
plus input, interpreter, host, noise, and verdict details. The generated run
directory remains disposable. `--evidence` refuses to overwrite an existing
record and skips the automatic baseline snapshot; use `--record-baseline` as
well when a stable baseline snapshot is wanted. Compact export currently
supports quick and standard smoke or realworld runs. Record an external
`/usr/bin/time -l` controller total separately when whole-lane CPU and peak
resident memory are needed.

The benchmark dependency prefix is separate from the tested interpreter. The
controller prepares it from verified wheelhouse inputs, so the tested
distribution does not need to ship `pip` or `venv`. Use `--wheelhouse PATH` to
point at an already fetched wheelhouse.

## Workloads

The `realworld` suite complements pyperformance with larger operations that
exercise recognizable application paths. Its initial workloads cover:

| Workload | What it exercises |
| --- | --- |
| `django_wsgi_request` | Django's WSGI handler, middleware, routing, ORM-backed view, and response construction |
| `django_wsgi_first_request` | One fresh interpreter through Django setup, handler construction, first SQLite open, and one validated WSGI response |
| `django_asgi_request` | The corresponding Django ASGI request path |
| `django_orm_10k` | Materializing and processing a deterministic 10,000-row ORM dataset |
| `django_template_realistic` | Rendering a template with loops, nested values, escaping, and formatting |
| `pylint_source` | A real lint pass over a pinned Python source corpus |
| `pycparser_source` | Parsing pinned C source inputs with pycparser |
| `compileall_source` | Compiling a pinned Python source tree into fresh bytecode output |
| `python_startup` | Fresh interpreter startup |
| `import_django` | Importing Django in a fresh process |
| `import_app_stack` | Importing Django, FastAPI/Pydantic, and SQLAlchemy in a fresh process |
| `pip_install_wheelhouse` | Installing a locked collection of predominantly pure-Python wheels into a fresh target directory, offline |
| `zlib_decode_1m`, `zlib_stream_4k`, `gzip_extract_1m` | Public one-shot, streaming, and gzip decompression of fixed compressed inputs |
| `zip_read_wheel`, `zipimport_cold` | ZIP member extraction and cold import through public stdlib paths |
| `difflib_unified_mostly_equal`, `difflib_unified_reordered` | Complete unified diffs over fixed sparse edits and reordered repetitive blocks |
| `serialization_roundtrip` | Repeated pickle encoding/decoding of a shared object graph with correctness checks |
| `multiprocess_pool` | Process creation and work distribution, with child processes included in memory accounting |

The Django requests go through framework handlers rather than timing a
standalone template call. Schema setup and fixture creation happen outside the
timed operation. The first-request workload prepares and hashes a read-only
SQLite fixture before each paired run; its timing spans process launch through
exit, including exactly one request and the first database open. Warm requests
retain their internal timing boundary. Each workload checks a result invariant
or digest so a regression cannot appear faster by silently skipping work.
Operation counts allow timing, memory, and allocation results per request,
row, file, import process, or other unit of work.

The `pyperformance` suite remains the standardized layer. Its timing and
Linux memory runs are separate; its aggregate is context, not the overall
verdict. The harness runs the pinned pyperformance 1.14 scripts directly with
an external dependency prefix. For `2to3`, it also prepends the
pyperformance wheel's bundled `vendor/src` compatibility code for that
benchmark only. This avoids the script's fallback to the tested interpreter's
`pip` to install the removed `lib2to3`; the product does not ship `pip`. When
the bundled `lib2to3` source is present, `2to3` runs normally. If it is
missing, the result records `2to3` as unsupported with the reason instead of
silently omitting it.

Before measurement, the baseline calibrates loops for each selected manifest
script with one pyperf process and one measured value. The harness reads the
baseline's value-bearing run metadata, then passes the selected fixed count
through `--loops` to baseline and candidate timing and both separate memory
passes. The raw per-script calibration files under `pyperformance/calibration/`
preserve the source counts. `pyperformance/comparison.json` records the chosen
count for each manifest script, unsupported reasons, and fixed-loop coverage.
Scripts with missing or inconsistent loop
metadata are marked unsupported. A script that emits several named results
accepts only one loop count, so the harness uses the largest baseline count
when it is at most four times the smallest; wider differences are marked
unsupported. Every measured result is checked against the requested count.
Calibration time is excluded from the timing comparison.

Payload versions are pinned separately from the interpreter baseline in
[`inputs.lock.json`](inputs.lock.json). Several pyperformance dependencies
use compatible releases because the older upstream pins lack CPython 3.14
musllinux wheels: coverage 7.16.1, dulwich 0.24.10, SQLAlchemy 2.0.43,
MarkupSafe 3.0.3, PyYAML 6.0.3, and urllib3 2.5.0. These versions define this
harness's payload baseline; they are not interchangeable with measurements
from pyperformance using a different dependency set. The pyperformance Django
payload remains pinned at 3.2.4 and may still fail on some CPython 3.14
workloads. The repository-owned Django macros use Django 6.1.1 and should be
read as a distinct workload version. The `full` suite combines pyperformance
with the repository-owned macro suite.

The CPython 3.15 macro inputs use separate native wheel sets:
[`inputs.macos-cp315.lock.json`](inputs.macos-cp315.lock.json) targets macOS
arm64, and [`inputs.linux-cp315.lock.json`](inputs.linux-cp315.lock.json)
targets x86-64 musl. Both pin Pydantic 2.14.1 with `pydantic-core` 2.50.1
because the CPython 3.15 wheel is not available for the CPython 3.14 lock's
2.13.5 and 2.46.5 pair. The CPython 3.15 locks contain only the `core` and
`macros` groups. They do not supply pyperformance or Memray inputs.

The experimental CPython 3.16 Django route keeps its separate pure-wheel
locks. [`inputs.macos-cp316.lock.json`](inputs.macos-cp316.lock.json) and
[`inputs.linux-cp316.lock.json`](inputs.linux-cp316.lock.json) pin Django
6.1.1, asgiref 3.12.1, and sqlparse 0.6.0. These inputs do not replace the
legacy CPython 3.14 or product CPython 3.15 locks.

The CPython 3.14 FastAPI payload uses Pydantic 2.13.5 and `pydantic-core`
2.46.5. Its `pydantic-core` wheel targets CPython 3.14 and
`musllinux_1_1_x86_64`, which is compatible with the benchmark image's musl
1.2 runtime. The CPython 3.15 locks use Pydantic 2.14.1 and `pydantic-core`
2.50.1 with native wheels for their declared targets.

## Memory and allocation measurements

The Linux memory pass samples `/proc/<pid>/smaps_rollup` for the workload process and
its descendants. It records peak total PSS, RSS, private memory (USS-like), and
process count, with the sample interval and raw samples retained in the run
data. PSS is the primary process-tree comparison because RSS counts shared
pages in every process. Steady or post-load PSS is reported only when a
workload marks a meaningful phase boundary; a short-lived batch process has
no steady-state value. A cgroup peak is an optional secondary metric; it is reported
only when a result contains a measurement. The process-tree sampler does not
require cgroup delegation.
The default sampling interval is 10 ms; use `--memory-interval-ms` to change
it, and compare runs only when they use the same interval.

On macOS, the external sampler uses filtered `libproc` process-group and
parent queries to discover the workload tree, then records time-stamped RSS,
physical-footprint totals, and process counts. The footprint values are
sequential per-PID reads, so their sampled tree peak is approximate. Before
reaping the workload root, the sampler also
reads that PID's lifetime physical-footprint peak via `proc_pid_rusage` and
uses kernel `wait4` peak RSS for the root alone. The reported
peak RSS is the larger of the sampled tree peak and the kernel root
peak, with both sources retained separately. The kernel counters catch a
short-lived root, but cannot reconstruct a simultaneous peak for children
that exited between samples. Apple's physical footprint is a charged-memory
measure, not Linux USS or PSS. PSS, unique/private resident memory,
and swap remain unavailable, never zero. The macOS peak RSS comparison is
diagnostic: clear RSS growth can fail, but a non-regression remains incomplete
for upstream memory parity because RSS counts shared pages in every process.
The first and last RSS samples remain raw diagnostics; retained RSS is
unavailable unless a workload marks an explicit steady boundary. The final
sample of a batch process is not silently relabeled as retained memory.
The sampler checks BSD birth timestamps and kernel start times to reject
detected PID reuse; escaped or very short-lived descendants can still evade
sampling. Inspect raw samples and cleanup status for each run. Short startup
memory runs hold an initialized interpreter
briefly in the separate memory pass.

Timing result files retain the workload's internal wall-latency sample and
external elapsed time separately. They also record kernel user and system CPU
seconds, raw and per logical operation. On Linux, the timing pass puts the
workload in a fresh cgroup v2 when delegation is available and reads `cpu.stat`
after process cleanup. Its user and system counters include descendants,
including processes in nested cgroups. This requires no sampling thread or
CPU controller. If delegation or the counters are unavailable, the runner
falls back to root-only `wait4` CPU and labels tree coverage incomplete.
On macOS, `wait4` for the workload root includes descendants reaped through
that process tree. The cold ZIP import workload also reports `RUSAGE_CHILDREN`
for its directly reaped interpreters; the harness retains this as a diagnostic
without adding it to
the macOS `wait4` or Linux cgroup total. The runner adds the cold ZIP child
ledger once when Linux falls back to root-only `wait4`; grandchildren and
unreaped descendants then remain excluded. Detached or unreaped descendants
remain outside the macOS total. Timeout rounds mark CPU unavailable. A CPU
comparison's `compared` status means numeric paired values were available,
not full process-tree coverage;
inspect each round's `coverage` field. The
memory pass also records CPU seconds for diagnosis, but its sampled execution
does not replace the uninstrumented timing pass.

A workload whose `cpu_boundary` is `internal` reports its own
`cpu_user_seconds` and `cpu_system_seconds` from `getrusage(RUSAGE_SELF)`
across the same loop its `elapsed_seconds` covers. Its CPU then excludes
interpreter startup, imports, input loading, and shutdown, and its coverage is
the kernel-region label. The process total stays in `process_total_seconds`.
The JIT kernels use this boundary. A missing or invalid region value fails the
round, and kernel-region rounds never compare against process rounds.

The allocation pass uses Memray separately from timing and memory. It records
total allocations, total allocated bytes, peak heap, allocator information,
and native allocation origins. Counts and bytes are normalized by the
workload's operation count where that is meaningful. Detailed allocation
tracing adds substantial overhead, which is why those durations are not
performance results. The raw Memray binary captures and machine-readable
stats are retained for every profiled run, including separate child captures
when following forks. They live under the ignored `benchmarks/results/` tree;
remove the run directory when the captures are no longer useful, and do not
commit them.

## Verdicts

Reports show per-workload timing, memory, and allocation changes. A timing
geometric mean cannot hide an individual real-world memory regression. Each
explicit interpreter pair receives per-workload memory checks. Against a
designated upstream CPython baseline, each primary workload's peak PSS must
remain within a small, noise-aware parity allowance; applicable steady-state
and post-load memory are checked as well. The allowance is derived from
repeated baseline variation and a small page-scale floor, not a large blanket
percentage. A PBS-relative failure reports a regression against PBS; it does
not establish the upstream guarantee. Workload-level failures remain visible
even when category or suite aggregates improve.

Allocation churn has its own verdict. Clear increases in allocated bytes or
allocation count per operation are highlighted, and material regressions can
fail the resource verdict. Memory failures and allocation failures are
reported separately so the source of a resource regression is clear. PBS
results remain labeled as PBS comparisons and do not receive the upstream
memory guarantee.

## Fairness and limits

The first implementation is for native Linux amd64. It discovers CPU topology
from Linux sysfs instead of assuming a fixed relationship between logical CPU
numbers. Paired runs record placement and ordering; the runner alternates
baseline/candidate order to limit thermal and frequency drift. For a
publication-quality comparison, use an otherwise quiet host and repeat the
run. Provenance records host, CPU, kernel, container, affinity, environment,
and interpreter details so noisy conditions can be assessed later.
ASGI, offline pip installation, and multiprocessing are marked noisy and use
at least five separate process-memory rounds. This follows self-comparison
evidence of occasional pip peak-PSS outliers; it adds observations while
keeping the same baseline-derived resource gate.

The benchmark image is pinned and both sides run against the same image bytes.
Inputs and benchmark dependencies are hash-locked. Fetch/prepare may use the
network, but the measurement phase is offline. Page cache is not forcibly
dropped and no privileged host tuning is performed. CPU frequency, shared
memory bandwidth, virtualization, and kernel scheduling can still affect
results; the report includes repeatability data rather than treating a single
number as conclusive. Low-level `perf` counters are optional diagnostics, not
part of the timing pass. Add `--perf-stat` to collect them as separate
diagnostics for each selected workload:

```sh
python3 benchmarks/bench.py run \
  --baseline /path/to/baseline/python \
  --candidate /path/to/candidate/python \
  --suite realworld --profile standard --perf-stat
```

Missing `perf` or kernel permissions are recorded as unavailable diagnostics;
they do not alter timing measurements or the resource verdict.

## Results

Each run gets a directory under `benchmarks/results/` containing
`provenance.json`, `summary.json`, and `summary.md`. For the default
container-backed run, these files are in the run's `run/` subdirectory. The
summary includes per-workload measurements, ratios, noise estimates, and
verdict reasons. Detailed results are grouped by suite and workload; timing,
memory, and allocation data remain distinct. Logs, raw memory samples, and
Memray captures support investigation and remain under the ignored results
tree.

Provenance includes baseline and candidate identity, Python version and build
configuration, artifact hashes, benchmark input lock hash, benchmark package
versions, container image, host and CPU details, affinity and run order,
measurement settings, and relevant Python environment variables. It is
intended to make a result reproducible or to show why it should be rejected.
Installed-size figures use each executable's containing prefix. For a system
Python such as the image interpreter, that prefix includes unrelated system
files; compare size figures only for similarly laid-out standalone installs.
