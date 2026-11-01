# CI and releases

CI is manually dispatched. macOS build and smoke use `xcode-27`; Linux
qualification uses native `ubuntu-24.04` amd64 and `ubuntu-24.04-arm` runners.
Fast controller tests run before builds.

Assembly validates the exact three-target set, product version, built
revision, LLVM policy, qualification verdicts, and archive digests before
producing uv metadata. Smoke installs the assembled archive bytes through a
local mirror into fresh uv directories. Release tags must resolve to the
revision recorded in every artifact.

A final release requires P1-P5 receipts, the Linux wave, a fresh
three-platform qualification at the release revision, exact uv-installed
archive checksums, source and tag identity, and full failure accounting with
no stale production claims. An earlier CI success cannot substitute for that
run. The open items are tracked in [open-work.md](open-work.md).

`build/uvmirror.py` validates the three target artifacts and their manifests,
then creates uv metadata and checksums in a new output directory.
