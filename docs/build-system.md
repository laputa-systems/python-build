# Build system

`build.py` drives one target at a time from locked inputs:

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

`build` is the development build. `qualify` uses `sandbox-exec` on the native
macOS host and the locked, network-disabled BuildKit path for Linux. Linux
targets build and run in native-architecture containers; emulated timing is
never performance evidence. `compare-reference` and `reproduce` are optional
and cannot change an accepted package.

## Inputs

`sources.lock.json` pins build, trust, and comparison inputs with URL, version,
SHA-256, size, role, license, and purpose. Reference-only entries are never
build inputs. `bootstrap.lock.json` pins the macOS compiler archive and
attestation, host requirements, the Linux image, the Linux LLVM archive, and
the complete APK closure.
`buildsys/inputs.py` verifies cached bytes and safe extraction; invalid
objects are replaced only after the downloaded bytes pass verification.

Every pinned object is also stored as an asset named by its SHA-256 in the
`bootstrap-inputs` release of this repository. Fetches try that mirror first
and the recorded upstream URL second. Only transport failures fall back;
bytes that do not match the lock fail the fetch from either source. Alpine
edge drops superseded APKs, so the mirror is what keeps an existing pin
buildable.

```text
python3 build/relock_linux.py     # re-resolve both APK closures
python3 build/mirror_inputs.py    # upload every pin the mirror lacks
python3 build/mirror_inputs.py --check
```

`build/relock_linux.py` exports each locked base image without running it
and installs each layer's direct packages into that root with x86_64 `apk`
and scripts disabled. Both architectures therefore resolve on one host. The
recorded closure is the change in the installed database, which is what the
image builds assert. Run `mirror_inputs.py` before committing a new lock.

## Build records

The common build record binds source and patch identities, target and CPU
baseline, dependency inputs, compiler policy and effective flags, bootstrap
identity, commands, build mode, and isolation evidence. Locked identity and
observed host paths are separate. A sealed build must identify the enforced
network boundary. macOS `sandbox-exec` containment is weaker than a Linux
container and must be reported as such.

Recipes compose baseline, optimization, PIC, hardening, frame-pointer, and
ThinLTO policy in one place. Use the selected toolchain's actual tools and run
the LTO compile/archive/link/run fixture before expensive builds. Never
silently drop a required compiler flag or accept staged output from a
different source, patch set, target, or recipe.

PGO is deferred on every target and out of scope until the owner reopens it;
`Target.pgo` and `--pgo-jobs` keep the machinery. JIT, BOLT, the free-threaded
ABI, and the tail-call interpreter are disabled.

## Outputs

Packaging publishes one immutable generation under
`dist/<target>/generations/`, selected by an atomic `dist/<target>/ACTIVE`
file. Readers use `buildsys.generations.active_generation` and never read
loose files in `dist/<target>/`. See
[qualification-and-packaging.md](qualification-and-packaging.md) for the
verdict, publication, comparison, and reproduction contracts.
