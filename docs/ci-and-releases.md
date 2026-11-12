# CI and releases

`.github/workflows/release.yml` is the release workflow. It runs only on
`workflow_dispatch`, and its preflight job rejects any ref other than the
default branch. macOS build and smoke use `xcode-27`; Linux qualification uses
native `ubuntu-26.04` amd64 and `ubuntu-26.04-arm` runners. `lint.yml` runs
`make lint`. Fast controller tests run before builds.

Assembly validates the exact three-target set, product version, built
revision, LLVM policy, qualification verdicts, and archive digests before
producing the release assets. Smoke installs the assembled
python-build-standalone-compatible archive bytes through a local uv mirror into
fresh uv directories. The release job then rechecks the ref, refuses to run when
the tag exists, creates `release-<commit>` at the triggering commit through the
GitHub API, writes notes with the shared `Write release notes` step, and
publishes with `gh release create ... --prerelease --verify-tag`.

## Release assets

Each target has, per the build-repository contract:

- `cpython-<version>-<target>.tar.xz`, the installation extracted from the
  qualified archive and repacked by `laputa-buildlib` (same tar content, xz
  preset 6).
- `cpython-<version>-<target>.manifest.json`, with `schema_version`,
  `product`, `version`, `target`, `source_revision`, `sources_lock_sha256`, and
  the archive and compatibility asset digests, in that key order.

uv, the consumer of the compatibility layout, reads these beside them:

- `cpython-<version>+<date>-<target>-install_only_stripped.tar.gz`, the
  qualified archive bytes. `<date>` is the UTC build date (`YYYYMMDD`).
- `download-metadata.json`, whose URLs point into the `release-<commit>`
  release, and `smoke-metadata.json`, the same entries under the canonical
  python-build-standalone prefix for the local-mirror smoke.
- `artifact-<triple>.json`, the qualification record of each target.
- `assembly.json`, the assembly report.

`SHA256SUMS` covers every other asset in file-name order, and the workflow
checks it with `sha256sum -c` before publishing. Consumers that pin the
compatibility archives, such as pi-build's `tools/toolchain.pins`, name the
`release-<commit>` tag and the dated file name of a published release.

`bootstrap-inputs` is an input mirror, not a product release; see
[build-system.md](build-system.md).

A final release requires P1-P5 receipts, the Linux wave, a fresh
three-platform qualification at the release revision, exact uv-installed
archive checksums, source and tag identity, and full failure accounting with
no stale production claims. An earlier CI success cannot substitute for that
run. The open items are tracked in [open-work.md](open-work.md).

`build/uvmirror.py` validates the three target artifacts and their manifests,
then creates the release assets and uv metadata in a new output directory.
