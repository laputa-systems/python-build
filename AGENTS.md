# python-build

Independent build system for locked CPython 3.15.1 and its native
dependencies. It produces relocatable, OpenSSL-free installations for macOS
arm64 and Linux x86-64/arm64. Windows, Intel macOS, other Linux
architectures, and every other target are unsupported. No glibc enters any
artifact.

`rust-cpython/` is a separate CPython 3.16 Rust research lane. Its source pin
and results do not affect the production targets, and Linux arm64 is not
implemented there.

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
```

## Invariants

- Keep exactly the three production targets, platform CPU baselines, and the
  declared dependency closure.
- Do not add platforms, production dependencies, product versions, or change
  public package/API contracts without an explicit scope decision.
- Keep the payload OpenSSL-free and glibc-free.
- A sealed build must identify the enforced network boundary; macOS
  `sandbox-exec` containment is weaker than a Linux container and is reported
  as such.
- PGO is deferred until the owner reopens it. JIT, BOLT, the free-threaded
  ABI, and the tail-call interpreter stay disabled.
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
| RTLS backend | [backend/README.md](backend/README.md) |
| Rust lane build and harness | [rust-cpython/README.md](rust-cpython/README.md) |
| Rust lane contract and verdicts | [rust-cpython/ENGINEERING.md](rust-cpython/ENGINEERING.md) |
| Rust route suites | [rust-cpython/ROUTES.md](rust-cpython/ROUTES.md) |
| C module placement design | [rust-cpython/C_CORE_BATCH.md](rust-cpython/C_CORE_BATCH.md) |
