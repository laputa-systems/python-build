# python-build

Independent build system for locked CPython 3.15.1 and its native
dependencies. It produces relocatable, OpenSSL-free installations for macOS
arm64 and Linux x86-64/arm64. Windows, Intel macOS, other Linux
architectures, and every other target are unsupported. No glibc enters any
artifact.

`rust-cpython/` is a separate CPython 3.16 Rust research lane. The
next research milestone is to pin the latest verified upstream 3.16 `main`
commit while retaining the existing LLVM 23.1.4/macOS arm64 and
23.1.2/Linux x86-64 toolchains. See the [JIT modernization roadmap](docs/CAMPAIGN-JIT-CINDERX-316.md).
Neither its source pin nor results affect production targets; Linux arm64
is not implemented in the research lane.

## Commands

```text
python3 build.py doctor
python3 build.py fetch --target <triple>
python3 build.py build --target <triple> [--pgo-jobs N]
python3 build.py qualify --target <triple> [--pgo-jobs N]
python3 build.py controller-test
python3 build.py compare-reference --target <triple>
python3 build.py reproduce --target aarch64-apple-darwin [--pgo-jobs N]
python3 build/uvmirror.py --tag <tag> --repo <owner/repo>
python3 build/relock_linux.py
python3 build/mirror_inputs.py [--check]
```

## Invariants

- Keep exactly the three production targets, platform CPU baselines, and the
  declared dependency closure.
- Do not add platforms, production dependencies, product versions, or change
  public package/API contracts without an explicit scope decision.
- Keep the payload OpenSSL-free, glibc-free, and GCC-free: no libgcc_s
  dependency and no GCC-compiled object. The Linux build image carries no GCC
  toolchain package.
- A sealed build must identify the enforced network boundary; macOS
  `sandbox-exec` containment is weaker than a Linux container and is reported
  as such.
- PGO is deferred until the owner reopens it. BOLT, the free-threaded ABI, and
  the tail-call interpreter stay disabled. The experimental JIT is disabled in
  production and opt-in only for `rust-cpython/` lane builds; JIT work never
  changes the product build.
- Lane crates pin exact versions in the overlay `Cargo.lock`, record their
  license, and add no production dependency or platform.
- Never relax suite or benchmark thresholds to hit a score.
- Do not run formatters, linters, pre-commit hooks, or CI-only changes for
  unrelated work. Do not push unless asked. Commits use Conventional Commit
  format.

## Documentation

| Topic | Doc |
| --- | --- |
| Targets, components, TLS trust | [docs/product-targets.md](docs/product-targets.md) |
| Inputs, build records, isolation, recipes | [docs/build-system.md](docs/build-system.md) |
| Qualification, packaging, reproduction | [docs/qualification-and-packaging.md](docs/qualification-and-packaging.md) |
| CI and releases | [docs/ci-and-releases.md](docs/ci-and-releases.md) |
| Remaining production and lane work | [docs/open-work.md](docs/open-work.md) |
| Latest 3.16 / CinderX-informed Rust JIT roadmap | [docs/CAMPAIGN-JIT-CINDERX-316.md](docs/CAMPAIGN-JIT-CINDERX-316.md) |
| JIT hill-climb gates and historical results | [docs/CAMPAIGN-JIT-CLIMB.md](docs/CAMPAIGN-JIT-CLIMB.md) |
| RTLS backend | [backend/README.md](backend/README.md) |
| Rust lane build and harness | [rust-cpython/README.md](rust-cpython/README.md) |
| Rust lane contract and verdicts | [rust-cpython/ENGINEERING.md](rust-cpython/ENGINEERING.md) |
| Rust route suites | [rust-cpython/ROUTES.md](rust-cpython/ROUTES.md) |
| C module placement design | [rust-cpython/C_CORE_BATCH.md](rust-cpython/C_CORE_BATCH.md) |
