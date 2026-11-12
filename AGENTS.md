# python-build

Independent build system for locked CPython 3.15.1 and its native
dependencies. It produces relocatable, OpenSSL-free installations for macOS
arm64 and Linux x86-64/arm64. Windows, Intel macOS, other Linux
architectures, and every other target are unsupported. No glibc enters any
artifact.

The 3.15.1 product is stable and is not modified for lane work.
`rust-cpython/` is a separate CPython 3.16 Rust research lane whose frontier
is the Rust JIT and a Rust source compiler ported from Serc: one campaign,
[rust-cpython JIT](docs/CAMPAIGN-RUST-CPYTHON-JIT.md), owns their goal, end
state, and gates. Neither the lane's source pin nor its
results affect production targets; Linux arm64 is not implemented there.

## Commands

```text
python3 build.py doctor
python3 build.py fetch --target <triple>
python3 build.py build --target <triple> [--pgo-jobs N]
python3 build.py qualify --target <triple> [--pgo-jobs N]
python3 build.py controller-test
python3 build.py verify --target <triple>
python3 build.py compare-reference --target <triple>
python3 build.py reproduce --target aarch64-apple-darwin [--pgo-jobs N]
python3 build/uvmirror.py --build <YYYYMMDD> --repo <owner/repo>
python3 build/relock_linux.py
python3 build/mirror_inputs.py [--check]
```

`make build|fetch|package|verify|repro|clean|lint` wrap these commands; `TARGET` is a Rust triple
and defaults to the host. Releases come from `.github/workflows/release.yml`; the
`bootstrap-inputs` release is an input mirror outside the release contract and never uses a
`release-` tag.

## Invariants

- `laputa-buildlib` reaches `buildsys` only through `buildsys/wheel.py`: change its version in
  `sources.lock.json`, `pyproject.toml`, and `uv.lock` together. Import it after `buildsys` in scripts,
  or through `buildsys` re-exports, because importing `buildsys` is what makes the wheel importable.
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
| rust-cpython JIT campaign: goal, gates, ledger | [docs/CAMPAIGN-RUST-CPYTHON-JIT.md](docs/CAMPAIGN-RUST-CPYTHON-JIT.md) |
| RTLS backend | [backend/README.md](backend/README.md) |
| Rust lane build and harness | [rust-cpython/README.md](rust-cpython/README.md) |
| Rust lane contract and verdicts | [rust-cpython/ENGINEERING.md](rust-cpython/ENGINEERING.md) |
| Rust route suites | [rust-cpython/ROUTES.md](rust-cpython/ROUTES.md) |
| C module placement design | [rust-cpython/C_CORE_BATCH.md](rust-cpython/C_CORE_BATCH.md) |
