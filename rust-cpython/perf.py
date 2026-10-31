#!/usr/bin/env python3
"""Fast-iteration performance builds and hill-climbing measurements for the
Rust-for-CPython lane.

Builds matched optimized interpreters without PGO or ThinLTO so a
baseline/candidate pair can be rebuilt quickly. The only deliberate
difference between a pair is the source overlay: an empty overlay for the
pristine-fork control, the committed overlay for a Rust candidate.

All outputs live under isolated `work/perf/<tag>/`, `stage-<tag>/`, and
`<tag>-*` log/report names, where the tag always starts with `perf-`. The
coverage `work/build`, `stage`, and `results/build.json` trees are never read
or removed here.

Measurement and compilation share one host. Builds and suites take a shared
host lease; `bench` takes it exclusively with writer priority, so a timing
pass never overlaps a compiler or test run started through this tool from
any worktree of this repository.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

LANE = Path(__file__).resolve().parent
REPO = LANE.parent
SCRATCH = REPO / ".work" / "tmp"


def _lane_build():
    spec = importlib.util.spec_from_file_location("lane_build", LANE / "build.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the lane coverage builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules["lane_build"] = module
    spec.loader.exec_module(module)
    return module


lb = _lane_build()
LaneError = lb.LaneError

import perf_verdict

CONTROL = "perf-upstream"
INCUMBENT = "perf-rust"
ALIASES = {"@control": CONTROL, "@incumbent": INCUMBENT}
# The gate guards use stdlib-only workloads plus the
# Django workloads covered by benchmarks/inputs.macos-cp316.lock.json.
GATE_WORKLOADS = (
    "python_startup",
    "serialization_roundtrip",
    "zlib_decode_1m",
    "gzip_extract_1m",
    "django_wsgi_request",
    "django_template_realistic",
    "import_django",
)
# Registered workloads whose 3.16 input closures do not exist yet.
UNAVAILABLE_WORKLOADS = frozenset({
    "pylint_source", "pycparser_source", "import_app_stack", "pip_install_wheelhouse",
})
# Configure selects Cargo's dev profile whenever --enable-optimizations is
# absent. Every make invocation, including `make install`, must override it:
# the Rust extension rules move their artifact out of the target directory,
# so a make run without the override rebuilds and installs dev artifacts.
MAKE_VARS = ("CARGO_PROFILE=release", "CARGO_TARGET_DIR=release")
# Overlay paths whose change alters configure output or the generated module
# rules. An incremental build refuses them; run a clean build instead.
CLEAN_BUILD_PATHS = (
    re.compile(r"configure(\.ac)?"),
    re.compile(r"Makefile\.pre\.in"),
    re.compile(r"pyconfig\.h\.in"),
    re.compile(r"aclocal\.m4"),
    re.compile(r"Modules/Setup(\..*)?"),
    re.compile(r"Modules/makesetup"),
)
CompressionRoute = Literal["private", "upstream"]
UPSTREAM_COMPRESSION_OMITTED_FILES = ("Lib/zlib.py", "Modules/_bz2module.c")
UPSTREAM_COMPRESSION_OMITTED_SETUP_ROWS = frozenset({"_bz2", "_bz2_rs", "_zlib_rs"})
UPSTREAM_COMPRESSION_OMITTED_MEMBERS = frozenset({"_bz2_rs", "_zlib_rs"})
UPSTREAM_COMPRESSION_MODULES = ("zlib", "_bz2", "binascii")
COMPLETED = {"built", "suite-failed", "suite-passed", "all-failed", "all-passed"}
DEFAULT_MIN_IDLE = 80.0
# compileall_source hashes the .pyc bytes it writes; the checklist's marshal
# route documents that Rust marshal output differs from pristine CPython's
# selective-reference bytes, so that workload is not comparable to the
# control. It stays comparable between two Rust builds.
KNOWN_CONTROL_MISMATCHES = frozenset({"compileall_source"})
PROFILE_MODULE_ROUNDS = {"quick": 3, "standard": 5, "rigorous": 10}
MODULE_TARGET_SECONDS = 0.25
MODULE_MAX_ITERATIONS = 20_000
QUIET_WAIT_SECONDS = 180


def _check_name(name: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) is None:
        raise LaneError(f"perf build name must match [a-z0-9][a-z0-9-]*: {name!r}")
    return name


def _tag(name: str) -> str:
    return name if name.startswith("perf-") else f"perf-{name}"


def _paths(name: str, lane: Path = LANE) -> dict[str, Path]:
    tag = _tag(name)
    work = lane / "work" / "perf" / tag
    logs = lane / "logs"
    return {
        "work": work,
        "source_parent": work / "source",
        "overlay_staging": work / "overlay-staging",
        "overlay_manifest": work / "overlay-files.json",
        "build": work / "build",
        "stage": lane / f"stage-{tag}",
        "configure_log": logs / f"{tag}-configure.log",
        "build_log": logs / f"{tag}-build.log",
        "install_log": logs / f"{tag}-install.log",
        "compression_log": logs / f"{tag}-compression-engines.log",
        "cargo_log": logs / f"{tag}-cargo-offline-check.log",
        "report": lane / "results" / f"{tag}.json",
    }


# ---------------------------------------------------------------- git state


def _git(*args: str, cwd: Path = LANE) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LaneError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _common_dir() -> Path:
    return Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir"))


def _primary_repo() -> Path:
    common = _common_dir()
    if common.name != ".git":
        raise LaneError(f"cannot locate the primary checkout from git common dir {common}")
    return common.parent


def _git_state() -> dict[str, Any]:
    return {
        "commit": _git("rev-parse", "HEAD"),
        "overlay_dirty": bool(_git("status", "--porcelain", "--", "overlay")),
    }


def _harness_identity() -> dict[str, Any]:
    paths = [LANE / name for name in ("perf.py", "perf_verdict.py", "perf_modules.py")]
    paths += [REPO / "benchmarks/bench.py", *sorted((REPO / "benchmarks/harness").glob("*.py"))]
    hashes = {path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"git_commit": _git("rev-parse", "HEAD"), "source_sha256": digest, "files": hashes,
            "dirty": bool(_git("status", "--porcelain", "--", "perf.py", "perf_verdict.py",
                                "perf_modules.py", "../benchmarks/bench.py", "../benchmarks/harness"))}


# -------------------------------------------------------------- host lease


def _lease_dir() -> Path:
    return _common_dir() / "rust-cpython-perf"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lease_holders() -> list[dict[str, Any]]:
    holders = []
    for path in sorted((_lease_dir() / "holders").glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and _pid_alive(int(record.get("pid", 0))):
            holders.append(record)
    return holders


def _describe_holders() -> str:
    holders = lease_holders()
    if not holders:
        return "an untracked holder"
    return "; ".join(f"{item['kind']} {item['what']} (pid {item['pid']}, {item['worktree']}, "
                     f"since {item['started']})" for item in holders)


def _flock(handle, operation: int) -> None:
    try:
        fcntl.flock(handle, operation | fcntl.LOCK_NB)
        return
    except BlockingIOError:
        pass
    print(f"WAIT  host lease busy: {_describe_holders()}", flush=True)
    fcntl.flock(handle, operation)


@contextlib.contextmanager
def host_lease(kind: str, what: str, *, timings: dict[str, float] | None = None) -> Iterator[None]:
    """Hold the repository-wide host lease.

    `build` and `test` share the host; `measure` is exclusive. Every caller
    passes through a turnstile first, and a measurement keeps it closed while
    it runs, so waiting measurements are not starved by new builds.
    Optional timing records only the two lock-acquisition calls, excluding
    holder publication and the protected operation.
    """
    if kind not in {"build", "test", "measure"}:
        raise ValueError(f"unknown lease kind {kind!r}")
    directory = _lease_dir()
    (directory / "holders").mkdir(parents=True, exist_ok=True)
    holder = directory / "holders" / f"{os.getpid()}.json"
    with (directory / "turnstile.lock").open("a+") as turnstile, \
            (directory / "host.lock").open("a+") as host:
        if timings is not None:
            wait_started = time.monotonic()
        _flock(turnstile, fcntl.LOCK_EX)
        _flock(host, fcntl.LOCK_EX if kind == "measure" else fcntl.LOCK_SH)
        if timings is not None:
            timings["lease_wait"] = time.monotonic() - wait_started
        if kind != "measure":
            fcntl.flock(turnstile, fcntl.LOCK_UN)
        holder.write_text(json.dumps({
            "pid": os.getpid(), "kind": kind, "what": what, "worktree": str(REPO),
            "started": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }) + "\n")
        try:
            yield
        finally:
            holder.unlink(missing_ok=True)


def _cpu_idle_percent() -> float | None:
    if lb.IS_LINUX:
        def read() -> tuple[int, int]:
            fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            values = [int(value) for value in fields]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return idle, sum(values)
        try:
            idle_before, total_before = read()
            time.sleep(1)
            idle_after, total_after = read()
        except (OSError, ValueError, IndexError):
            return None
        if total_after <= total_before:
            return None
        return 100.0 * (idle_after - idle_before) / (total_after - total_before)
    # `top`'s first sample averages since boot; the next two each cover one
    # second, so a single-second blip does not decide quietness alone.
    result = subprocess.run(["/usr/bin/top", "-l", "3", "-n", "0", "-s", "1"],
                            capture_output=True, text=True, check=False)
    matches = [float(value) for value in re.findall(r"CPU usage:.*?([\d.]+)% idle", result.stdout)]
    return sum(matches[1:]) / len(matches[1:]) if len(matches) >= 3 else None


def _on_ac_power() -> bool | None:
    if lb.IS_LINUX:
        supplies = list(Path("/sys/class/power_supply").glob("*/online"))
        states = []
        for path in supplies:
            try:
                states.append(path.read_text().strip())
            except OSError:
                continue
        return any(state == "1" for state in states) if states else None
    result = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return "'AC Power'" in result.stdout


def _host_sample(min_idle: float, *, wait_seconds: float = 0) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    while True:
        idle = _cpu_idle_percent()
        sample = {
            "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cpu_idle_percent": idle,
            "ac_power": _on_ac_power(),
            "load_average": list(os.getloadavg()),
        }
        if lb.IS_LINUX:
            limit = float(os.environ.get("PERF_GATE_MAX_LOAD", "0.7"))
            cores = os.cpu_count() or 1
            sample["quiet"] = (sample["load_average"][0] <= limit * cores
                               and sample["ac_power"] is not False)
        else:
            sample["quiet"] = (idle is not None and idle >= min_idle
                               and sample["ac_power"] is not False)
        if sample["quiet"] or time.monotonic() >= deadline:
            return sample
        time.sleep(3)


# ------------------------------------------------------------------- build


def _perf_flags(toolchain, target, *, stage: Path) -> dict[str, str]:
    # Fast iteration over maximum optimization: -O2 instead of -O3, no
    # debug info, no link-time optimization. Profile-guided optimization
    # is off by construction: configure never receives
    # --enable-optimizations. Test modules stay enabled so a perf build
    # can still run focused CPython suites and the full suite for
    # correctness. Cargo uses the release profile through MAKE_VARS: a dev
    # profile would punish the Rust routes and is not a performance result.
    if lb.IS_LINUX:
        flags = lb._platform_flags(toolchain, target, prefix=stage)
        optimized = flags["CFLAGS"].replace("-O0 -g3", "-O2")
        return {**flags, "CFLAGS": optimized, "CXXFLAGS": optimized}
    flags = " ".join(("-O2", target.cpu_baseline_cflag, "-fPIC",
                      f"-mmacosx-version-min={toolchain.deployment_target}"))
    return {
        "CFLAGS": flags,
        "CXXFLAGS": flags,
        "CPPFLAGS": f"-isysroot {toolchain.sdkroot}",
        "PY_CPPFLAGS": f"-isysroot {toolchain.sdkroot}",
        "LDFLAGS": f"-mmacosx-version-min={toolchain.deployment_target}",
        "PKG_CONFIG_PATH": lb._brew_pkg_config_path(),
        **lb._macos_deployment_cache(toolchain.deployment_target),
    }


def _overlay_members(route: CompressionRoute) -> set[str]:
    members: set[str] = set()
    for manifest in lb.OVERLAY.glob("Modules/_*/Cargo.toml"):
        package = tomllib.loads(manifest.read_text()).get("package", {})
        name = package.get("name") if isinstance(package, dict) else None
        if not isinstance(name, str) or not name:
            raise LaneError(f"overlay Rust module has no package name: {manifest}")
        members.add(name)
    if route == "upstream":
        members -= UPSTREAM_COMPRESSION_OMITTED_MEMBERS
    return members


def _built_members(build_log: Path, members: set[str]) -> list[str]:
    pattern = re.compile(r"(?:Compiling|Fresh) ([^ ]+) v[^ ]+")
    package_pattern = re.compile(r"\bCARGO_PKG_NAME=(?:'([^']+)'|([^\s]+))")
    crate_pattern = re.compile(r"\brustc\s+--crate-name\s+(\S+)")
    found: set[str] = set()
    for line in build_log.read_text(errors="replace").splitlines():
        match = pattern.search(line)
        if match and match.group(1) in members:
            found.add(match.group(1))
        # Parallel make output can split Cargo's progress line. Its verbose
        # compiler command still identifies the package and library crate;
        # a package's build script has a different crate name and cannot count.
        package = package_pattern.search(line)
        crate = crate_pattern.search(line)
        if package and crate:
            name = package.group(1) or package.group(2)
            if name in members and crate.group(1) == name.replace("-", "_"):
                found.add(name)
    return sorted(found)


def _build_python(build_dir: Path) -> Path:
    makefile = build_dir / "Makefile"
    if not makefile.is_file():
        raise LaneError(f"configured build Makefile is missing: {makefile}")
    name = lb._make_value(makefile, "BUILDPYTHON")
    suffix = lb._make_value(makefile, "BUILDEXE")
    name = name.replace("$(BUILDEXE)", suffix)
    if not name or "$" in name or Path(name).name != name:
        raise LaneError(f"cannot resolve CPython build interpreter name {name!r}")
    return build_dir / name


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LaneError(f"no perf build report at {path}")
    report = json.loads(path.read_text())
    if not isinstance(report, dict):
        raise LaneError(f"perf build report is not an object: {path}")
    return report


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(root: Path) -> dict[str, Any]:
    """Digest every path, mode, symlink target, and file byte under root."""
    digest = hashlib.sha256()
    files = 0
    size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode() + b"\0")
        elif path.is_file():
            stat = path.stat()
            digest.update(b"F\0" + relative + b"\0" + oct(stat.st_mode & 0o777).encode()
                          + b"\0" + _sha256_file(path).encode() + b"\0")
            files += 1
            size += stat.st_size
    return {"sha256": digest.hexdigest(), "files": files, "bytes": size}


def _check_logs(build_log: Path, install_log: Path) -> None:
    build_text = build_log.read_text(errors="replace")
    if "--profile release" not in build_text:
        raise LaneError("perf build did not invoke cargo with --profile release")
    for log in (build_log, install_log):
        if "--profile dev" in log.read_text(errors="replace"):
            raise LaneError(f"cargo ran with --profile dev; the stage is not a release build: {log}")


def verify_release_artifacts(build: Path, stage: Path, members: set[str]) -> dict[str, str]:
    """Prove each installed Rust extension is a byte-identical release artifact."""
    target = build / "target" / lb.TARGET
    if (target / "debug").exists():
        raise LaneError(f"a dev-profile Cargo tree exists: {target / 'debug'}")
    suffix = "*.so" if lb.IS_LINUX else "*.dylib"
    release = {_sha256_file(path) for path in (target / "release").rglob(suffix)
               if path.is_file() and not path.is_symlink()}
    installed: dict[str, str] = {}
    dynload = next(stage.glob("lib/python3.*/lib-dynload"), None)
    if dynload is None:
        raise LaneError(f"installed lib-dynload is missing under {stage}")
    for extension in sorted(dynload.glob("*.so")):
        module = extension.name.split(".", 1)[0]
        if module not in members:
            continue
        digest = _sha256_file(extension)
        if digest not in release:
            raise LaneError(f"{extension.name} does not match any release-profile Cargo artifact")
        installed[module] = digest
    if not installed:
        raise LaneError("no installed Rust extension modules were found to verify")
    return installed


def verify_upstream_compression(stage: Path, toolchain) -> None:
    """Prove the stock zlib, bzip2 and CRC modules carry no system codec library."""
    dynload = next(stage.glob("lib/python3.*/lib-dynload"), None)
    if dynload is None:
        raise LaneError(f"installed lib-dynload is missing under {stage}")
    for module in UPSTREAM_COMPRESSION_MODULES:
        extensions = sorted(dynload.glob(f"{module}.*.so"))
        if len(extensions) != 1:
            raise LaneError(f"expected one installed {module} extension, found {len(extensions)}")
        if lb.IS_LINUX:
            readelf = toolchain.llvm_prefix / "bin" / "llvm-readelf"
            text = subprocess.run([str(readelf), "-d", str(extensions[0])],
                                  capture_output=True, text=True, check=True).stdout
            needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", text)
            system_codecs = [name for name in needed if re.search(r"lib(z|bz2)\.so", name)]
        else:
            linked = subprocess.run(["otool", "-L", str(extensions[0])], capture_output=True,
                                    text=True, check=True).stdout
            system_codecs = [line.split()[0] for line in linked.splitlines()[1:]
                             if re.search(r"/lib(z|bz2)\.", line.split()[0])]
        if system_codecs:
            raise LaneError(f"{extensions[0].name} links system codec libraries: {system_codecs}")
    private = sorted(path.name for module in UPSTREAM_COMPRESSION_OMITTED_MEMBERS
                     for path in dynload.glob(f"{module}.*.so"))
    if private or (next(stage.glob("lib/python3.*/zlib.py"), None) is not None):
        raise LaneError(f"upstream compression stage contains private adapters: {private or 'zlib.py'}")


def _source_aggregate_recipe(source: Path):
    path = source / "Tools/build/csv_source_aggregate.py"
    overlay = LANE / "overlay/Tools/build/csv_source_aggregate.py"
    if path.resolve() != path or not path.is_file() or path.read_bytes() != overlay.read_bytes():
        raise LaneError("source aggregate verifier is not the current owned overlay source")
    spec = importlib.util.spec_from_file_location("csv_source_aggregate", path)
    if spec is None or spec.loader is None:
        raise LaneError("cannot load source aggregate artifact verifier")
    recipe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = recipe
    spec.loader.exec_module(recipe)
    return recipe


def _prepare_csv_source_std(paths: dict[str, Path], env: dict[str, str]) -> dict[str, str]:
    env.update(lb._rust_std_source_environment(paths["source_parent"].parent / "rust-std-source"))
    metadata, source_input = lb._read_rust_std_lock()
    return {"sha256": source_input.sha256,
            "compiler_revision": metadata["compiler_revision"],
            "library_cargo_lock_sha256": metadata["library_cargo_lock_sha256"]}


# CPython installation follows helper aliases. Validate those copies first,
# then restore one real image and eleven relative names in the installed directory.
# The public extension paths remain distinct while dyld owns one image per namespace.
def _install_source_aggregate(source: Path, paths: dict[str, Path],
                              source_metadata: dict[str, str]) -> dict[str, Any]:
    build, stage = paths["build"], paths["stage"]
    work = build.parent
    if (build != work / "build" or work.parent != LANE / "work/perf"
            or not work.name.startswith("perf-") or work.resolve() != work
            or paths["source_parent"] != work / "source"
            or stage != LANE / ("stage-" + work.name) or stage.resolve() != stage):
        raise LaneError("source aggregate has no isolated candidate owner")
    recipe = _source_aggregate_recipe(source)
    try:
        receipt = recipe.verify_build_receipt(source, build, lb.TARGET, source_metadata)
    except (ValueError, RuntimeError) as error:
        raise LaneError(f"source aggregate artifact proof failed: {error}") from error
    if (receipt["schema_version"] != 2 or receipt["status"] != "complete"
            or receipt["target"] != lb.TARGET or receipt["profile"] != "release"
            or receipt["panic"] != "abort" or receipt["allocator"] != "System"):
        raise LaneError("source aggregate receipt has an incompatible runtime policy")
    names = ('_csv_rs', '_json_rs', '_typing_rs', '_tokenize_rs',
             '_datetime_rs', '_threading_rs', '_uuid_rs',
             '_collections_rs', '_sqlite3_rs', '_warnings_rs', '_socket_rs')
    if set(receipt["consumers"]) != set(names):
        raise LaneError("source aggregate requires exactly eleven consumer receipts")
    aggregate = receipt["aggregate"]
    basename = "libcpython_rust_source_aggregate356.dylib"
    release = build / "target" / lb.TARGET / "release" / basename
    final = build / "source-aggregate356/final" / basename
    canonical = build / "Modules" / basename
    if (Path(aggregate["path"]) != release or aggregate["install_id"] != "@rpath/" + basename
            or Path(aggregate["final_artifact"]["path"]) != final
            or aggregate["final_artifact"]["sha256"] != aggregate["sha256"]
            or aggregate["final_artifact"]["size"] != aggregate["size"]):
        raise LaneError("source aggregate canonical artifact owner changed")
    for path in (release, final, canonical):
        if (not path.is_file() or path.resolve() != path or path.stat().st_nlink != 1
                or path.stat().st_size != aggregate["size"] or _sha256_file(path) != aggregate["sha256"]):
            raise LaneError(f"source aggregate finalized bytes differ: {path}")
    layout = aggregate["layout"]
    if layout["schema"] != 2 or layout["page_size"] != 16384:
        raise LaneError("source aggregate layout policy changed")
    for name, flags, initial in (("__DATA_CONST", 16, (1, 3)), ("__DATA", 0, (3,))):
        segment = layout["segments"][name]
        if (segment["vmsize"] != 16384 or segment["maxprot"] != 3
                or segment["initprot"] not in initial or segment["flags"] != flags):
            raise LaneError("source aggregate data extent or protection changed: " + name)
    surface = layout["link_surface"]
    initializers = {name: flags for name, flags in surface["exports"].items() if name.startswith('_PyInit_')}
    if (initializers != {'_PyInit_' + name: 0 for name in names}
            or surface["loads"] != ["/usr/lib/libSystem.B.dylib"]):
        raise LaneError("source aggregate initializer or dependency surface changed")
    directories = list(stage.glob("lib/python3.*/lib-dynload"))
    if len(directories) != 1 or directories[0].resolve() != directories[0]:
        raise LaneError("source aggregate requires one canonical installed extension directory")
    dynload = directories[0]
    staged = dynload / basename
    if staged.is_symlink() or (staged.exists() and (not staged.is_file()
            or staged.stat().st_nlink != 1 or _sha256_file(staged) != aggregate["sha256"])):
        raise LaneError("source aggregate installed canonical image collides with different bytes")
    installed_paths = {}
    for name, record in receipt["consumers"].items():
        published = release.parent / ('lib' + name + '.dylib')
        helpers = list((build / 'Modules').glob(name + '.*.so'))
        installed = list(dynload.glob(name + '.*.so'))
        if (Path(record['path']) != published or record['sha256'] != aggregate['sha256']
                or record['size'] != aggregate['size'] or len(helpers) != 1 or len(installed) != 1
                or helpers[0].name != installed[0].name):
            raise LaneError("source aggregate consumer owner or bytes changed: " + name)
        for alias, target in ((published, release), (helpers[0], canonical)):
            if not alias.is_symlink() or os.readlink(alias) != basename or alias.resolve() != target:
                raise LaneError("source aggregate build alias is not the canonical relative name: " + name)
        path = installed[0]
        if (not path.is_file() or _sha256_file(path) != aggregate['sha256']
                or path.stat().st_size != aggregate['size']):
            raise LaneError("source aggregate installed consumer bytes changed: " + name)
        if path.is_symlink():
            if os.readlink(path) != basename or path.resolve() != staged:
                raise LaneError("source aggregate installed alias escapes its namespace: " + name)
        elif path.resolve() != path or path.stat().st_nlink != 1:
            raise LaneError("source aggregate installed copy is aliased: " + name)
        temporary = path.with_name('.' + path.name + '.aggregate-pending')
        if temporary.exists() or temporary.is_symlink():
            raise LaneError("source aggregate alias publication has an existing temporary file")
        installed_paths[name] = path
    def verify_image(path):
        if lb.macho.read_header(path).arch != 'arm64':
            raise LaneError("source aggregate is not arm64")
        signed, detail = lb.macho.signature_status(path)
        if not signed:
            raise LaneError(f"source aggregate signature is invalid: {detail}")
        if (lb.macho.dylib_id(path) != aggregate['install_id'] or lb.macho.rpaths(path)
                or lb.macho.dependencies(path) != ['/usr/lib/libSystem.B.dylib']):
            raise LaneError("source aggregate runtime loader contract changed")
    verify_image(release)
    _copy_file(release, staged)
    if _sha256_file(staged) != aggregate['sha256'] or staged.stat().st_nlink != 1:
        raise LaneError("source aggregate bytes changed during installation")
    verify_image(staged)
    for path in installed_paths.values():
        temporary = path.with_name('.' + path.name + '.aggregate-pending')
        try:
            temporary.symlink_to(basename)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        if (os.readlink(path) != basename or path.resolve() != staged
                or (path.stat().st_dev, path.stat().st_ino) != (staged.stat().st_dev, staged.stat().st_ino)):
            raise LaneError("source aggregate installed alias does not share its canonical image")
    return {"receipt": receipt, "receipt_sha256": _sha256_file(build / 'source-aggregate356/receipt.json'),
            "staged_aggregate": str(staged), "staged_aggregate_sha256": aggregate['sha256'],
            "installed_aliases": {name: str(path) for name, path in installed_paths.items()}}


def _builtin_artifact_verifier():
    proof_path = LANE / "builtin_modules.py"
    common_dir = _common_dir()
    if common_dir.name != ".git":
        raise LaneError(f"cannot locate primary Git storage from common dir {common_dir}")
    git_dir = Path(_git("rev-parse", "--absolute-git-dir"))
    object_stores = (REPO / ".cache/objects", common_dir.parent / ".cache/objects",
                     REPO / ".git/objects", git_dir / "objects", common_dir / "objects")
    identities = {(info.st_dev, info.st_ino) for path in object_stores
                  if path.exists() for info in [path.stat()]}
    for path in (proof_path,):
        if ("objects" in path.parts or ".cache" in path.parts or ".git" in path.parts
                or path.resolve() != path):
            raise LaneError(f"unowned built-in proof source: {path}")
        for part in (path, *path.parents):
            info = part.stat()
            if part.is_symlink() or (info.st_dev, info.st_ino) in identities:
                raise LaneError(f"aliased built-in proof source: {path}")
        if path.stat().st_nlink != 1:
            raise LaneError(f"hard-linked built-in proof source: {path}")
    spec = importlib.util.spec_from_file_location("builtin_artifacts", proof_path)
    if spec is None or spec.loader is None:
        raise LaneError("cannot load built-in artifact proof")
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    return proof, object_stores


def _upstream_compression_setup(setup_local: str) -> str:
    kept: list[str] = []
    dropped: set[str] = set()
    for line in setup_local.splitlines(keepends=True):
        tokens = line.split("#", 1)[0].split()
        if tokens and tokens[0] in UPSTREAM_COMPRESSION_OMITTED_SETUP_ROWS:
            dropped.add(tokens[0])
        else:
            kept.append(line)
    if dropped != UPSTREAM_COMPRESSION_OMITTED_SETUP_ROWS:
        raise LaneError("Setup.local lacks private compression rows: "
                        + ", ".join(sorted(UPSTREAM_COMPRESSION_OMITTED_SETUP_ROWS - dropped)))
    return "".join(kept)


def _stage_overlay(paths: dict[str, Path], route: CompressionRoute) -> tuple[dict[str, Any], list[str]]:
    # Copy the overlay into a scratch tree with the coverage builder's own
    # validation (no Lib/test edits, no symlinks), then diff from there.
    staging = paths["overlay_staging"]
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    overlay = lb._apply_overlay(staging)
    if route == "upstream":
        for omitted in UPSTREAM_COMPRESSION_OMITTED_FILES:
            (staging / omitted).unlink()
        setup_local = staging / "Modules" / "Setup.local"
        setup_local.write_text(_upstream_compression_setup(setup_local.read_text()))
    files = sorted(path.relative_to(staging).as_posix()
                   for path in staging.rglob("*") if path.is_file())
    return overlay, files


def _copy_file(source: Path, target: Path) -> None:
    # copyfile, not copy2: the target must get a fresh mtime so make
    # rebuilds what depends on it.
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(source.stat().st_mode & 0o777)


def _existing_source(parent: Path) -> Path:
    # Not lb._source_root: that re-verifies the pristine Cargo.lock digest,
    # which the overlay's own lockfile has already replaced.
    matches = [child for child in parent.iterdir() if (child / "configure").is_file()] \
        if parent.is_dir() else []
    if len(matches) != 1:
        raise LaneError(f"the previous source tree is missing under {parent}; run a clean build")
    return matches[0]


def _c_template_modes(template: bytes, original: bytes) -> tuple[bytes, set[str]]:
    """Remove same-guard original-C wrappers; ignore only inert full-line comments."""
    # The generator removes full-line comments and empty lines before interpreting
    # mode switches. All noncomment row bytes remain exact source-input bytes.
    def semantic(data):
        return b''.join(line for line in data.splitlines(keepends=True)
                        if line.strip() and not line.lstrip().startswith(b'#'))
    template, original = semantic(template), semantic(original)
    rows = {}
    for line in original.splitlines(keepends=True):
        match = re.fullmatch(rb'@MODULE_[A-Z0-9_]+_TRUE@([a-zA-Z0-9_]+) ([^\n]+)\n', line)
        if match:
            name = match[1].decode('ascii')
            tokens = match[2].decode('ascii').split()
            sources = [token for token in tokens if token.endswith('.c')]
            if sources and not any(token.endswith(('.toml', '.rs')) for token in tokens) and all(re.fullmatch(r'[a-zA-Z0-9_./-]+\.c', src)
                               and not Path(src).is_absolute() and '..' not in Path(src).parts for src in sources):
                rows[line] = name
    wrapped = set()
    pattern = (rb'(?P<guard>@MODULE_[A-Z0-9_]+_TRUE@)\*static\*\n'
               rb'(?P<row>(?P=guard)[^\n]+\n)(?P=guard)\*@MODULE_BUILDTYPE@\*\n')
    def unwrap(match):
        row = match['row']
        if row not in rows or rows[row] in wrapped:
            raise LaneError('placement wrapper is not a unique original C row')
        wrapped.add(rows[row])
        return row
    normalized = re.sub(pattern, unwrap, template)
    if normalized != original:
        raise LaneError('placement template changes more than original C row wrappers')
    return normalized, wrapped


def c_placement_plan(before: bytes, after: bytes, original: bytes) -> dict[str, tuple[str, ...]]:
    """Admit only static/configured-mode transitions of unchanged original C rows."""
    _, old = _c_template_modes(before, original)
    _, new = _c_template_modes(after, original)
    moved = old ^ new
    if not moved:
        raise LaneError('no original C placement transition')
    result = {}
    owners: dict[str, set[str]] = {}
    for line in original.decode('ascii').splitlines():
        match = re.fullmatch(r'@MODULE_[A-Z0-9_]+_TRUE@([a-zA-Z0-9_]+) (.+)', line)
        if match:
            sources = [token for token in match[2].split() if token.endswith('.c')]
            objects = tuple('Modules/' + str(Path(src).with_suffix('.o')) for src in sources)
            for obj in objects:
                owners.setdefault(obj, set()).add(match[1])
            if match[1] in moved:
                result[match[1]] = objects
    if any(len(owners[obj]) != 1 for objects in result.values() for obj in objects):
        raise LaneError('placement object is shared with another original module row')
    if set(result) != moved or any(not objects for objects in result.values()):
        raise LaneError('placement object owners are incomplete')
    return result


def _original_setup_template() -> bytes:
    # Read the template from the checksum-verified source input rather than
    # treating a previously overlaid configured tree as the original owner.
    import tarfile
    _, entry = lb._read_lock()
    blob = lb.Cache(lb.CACHE_ROOT).require(entry)
    with tarfile.open(blob, 'r:*') as archive:
        matches = [member for member in archive.getmembers()
                   if member.name.endswith('/Modules/Setup.stdlib.in') and member.isfile()]
        if len(matches) != 1:
            raise LaneError('pinned source has no unique standard-module template')
        stream = archive.extractfile(matches[0])
        if stream is None:
            raise LaneError('cannot read pinned standard-module template')
        return stream.read()


def invalidate_c_placement(build: Path, stage: Path, plan: dict[str, tuple[str, ...]]) -> None:
    # Make does not track compiler command changes, and install only adds
    # current shared modules. Both the old object and old shared owner must go.
    aliases = None
    marker = build / 'pybuilddir.txt'
    if marker.is_file():
        relative = Path(marker.read_text().strip())
        if relative.is_absolute() or '..' in relative.parts:
            raise LaneError('unowned extension alias directory')
        aliases = build / relative
        if aliases.exists() and (aliases.is_symlink() or aliases.resolve() != build.resolve() / relative):
            raise LaneError('aliased extension alias directory')
    for objects in plan.values():
        for relative in objects:
            (build / relative).unlink(missing_ok=True)
    (build / 'Modules/config.o').unlink(missing_ok=True)
    for name in plan:
        for directory in (build / 'Modules', aliases):
            if directory is not None:
                for path in (*directory.glob(name + '.*.so'), directory / (name + '.so')):
                    path.unlink(missing_ok=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)


def verify_c_placement_compilation(log: Path, plan: dict[str, tuple[str, ...]],
                                   modes: dict[str, bool]) -> None:
    import shlex
    commands = {}
    for line in log.read_text(errors='replace').splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if '-c' in tokens and '-o' in tokens:
            index = tokens.index('-o') + 1
            if index < len(tokens):
                commands[tokens[index]] = tokens
    for name, objects in plan.items():
        for obj in objects:
            tokens = commands.get(obj)
            if tokens is None or ('-DPy_BUILD_CORE_BUILTIN' in tokens) != modes[name]:
                raise LaneError(f'current compiler transcript does not prove C placement: {obj}')


def _verify_c_placement_rules(build: Path, plan: dict[str, tuple[str, ...]],
                             modes: dict[str, bool]) -> None:
    makefile = (build / 'Makefile').read_text()
    shared = set(lb._make_value(build / 'Makefile', 'MODSHARED_NAMES').split())
    config = (build / 'Modules/config.c').read_text()
    for name, objects in plan.items():
        initialized = re.search(r'\{\s*"' + re.escape(name) + r'"\s*,\s*PyInit_', config) is not None
        if initialized != modes[name] or (name in shared) == modes[name]:
            raise LaneError(f'generated C placement differs: {name}')
        for obj in objects:
            rules = [line for line in makefile.splitlines() if line.startswith(obj + ':')]
            flag = '$(PY_BUILTIN_MODULE_CFLAGS)' if modes[name] else '$(PY_STDMODULE_CFLAGS)'
            if len(rules) != 1 or flag not in rules[0]:
                raise LaneError(f'generated C compiler rule differs: {obj}')


def incremental_plan(staged: dict[str, bytes], current: dict[str, bytes | None],
                     previous_files: list[str]) -> dict[str, list[str]]:
    """Classify overlay changes against the source tree of the last build."""
    changed = sorted(name for name, data in staged.items() if current.get(name) != data)
    removed = sorted(set(previous_files) - set(staged))
    clean_only = sorted(name for name in changed
                        if any(pattern.fullmatch(name) for pattern in CLEAN_BUILD_PATHS))
    return {"changed": changed, "removed": removed, "clean_only": clean_only}


class OverlaySync(NamedTuple):
    source: Path
    overlay: dict[str, Any]
    files: list[str]
    staged: dict[str, bytes]
    current: dict[str, bytes | None]
    changes: dict[str, list[str]]


def _plan_overlay_sync(paths: dict[str, Path], route: CompressionRoute) -> OverlaySync:
    source = _existing_source(paths["source_parent"])
    if not (paths["build"] / "Makefile").is_file() or not paths["overlay_manifest"].is_file():
        raise LaneError("the previous build tree is incomplete; run a clean build")
    overlay, files = _stage_overlay(paths, route)
    staged = {item: (paths["overlay_staging"] / item).read_bytes() for item in files}
    current = {item: ((source / item).read_bytes() if (source / item).is_file() else None)
               for item in files}
    changes = incremental_plan(staged, current, json.loads(paths["overlay_manifest"].read_text()))
    return OverlaySync(source, overlay, files, staged, current, changes)


def _refuse_unsyncable(changes: dict[str, list[str]]) -> None:
    if changes["removed"] or changes["clean_only"]:
        raise LaneError("incremental build refused; run a clean build. Removed: "
                        f"{changes['removed'] or 'none'}; build-system changes: "
                        f"{changes['clean_only'] or 'none'}")


def _copy_changed_overlay(paths: dict[str, Path], source: Path, changes: dict[str, list[str]]) -> None:
    for item in changes["changed"]:
        _copy_file(paths["overlay_staging"] / item, source / item)
    print(f"OK    incremental overlay sync: {len(changes['changed'])} changed file(s)")


def _worker_count(jobs: int | None) -> int:
    return jobs if jobs is not None else max(1, (os.cpu_count() or 4) - 1)


def _build_environment(toolchain, target, paths: dict[str, Path], *, workers: int, sandbox) -> dict[str, str]:
    env = lb._environment(toolchain, offline=True, build_dir=paths["build"])
    env.update(_perf_flags(toolchain, target, stage=paths["stage"]))
    env.update({
        "PY_CC": str(toolchain.llvm_prefix / "bin" / "clang"),
        "PY_CPPFLAGS": env["CPPFLAGS"],
        "PY_CFLAGS": env["CFLAGS"],
        "PYTHON_BUILD_DIR": str(paths["build"]),
        "CARGO_BUILD_JOBS": str(workers),
        lb._cargo_linker_variable(): str(toolchain.llvm_prefix / "bin" / "clang"),
        "IPHONEOS_DEPLOYMENT_TARGET": "",
    })
    return sandbox.environment(env)


def _report_route(report: dict[str, Any]) -> CompressionRoute:
    route = report.get("compression_route", "private")
    if route not in ("private", "upstream"):
        raise LaneError(f"build report names an unknown compression route {route!r}")
    return route


def cargo(*, name: str, arguments: list[str]) -> int:
    _check_name(name)
    paths = _paths(name)
    with host_lease("build", f"cargo {_tag(name)}"):
        previous = _read_report(paths["report"])
        if previous.get("pristine"):
            raise LaneError(f"{_tag(name)} is the pristine control and has no overlay to run cargo on")
        if not previous.get("configured"):
            raise LaneError(f"no configured candidate build named {name!r}; run a clean build")
        sync = _plan_overlay_sync(paths, _report_route(previous))
        _refuse_unsyncable(sync.changes)
        _copy_changed_overlay(paths, sync.source, sync.changes)
        _write_json(paths["overlay_manifest"], sync.files)
        toolchain, target = lb._toolchain()
        lb._llvm_ready(toolchain)
        env = _build_environment(toolchain, target, paths, workers=_worker_count(None),
                                 sandbox=lb._sealed_sandbox())
        return subprocess.run([str(lb.CARGO_HOME / "bin" / "cargo"), *arguments],
                              cwd=sync.source, env=env, check=False).returncode


def build(*, name: str, empty_overlay: bool, jobs: int | None = None,
          incremental: bool = False, upstream_compression: bool = False) -> int:
    _check_name(name)
    if jobs is not None and jobs < 1:
        raise LaneError("build jobs must be positive")
    if incremental and empty_overlay:
        raise LaneError("the pristine control is always a clean build")
    if upstream_compression and (incremental or empty_overlay):
        raise LaneError("--upstream-compression selects the route of a clean candidate build; "
                        "an incremental build keeps its recorded route and the control has no overlay")
    if not lb.doctor_report()["ok"]:
        raise LaneError("doctor found prerequisites missing; run `build.py doctor` for details")
    paths = _paths(name)
    started = time.monotonic()
    phase_seconds: dict[str, float] = {}
    with host_lease("build", f"build {_tag(name)}", timings=phase_seconds):
        # The report is the stage's identity. It never describes a stage
        # that is being rebuilt, so `bench` cannot pair new bytes with an
        # old report. A failed incremental build keeps its configured tree.
        configured = False
        route: CompressionRoute = "upstream" if upstream_compression else "private"
        if incremental:
            previous = _read_report(paths["report"])
            if not previous.get("configured") or previous.get("pristine"):
                raise LaneError(f"no configured candidate build named {name!r}; run a clean build")
            configured = True
            route = _report_route(previous)
        _write_json(paths["report"], {"status": "building", "name": _tag(name),
                                      "pristine": empty_overlay, "configured": configured,
                                      "compression_route": route})
        state = {"configured": configured}
        if incremental:
            state["previous"] = previous
        try:
            report = _build_locked(name, paths, empty_overlay=empty_overlay, jobs=jobs,
                                   incremental=incremental, state=state, route=route)
        except BaseException as error:
            _write_json(paths["report"], {"status": "failed", "name": _tag(name),
                                          "pristine": empty_overlay, "configured": state["configured"],
                                          "compression_route": route, "error": str(error)})
            raise
        report["build_seconds"] = round(time.monotonic() - started, 1)
        report["phase_seconds"] = phase_seconds
        _write_json(paths["report"], report)
    kind = "incremental" if incremental else "clean"
    print(f"OK    perf CPython {report['interpreter']['version'].split()[0]} ({kind}, "
          f"{len(report['rust_extensions_sha256'])} release Rust extensions verified, "
          f"{report['build_seconds']:.0f}s) -> {paths['stage']}")
    return 0


def _build_locked(name: str, paths: dict[str, Path], *, empty_overlay: bool, jobs: int | None,
                  incremental: bool, state: dict[str, Any],
                  route: CompressionRoute = "private") -> dict[str, Any]:
    toolchain, target = lb._toolchain()
    lb._llvm_ready(toolchain)
    git_state = _git_state()
    changes: dict[str, list[str]] | None = None
    placement: dict[str, tuple[str, ...]] = {}
    placement_modes: dict[str, bool] = {}
    placement_original: bytes | None = None
    if incremental:
        source, overlay, files, staged, current, changes = _plan_overlay_sync(paths, route)
        setup = "Modules/Setup.stdlib.in"
        if changes["changed"] == [setup] and not changes["removed"]:
            placement_original = _original_setup_template()
            before = current[setup]
            if before is None:
                raise LaneError("previous standard-module template is missing")
            placement = c_placement_plan(before, staged[setup], placement_original)
            _, wrapped = _c_template_modes(staged[setup], placement_original)
            status = (paths["build"] / "config.status").read_text()
            if re.search(r'S\["MODULE_BUILDTYPE"\]="shared"', status) is None:
                raise LaneError("C placement requires the original configured shared mode")
            for owner in (source / 'Modules/Setup', paths['build'] / 'Modules/Setup.local',
                          paths['build'] / 'Modules/Setup.bootstrap'):
                if owner.is_file():
                    moved_objects = {obj for objects in placement.values() for obj in objects}
                    for line in owner.read_text().splitlines():
                        tokens = line.split('#', 1)[0].split()
                        if tokens and tokens[0] in placement:
                            raise LaneError("C placement row is shadowed by another Setup owner")
                        objects = {'Modules/' + str(Path(token).with_suffix('.o'))
                                   for token in tokens[1:] if token.endswith('.c')}
                        if objects & moved_objects:
                            raise LaneError("C placement object is shared with another Setup owner")
            previous = state.get("previous", {})
            metadata, entry = lb._read_lock()
            if (previous.get("source_commit") != metadata["commit"]
                    or previous.get("source_archive_sha256") != entry.sha256
                    or previous.get("target") != lb.TARGET
                    or previous.get("cargo_lock_sha256") != _sha256_file(source / 'Cargo.lock')):
                raise LaneError("C placement requires unchanged prior source, target and Cargo lock")
            flags = _perf_flags(toolchain, target, stage=paths["stage"])
            for flag in ('CFLAGS', 'CPPFLAGS', 'LDFLAGS'):
                if lb._make_value(paths['build'] / 'Makefile', 'CONFIGURE_' + flag) != flags[flag]:
                    raise LaneError("C placement requires unchanged configured compiler flags")
            if lb._make_value(paths['build'] / 'Makefile', 'CC') != str(toolchain.llvm_prefix / 'bin/clang'):
                raise LaneError("C placement requires unchanged compiler owner")
            placement_modes = {name: name in wrapped for name in placement}
            changes["clean_only"] = []
        _refuse_unsyncable(changes)
        if placement:
            # A failed placement migration must not be retried as an ordinary
            # source-only sync after its template has already been copied.
            state["configured"] = False
            # Process loss can bypass the exception handler. Persist the refusal
            # before copying the template, while validated prior inputs remain
            # available only to this already-running migration.
            _write_json(paths["report"], {"status": "building", "name": _tag(name),
                                          "pristine": False, "configured": False})
        _copy_changed_overlay(paths, source, changes)
    else:
        source = lb._extract_fresh(paths["source_parent"])
        if empty_overlay:
            overlay, files = {"files": 0, "sha256": None, "pristine": True}, []
        else:
            overlay, files = _stage_overlay(paths, route)
            for item in files:
                _copy_file(paths["overlay_staging"] / item, source / item)
    _write_json(paths["overlay_manifest"], files)
    if not empty_overlay and not lb.IS_LINUX:
        proof, object_stores = _builtin_artifact_verifier()
        try:
            proof.validate_builtin_source(source, lb.TARGET, object_stores)
        except proof.BuiltinArtifactError as error:
            raise LaneError(f"built-in Rust source preflight failed: {error}") from error
    lb._require_command(
        [str(lb.CARGO_HOME / "bin" / "cargo"), "fetch", "--locked", "--offline",
         "--manifest-path", str(source / "Cargo.toml")],
        cwd=source, env=lb._environment(toolchain, offline=True, build_dir=paths["build"]),
        log=paths["cargo_log"],
    )
    workers = _worker_count(jobs)
    sandbox = lb._sealed_sandbox()
    env = _build_environment(toolchain, target, paths, workers=workers, sandbox=sandbox)
    configure = [str(source / "configure"), f"--prefix={paths['stage']}", "--enable-shared",
                 "--enable-experimental-jit=no", "--with-tail-call-interp=no",
                 "--without-ensurepip"]
    # Only the aggregate workspace requires the pinned source-built runtime.
    # Locked Cargo fetch has already validated every declared workspace dependency.
    source_aggregate = (not empty_overlay and lb.TARGET == "aarch64-apple-darwin"
                        and (source / "Modules/cpython-rust-source-aggregate356/Cargo.toml").is_file())
    std_metadata = None
    engine_environment: dict[str, str] | None = None
    if not incremental:
        for path in (paths["build"], paths["stage"]):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True)
        if source_aggregate:
            std_metadata = _prepare_csv_source_std(paths, env)
        if route == "upstream":
            engine_environment = lb.compression_engine_environment(
                source, env, target_dir=paths["build"] / "compression-target", profile="release",
                log=paths["compression_log"], sealed=sandbox)
            env.update(engine_environment)
        if lb.IS_LINUX:
            env.update(lb._build_mpdecimal(toolchain, target, workers, sandbox))
        lb._require_command(configure, cwd=paths["build"], env=env,
                            log=paths["configure_log"], sealed=sandbox)
        setup_local = source / "Modules" / "Setup.local"
        if setup_local.is_file():
            contents = setup_local.read_text()
            if lb.IS_LINUX:
                contents = lb.lane_linux.setup_local_with_shared_ssl_rs(contents)
            setup_target = paths["build"] / "Modules" / "Setup.local"
            setup_target.parent.mkdir(parents=True, exist_ok=True)
            setup_target.write_text(contents)
            setup_target.chmod(setup_local.stat().st_mode & 0o777)
        state["configured"] = True
    if placement:
        # The existing generators own module registration and linker recipes.
        # Explicit regeneration happens before deleting any compiled owner.
        lb._require_command(['./config.status', 'Modules/Setup.stdlib'],
                            cwd=paths['build'], env=env, log=paths['configure_log'], sealed=sandbox)
        lb._require_command([str(toolchain.make), 'Makefile', *MAKE_VARS],
                            cwd=paths['build'], env=env, log=paths['build_log'], sealed=sandbox)
        _verify_c_placement_rules(paths['build'], placement, placement_modes)
        invalidate_c_placement(paths['build'], paths['stage'], placement)
    if incremental or std_metadata is not None:
        if incremental and source_aggregate:
            std_metadata = _prepare_csv_source_std(paths, env)
        # Install depends on the complete build. A single invocation preserves
        # the source runtime's pinned libpython bytes through publication; its
        # current transcript proves both compilation and installation.
        paths["build_log"].unlink(missing_ok=True)
        lb._require_command([str(toolchain.make), f"-j{workers}", "install", *MAKE_VARS],
                            cwd=paths["build"], env=env, log=paths["install_log"], sealed=sandbox)
        _check_logs(paths["install_log"], paths["install_log"])
    else:
        # Pristine and non-source-runtime clean builds keep their two-step recipe.
        lb._require_command([str(toolchain.make), f"-j{workers}", *MAKE_VARS], cwd=paths["build"],
                            env=env, log=paths["build_log"], sealed=sandbox)
        lb._require_command([str(toolchain.make), "install", *MAKE_VARS], cwd=paths["build"],
                            env=env, log=paths["install_log"], sealed=sandbox)
        _check_logs(paths["build_log"], paths["install_log"])
    if placement:
        verify_c_placement_compilation(paths['install_log'], placement, placement_modes)
        _verify_c_placement_rules(paths['build'], placement, placement_modes)
    python = paths["stage"] / "bin" / "python3.16"
    if not python.is_file():
        raise LaneError(f"perf install did not produce {python}")
    cargo_default = lb._make_value(paths["build"] / "Makefile", "CARGO_PROFILE")
    if cargo_default != "dev":
        raise LaneError(f"unexpected configured Cargo default {cargo_default!r}; expected 'dev'")
    members = set(lb._workspace_members(source, env))
    required = {"_base64", "cpython-sys"} | (set() if empty_overlay else _overlay_members(route))
    if not required <= members:
        raise LaneError("perf workspace is missing members: "
                        + ", ".join(sorted(required - members)))
    if not incremental:
        compile_log = paths["install_log"] if std_metadata is not None else paths["build_log"]
        missing = required - set(_built_members(compile_log, members))
        if missing:
            raise LaneError("perf build did not compile Rust members: " + ", ".join(sorted(missing)))
    rust_source_aggregate = (_install_source_aggregate(source, paths, std_metadata)
                       if std_metadata is not None else None)
    rust_extensions = verify_release_artifacts(paths["build"], paths["stage"], members)
    if route == "upstream":
        verify_upstream_compression(paths["stage"], toolchain)
    rust_builtins = {}
    if not empty_overlay:
        proof, object_stores = _builtin_artifact_verifier()
        try:
            rust_builtins = proof.verify_builtin_artifacts(
                source, paths["build"], paths["stage"], lb.TARGET, object_stores)
        except proof.BuiltinArtifactError as error:
            raise LaneError(f"built-in Rust artifact proof failed: {error}") from error
    metadata, source_input = lb._read_lock()
    report = {
        "status": "built",
        "configured": True,
        "build_mode": "perf-no-pgo-no-lto",
        "compression_route": route,
        "compression_engine_environment": (
            state.get("previous", {}).get("compression_engine_environment") if incremental
            else engine_environment),
        "name": _tag(name),
        "pristine": empty_overlay,
        "incremental": incremental,
        "incremental_changes": changes["changed"] if changes else None,
        "c_placement": ({"objects": placement, "builtin": placement_modes,
                         "original_template_sha256": hashlib.sha256(placement_original).hexdigest(),
                         "generated_makefile_sha256": _sha256_file(paths['build'] / 'Makefile'),
                         "generated_config_sha256": _sha256_file(paths['build'] / 'Modules/config.c')}
                        if placement else None),
        "git": git_state,
        "target": lb.TARGET,
        "source_commit": metadata["commit"],
        "source_archive_sha256": source_input.sha256,
        "overlay": overlay,
        "cargo_lock_sha256": hashlib.sha256((source / "Cargo.lock").read_bytes()).hexdigest(),
        "configure_arguments": configure,
        "make_variables": list(MAKE_VARS),
        "cflags": env["CFLAGS"],
        "cargo_profile": "release",
        "cargo_profile_selected_by": "make command-line override on build and install "
            "(configure defaults to dev without its PGO flag)",
        "rust_extensions_sha256": rust_extensions,
        "rust_builtin_artifacts": rust_builtins,
        "rust_source_aggregate": rust_source_aggregate,
        "pgo": False,
        "lto": False,
        "interpreter": lb._module_report(paths["stage"], python, toolchain),
        "build_interpreter": str(_build_python(paths["build"])),
        "rust": lb._rust_identity(shutil.which("rustup")),
        "c_toolchain": toolchain.identity(),
        "stage": str(paths["stage"]),
        "stage_identity": tree_digest(paths["stage"]),
    }
    # Metadata and stage verification can still fail after installation. Only
    # the fully constructed report permits reuse of a migrated configured tree.
    state["configured"] = True
    return report


# -------------------------------------------------------------------- test


def test(*, name: str, suites: list[str], all_suites: bool = False,
         jobs: int | None = None) -> int:
    # Module focus (--suite test_zlib) and full-suite (--all) correctness
    # checks for a perf build. Suites run unsealed like the coverage
    # runner: measurement isolation belongs to the benchmark passes.
    _check_name(name)
    if all_suites == bool(suites):
        raise LaneError("select --all or at least one --suite, but not both")
    if any(re.fullmatch(r"test_[a-z0-9_]+", suite) is None for suite in suites):
        raise LaneError("suite names must be CPython test modules or packages named test_*")
    if jobs is not None and jobs < 1:
        raise LaneError("test jobs must be positive")
    paths = _paths(name)
    report = _read_report(paths["report"])
    if report.get("status") not in COMPLETED:
        raise LaneError(f"perf report does not describe a completed build: {paths['report']}")
    build_python = _build_python(paths["build"])
    if not build_python.is_file():
        raise LaneError(f"perf build interpreter is missing: {build_python}")
    toolchain, _target = lb._toolchain()
    env = lb._test_environment(toolchain)
    scope = "all" if all_suites else "suite"
    label = "all" if all_suites else "-".join(suites)
    if len(label) > 160:
        label = f"{'-'.join(suites[:3])}-{hashlib.sha256(label.encode()).hexdigest()[:16]}"
    log = lb.LOGS / f"{_tag(name)}-{label}.log"
    workers = jobs if jobs is not None else lb._test_jobs()
    with host_lease("test", f"test {_tag(name)} {label}"):
        result = lb._run_python_test(
            [str(build_python), "-m", "test", "-j", str(workers), "--timeout=900", *suites],
            cwd=paths["build"], env=env, log=log,
        )
    report["tests"] = {"scope": scope, "suites": suites, "git": _git_state(), **result}
    report["status"] = f"{scope}-passed" if result["returncode"] == 0 else f"{scope}-failed"
    _write_json(paths["report"], report)
    if result["returncode"] != 0:
        raise LaneError(f"CPython suite failed; inspect {log}")
    print("OK    default-resource CPython suite" if all_suites
          else f"OK    CPython suites: {', '.join(suites)}")
    return 0


# ------------------------------------------------------------------- bench


@contextlib.contextmanager
def _runtime_prefix_aliases(baseline: Path, candidate: Path) -> Iterator[tuple[Path, Path]]:
    """Give both unchanged installation trees equal-length visible home paths."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rust-memory-home-", dir=SCRATCH) as temporary:
        root = Path(temporary)
        homes = (root / "baseline", root / "subject0")
        for home, stage in zip(homes, (baseline, candidate)):
            if not stage.is_dir():
                raise LaneError(f"interpreter installation is missing: {stage}")
            home.symlink_to(stage.resolve(), target_is_directory=True)
        yield homes


def resolve(ref: str) -> dict[str, Any]:
    """Resolve `@control`, `@incumbent`, or a local build name to its stage."""
    if ref in ALIASES:
        lane = _primary_repo() / "rust-cpython"
        name = ALIASES[ref]
    elif ref.startswith("@"):
        raise LaneError(f"unknown reference {ref!r}; use @control, @incumbent, or a build name")
    else:
        lane = LANE
        name = _check_name(ref)
    paths = _paths(name, lane)
    report = _read_report(paths["report"])
    if report.get("status") not in COMPLETED:
        raise LaneError(f"{ref}: build report is not a completed build ({report.get('status')})")
    if report.get("cargo_profile") != "release" or "stage_identity" not in report:
        raise LaneError(f"{ref}: build predates release-profile verification; rebuild it")
    python = paths["stage"] / "bin" / "python3.16"
    if not python.is_file():
        raise LaneError(f"{ref}: installed interpreter is missing: {python}")
    return {"ref": ref, "name": _tag(name), "lane": lane, "stage": paths["stage"],
            "python": python, "report": report}


def _verify_stage(side: dict[str, Any]) -> None:
    found = tree_digest(side["stage"])
    if found["sha256"] != side["report"]["stage_identity"]["sha256"]:
        raise LaneError(f"{side['ref']}: stage changed after its build ({side['stage']}); rebuild it")


def _label(side: dict[str, Any]) -> str:
    report = side["report"]
    commit = report.get("git", {}).get("commit", "unknown")[:10]
    flags = []
    if report.get("incremental"):
        flags.append("incremental")
    if report.get("compression_route") == "upstream":
        flags.append("upstream-compression")
    if report.get("git", {}).get("overlay_dirty"):
        flags.append("dirty")
    suffix = f" ({', '.join(flags)})" if flags else ""
    return f"{side['name']}@{commit}{suffix}"


def _gate_checks(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    for side in (baseline, candidate):
        report = side["report"]
        if report.get("incremental"):
            raise LaneError(f"{side['ref']}: gate evidence needs a clean build, not incremental")
        if report.get("git", {}).get("overlay_dirty"):
            raise LaneError(f"{side['ref']}: gate evidence needs a committed overlay; commit, then rebuild")
    if baseline["ref"] == "@incumbent" and candidate["ref"] != "@incumbent":
        incumbent = baseline["report"]["git"]["commit"]
        challenger = candidate["report"]["git"]["commit"]
        result = subprocess.run(["git", "merge-base", "--is-ancestor", incumbent, challenger],
                                cwd=LANE, capture_output=True, check=False)
        if result.returncode != 0:
            raise LaneError(f"challenger {challenger[:10]} does not contain incumbent {incumbent[:10]}; "
                            "rebase on the incumbent commit and rebuild")


def eligible_workloads() -> list[str]:
    from benchmarks.workloads.registry import WORKLOADS

    return [item.name for item in WORKLOADS if item.name not in UNAVAILABLE_WORKLOADS]


def module_routes() -> list[str]:
    import perf_modules

    return list(perf_modules.KERNELS)


def selection(*, workloads: list[str], modules: list[str], gate: bool, all_workloads: bool = False,
              all_modules: bool = False) -> tuple[list[str], list[str], list[str]]:
    """Return (targets, evaluated workloads, evaluated modules)."""
    from benchmarks.workloads.registry import BY_NAME

    workloads = [*workloads, *(eligible_workloads() if all_workloads else ())]
    modules = [*modules, *(module_routes() if all_modules else ())]
    for name in workloads:
        if name not in BY_NAME:
            raise LaneError(f"unknown workload {name!r}")
        if name in UNAVAILABLE_WORKLOADS:
            raise LaneError(f"{name} needs inputs this lane does not lock yet")
    known = set(module_routes())
    for name in modules:
        if name not in known:
            raise LaneError(f"unknown module route {name!r}; see `perf.py modules`")
    targets = list(dict.fromkeys([*modules, *workloads]))
    evaluated_workloads = list(dict.fromkeys([*workloads, *(GATE_WORKLOADS if gate else ())]))
    evaluated_modules = list(dict.fromkeys(modules))
    if not evaluated_workloads and not evaluated_modules:
        raise LaneError("select --module M, --workload W (both repeatable), --gate, or --all-*")
    return targets or list(GATE_WORKLOADS), evaluated_workloads, evaluated_modules


def _run_bench(baseline: dict[str, Any], candidate: dict[str, Any], workload: str, *,
               output: Path, profile: str, timing_only: bool, self_compare: bool,
               record_baseline: Path | None, memory_only: bool = False) -> dict[str, Any] | None:
    """One bench.py run of one workload; None when the outputs differ."""
    # Workload evidence supports standard sampling at most; rigorous adds
    # module rounds while retaining the complete standard workload checks.
    workload_profile = "standard" if profile == "rigorous" else profile
    command = [
        sys.executable, str(REPO / "benchmarks" / "bench.py"), "run", "--local",
        "--baseline", str(baseline["python"]), "--candidate", str(candidate["python"]),
        "--baseline-label", _label(baseline), "--candidate-label", _label(candidate),
        "--baseline-kind", "self" if self_compare else "custom",
        "--candidate-kind", "self" if self_compare else "custom",
        "--suite", "realworld", "--profile", workload_profile, "--workload", workload,
        "--output", str(output), "--evidence", str(output.with_suffix(".evidence.json")),
    ]
    homes = (baseline.get("runtime_home"), candidate.get("runtime_home"))
    if any(home is not None for home in homes):
        if any(home is None for home in homes) or not memory_only:
            raise LaneError("runtime home aliases require both sides and memory-only sampling")
        command += ["--baseline-python-home", str(homes[0]),
                    "--candidate-python-home", str(homes[1])]
    if baseline.get("runtime_executable") or candidate.get("runtime_executable"):
        if not all(side.get("runtime_executable") for side in (baseline, candidate)):
            raise LaneError("launch aliases require both sides")
        command.append("--launch-through-python-home")
    if memory_only:
        command.append("--memory-only")
    if timing_only:
        command.append("--timing-only")
    if record_baseline is not None:
        command += ["--record-baseline", str(record_baseline)]
    log = output.with_suffix(".log")
    output.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        if "correctness digest differs" in log.read_text(errors="replace"):
            return None
        raise LaneError(f"bench.py failed for {workload}; inspect {log}")
    summary = json.loads((output / "summary.json").read_text())
    matches = [item for item in summary.get("workloads", [])
               if item.get("identity", {}).get("name") == workload]
    if len(matches) != 1:
        raise LaneError(f"bench summary lacks exactly one {workload} result: {output}")
    return matches[0]


def _module_sample(python: Path, route: str, iterations: int, scratch: Path, *,
                   runtime_home: Path | None = None,
                   runtime_executable: Path | None = None) -> dict[str, Any]:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()), "TMPDIR": str(scratch),
           "PYTHONHASHSEED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if runtime_home is not None:
        env["PYTHONHOME"] = str(runtime_home)
    launch = python
    if runtime_executable is not None:
        if runtime_home is None or not runtime_home.is_absolute() or \
                runtime_executable != runtime_home / "bin" / python.name or \
                not runtime_home.is_dir() or runtime_home.resolve() != python.parent.parent.resolve() or \
                not runtime_executable.is_file() or \
                runtime_executable.resolve() != python.resolve():
            raise LaneError("launch alias does not select the verified executable")
        launch = runtime_executable
    result = subprocess.run([str(launch), "-s", "-P", str(LANE / "perf_modules.py"), "measure", route,
                             "--iterations", str(iterations)],
                            cwd=scratch, env=env, capture_output=True, text=True, timeout=900, check=False)
    if result.returncode != 0:
        tail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise LaneError(f"module kernel {route} failed under {python}: {tail}")
    observation = json.loads(result.stdout.strip().splitlines()[-1])
    if runtime_executable is not None and (
            any(observation.get(key) != str(launch) for key in
                ("runtime_executable", "runtime_base_executable")) or
            any(observation.get(key) != str(runtime_home) for key in
                ("runtime_prefix", "runtime_exec_prefix"))):
        raise LaneError("module interpreter did not honor the requested launch and home")
    return observation


def _module_iterations(python: Path, route: str, scratch: Path, *,
                       runtime_home: Path | None = None,
                       runtime_executable: Path | None = None) -> int:
    # Size each kernel so the baseline's measured loop lasts about
    # MODULE_TARGET_SECONDS of CPU; both sides then run the same count.
    options = {"runtime_home": runtime_home}
    if runtime_executable is not None:
        options["runtime_executable"] = runtime_executable
    sample = _module_sample(python, route, 1, scratch, **options)
    per_iteration = max(float(sample["cpu_seconds_per_iteration"]), 1e-6)
    return max(1, min(MODULE_MAX_ITERATIONS, round(MODULE_TARGET_SECONDS / per_iteration)))


def _measure_module(baseline: dict[str, Any], candidate: dict[str, Any], route: str, *,
                    iterations: int, rounds: int, scratch: Path, memory_only: bool = False) -> dict[str, Any]:
    samples: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    sides = {"baseline": baseline, "candidate": candidate}
    for index in range(rounds):
        # Alternate which side runs first so neither is always the cold one.
        for side in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
            options = {"runtime_home": sides[side].get("runtime_home")}
            if sides[side].get("runtime_executable") is not None:
                options["runtime_executable"] = sides[side]["runtime_executable"]
            samples[side].append(_module_sample(sides[side]["python"], route, iterations, scratch,
                                                **options))
    observation = perf_verdict.module_observation(samples["baseline"], samples["candidate"],
                                                  memory_only=memory_only)
    observation["samples"] = samples
    return observation


def _measure(*, baseline_ref: str, candidate_ref: str, workloads: list[str], modules: list[str],
             gate: bool, runs: int, profile: str, timing_only: bool, min_idle: float,
             self_compare: bool, record_baselines: bool, slug_prefix: str = "",
             memory_only: bool = False, matched_prefix: bool = False,
             matched_executable: bool = False) -> dict[str, Any]:
    if matched_prefix and not memory_only:
        raise LaneError("--matched-prefix requires --memory-only")
    if matched_executable and not matched_prefix:
        raise LaneError("--matched-executable requires --matched-prefix")
    targets, evaluated_workloads, evaluated_modules = selection(
        workloads=workloads, modules=modules, gate=gate)
    samples: list[dict[str, Any]] = []
    observations: dict[str, list[dict[str, Any]]] = {
        name: [] for name in [*evaluated_workloads, *evaluated_modules]}
    iterations: dict[str, int] = {}
    rounds = PROFILE_MODULE_ROUNDS[profile]
    early_rejection = (memory_only and not gate and not self_compare and not record_baselines
                       and not slug_prefix and runs >= 2)
    stopped_after: dict[str, Any] | None = None

    def replicated_regression(name: str) -> bool:
        # Only completed replication can make the remaining samples unnecessary.
        # All survivor and acceptance paths still collect their entire selection.
        if not early_rejection or len(observations[name]) != runs:
            return False
        entity = perf_verdict.entity_verdict(observations[name])
        decision = perf_verdict.decide({name: entity}, targets=[name], runs=runs,
                                       quiet=None, gate=False, memory_only=True)
        return bool(decision["regressions"])

    started = time.monotonic()
    label = f"calibrate {baseline_ref}" if self_compare else f"{baseline_ref} vs {candidate_ref}"
    harness_identity = _harness_identity()
    # These intervals are disjoint. Workload subprocess durations include their
    # internal preparation and sampling, which this controller cannot separate.
    # Existing total seconds also cover host checks, receipt work and cleanup.
    phase_seconds: dict[str, float] = {"workload_runs": 0.0, "module_sampling": 0.0}
    with host_lease("measure", f"bench {label}", timings=phase_seconds), contextlib.ExitStack() as contexts:
        if _harness_identity()["source_sha256"] != harness_identity["source_sha256"]:
            raise LaneError("measurement harness changed while waiting for the host lease")
        # Resolve and verify only under the exclusive lease: no build can
        # replace a stage between this check and the last measurement.
        baseline = resolve(baseline_ref)
        candidate = baseline if self_compare else resolve(candidate_ref)
        if not self_compare and baseline["stage"] == candidate["stage"]:
            raise LaneError("baseline and candidate are the same stage; use `perf.py calibrate`")
        if gate:
            _gate_checks(baseline, candidate)
        phase_started = time.monotonic()
        for side in {id(baseline): baseline, id(candidate): candidate}.values():
            _verify_stage(side)
        phase_seconds["stage_verification_before"] = time.monotonic() - phase_started
        phase_started = time.monotonic()
        prefix_context: dict[str, Any] = {"kind": "natural"}
        if matched_prefix:
            homes = contexts.enter_context(_runtime_prefix_aliases(baseline["stage"], candidate["stage"]))
            baseline = {**baseline, "runtime_home": homes[0]}
            candidate = {**candidate, "runtime_home": homes[1]}
            prefix_context = {"kind": "matched-python-home", "baseline_home": str(homes[0]),
                              "candidate_home": str(homes[1]), "path_length": len(str(homes[0]))}
            if matched_executable:
                launches = [home / side["python"].relative_to(side["stage"])
                            for home, side in zip(homes, (baseline, candidate))]
                if len(str(launches[0])) != len(str(launches[1])) or any(
                        not launch.is_file() or launch.resolve() != side["python"].resolve()
                        for launch, side in zip(launches, (baseline, candidate))):
                    raise LaneError("launch aliases require equal-length verified executable paths")
                baseline = {**baseline, "runtime_executable": launches[0]}
                candidate = {**candidate, "runtime_executable": launches[1]}
                prefix_context.update(kind="matched-python-home-and-executable",
                                      baseline_executable=str(launches[0]),
                                      candidate_executable=str(launches[1]),
                                      executable_path_length=len(str(launches[0])))
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        slug = ("calibrate-" + baseline["name"] if self_compare
                else f"{slug_prefix}{baseline['name']}-vs-{candidate['name']}")
        directory = LANE / "results" / "perf-bench" / f"{stamp}-{slug}"
        scratch = directory / "tmp"
        scratch.mkdir(parents=True)
        contexts.callback(shutil.rmtree, scratch, ignore_errors=True)
        phase_seconds["controller_preparation"] = time.monotonic() - phase_started
        # Memory-only acceptance retains the same kernels and iteration
        # calibration, but host CPU idle and power state cannot delay it.
        if not memory_only:
            samples.append(_host_sample(min_idle, wait_seconds=QUIET_WAIT_SECONDS))
        if samples and not samples[-1]["quiet"]:
            print(f"WARN  host not quiet before measuring ({samples[-1]['cpu_idle_percent']}% idle)",
                  flush=True)
        # Finish workload replication before spending on module calibration in
        # exploratory memory comparisons. Protected modes retain interleaved runs.
        sampling_passes = ([(evaluated_workloads, []), ([], evaluated_modules)]
                           if early_rejection else [(evaluated_workloads, evaluated_modules)])
        phase_seconds["module_calibration"] = 0.0
        for pass_workloads, pass_modules in sampling_passes:
            if pass_modules or not early_rejection:
                phase_started = time.monotonic()
                for route in pass_modules:
                    options = {"runtime_home": baseline.get("runtime_home")}
                    if baseline.get("runtime_executable") is not None:
                        options["runtime_executable"] = baseline["runtime_executable"]
                    iterations[route] = _module_iterations(baseline["python"], route, scratch, **options)
                phase_seconds["module_calibration"] += time.monotonic() - phase_started
            for run in range(1, runs + 1):
                for workload in pass_workloads:
                    print(f"RUN   [{run}/{runs}] workload {workload}", flush=True)
                    record = None
                    if record_baselines and run == runs:
                        record = REPO / "benchmarks" / "baselines" / f"rust-cp316-perf-{workload}.json"
                    phase_started = time.monotonic()
                    summary = _run_bench(baseline, candidate, workload,
                                         output=directory / f"run-{run}" / workload, profile=profile,
                                         timing_only=timing_only, self_compare=self_compare,
                                         record_baseline=record, memory_only=memory_only)
                    phase_seconds["workload_runs"] += time.monotonic() - phase_started
                    observations[workload].append(perf_verdict.mismatch_observation("workload")
                                                  if summary is None else perf_verdict.run_observation(
                                                      summary, memory_only=memory_only))
                    if replicated_regression(workload):
                        stopped_after = {"run": run, "entity": workload}
                        break
                if stopped_after is not None:
                    break
                for route in pass_modules:
                    print(f"RUN   [{run}/{runs}] module {route} ({iterations[route]} iterations x {rounds} rounds)",
                          flush=True)
                    phase_started = time.monotonic()
                    observation = _measure_module(baseline, candidate, route,
                                                  iterations=iterations[route], rounds=rounds,
                                                  scratch=scratch, memory_only=memory_only)
                    phase_seconds["module_sampling"] += time.monotonic() - phase_started
                    observations[route].append(observation)
                    if replicated_regression(route):
                        stopped_after = {"run": run, "entity": route}
                        break
                if stopped_after is not None:
                    break
                if not memory_only:
                    samples.append(_host_sample(min_idle))
            if stopped_after is not None:
                break
        try:
            phase_started = time.monotonic()
            for side in {id(baseline): baseline, id(candidate): candidate}.values():
                _verify_stage(side)
            phase_seconds["stage_verification_after"] = time.monotonic() - phase_started
            if _harness_identity()["source_sha256"] != harness_identity["source_sha256"]:
                raise LaneError("measurement harness changed during sampling")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    quiet = all(sample["quiet"] for sample in samples) if samples else None
    incomplete_entities = [name for name, records in observations.items() if len(records) != runs]
    entities = {name: perf_verdict.entity_verdict(records)
                for name, records in observations.items() if name not in incomplete_entities}
    # A single output mismatch remains decisive even when that entity's memory
    # sampling is incomplete. Do not turn its partial metrics into replicated claims.
    decision_entities = dict(entities)
    for name in incomplete_entities:
        if any(record["mismatch"] for record in observations[name]):
            decision_entities[name] = perf_verdict.entity_verdict(
                [perf_verdict.mismatch_observation(observations[name][0]["kind"])])
    known = sorted(KNOWN_CONTROL_MISMATCHES) if baseline["ref"] == "@control" else []
    if self_compare:
        decision = perf_verdict.calibration(entities, runs=runs, quiet=quiet, gate=gate,
                                            memory_only=memory_only)
    else:
        decision = perf_verdict.decide(decision_entities, targets=targets, runs=runs, quiet=quiet, gate=gate,
                                       known_mismatches=known, memory_only=memory_only)
    goals = ({name: perf_verdict.goal_status(entities[name], memory_only=memory_only)
              for name in evaluated_modules if name in entities}
             if baseline["ref"] == "@control" and not self_compare else {})
    record = {
        "baseline": {"ref": baseline["ref"], "label": _label(baseline), "stage": str(baseline["stage"])},
        "candidate": {"ref": candidate["ref"], "label": _label(candidate), "stage": str(candidate["stage"])},
        "profile": profile,
        "workload_profile": "standard" if profile == "rigorous" else profile,
        "timing_only": timing_only,
        "acceptance_policy": "memory-only" if memory_only else "all-metrics",
        "runtime_prefix_context": prefix_context,
        "harness": harness_identity,
        "module_iterations": iterations,
        "module_rounds": rounds,
        "host_samples": samples,
        "entities": entities,
        "raw": observations,
        "sampling_complete": not incomplete_entities,
        "incomplete_entities": incomplete_entities,
        "stopped_after": stopped_after,
        "decision": decision,
        "goals": goals,
        "seconds": round(time.monotonic() - started, 1),
        "phase_seconds": phase_seconds,
        "directory": str(directory),
    }
    _write_json(directory / "verdict.json", record)
    return record


def bench(*, baseline_ref: str, candidate_ref: str, workloads: list[str], gate: bool,
          runs: int | None, profile: str, timing_only: bool, min_idle: float,
          modules: list[str] | None = None, all_workloads: bool = False, all_modules: bool = False,
          record_baselines: bool = False, self_compare: bool = False, memory_only: bool = False,
          matched_prefix: bool = False, matched_executable: bool = False) -> int:
    runs = runs if runs is not None else (2 if gate or memory_only else 1)
    if runs < 1:
        raise LaneError("runs must be positive")
    if memory_only and timing_only:
        raise LaneError("--memory-only requires the memory pass; drop --timing-only")
    if gate and timing_only:
        raise LaneError("gate runs include the memory pass; drop --timing-only")
    if record_baselines and not (gate and baseline_ref == "@control" and candidate_ref == "@incumbent"):
        raise LaneError("--record-baselines is for the coordinator's gated @control vs @incumbent run")
    workloads = [*workloads, *(eligible_workloads() if all_workloads else ())]
    modules = [*(modules or []), *(module_routes() if all_modules else ())]
    record = _measure(baseline_ref=baseline_ref, candidate_ref=candidate_ref, workloads=workloads,
                      modules=modules, gate=gate, runs=runs, profile=profile, timing_only=timing_only,
                      min_idle=min_idle, self_compare=self_compare, record_baselines=record_baselines,
                      memory_only=memory_only, matched_prefix=matched_prefix,
                      matched_executable=matched_executable)
    print(perf_verdict.render(record["entities"], record["decision"], record["goals"] or None))
    print(f"LOG   {record['directory']}/verdict.json")
    return 0


def goals(*, candidate_ref: str, modules: list[str], runs: int, profile: str, min_idle: float,
          memory_only: bool = False, matched_prefix: bool = False,
          matched_executable: bool = False) -> int:
    """Per-module goal status of a candidate against the pristine control.

    Memory-only goals require load footprint and working peak to satisfy
    the band; CPU measurements do not affect the overall status.
    """
    if runs < 2:
        raise LaneError("goal status needs at least two independent runs")
    record = _measure(baseline_ref="@control", candidate_ref=candidate_ref, workloads=[],
                      modules=modules or module_routes(), gate=False, runs=runs, profile=profile,
                      timing_only=False, min_idle=min_idle, self_compare=False, record_baselines=False,
                      slug_prefix="goals-", memory_only=memory_only, matched_prefix=matched_prefix,
                      matched_executable=matched_executable)
    print(perf_verdict.render_goals(record["goals"], record["entities"]))
    if not memory_only and not record["decision"]["quiet"]:
        print("WARN  host was not quiet: statuses may carry host noise; rerun before recording")
    print(f"LOG   {record['directory']}/verdict.json")
    return 0


# ----------------------------------------------------------------- profile

PSTATS_REPORT = """
import pstats, sys
stats = pstats.Stats(sys.argv[1])
stats.sort_stats("cumulative").print_stats(35)
stats.sort_stats("tottime").print_stats(25)
"""


def profile(*, ref: str, workload: str | None, module: str | None, tool: str, iterations: int | None,
            seconds: int) -> int:
    """Profile one workload or module kernel on one build: cProfile, `sample`, or importtime."""
    from benchmarks.harness.runner import workload_environment
    from benchmarks.workloads.registry import BY_NAME

    if (workload is None) == (module is None):
        raise LaneError("select exactly one of --workload or --module")
    if module is not None:
        if module not in module_routes():
            raise LaneError(f"unknown module route {module!r}; see `perf.py modules`")
        if tool == "importtime":
            raise LaneError("importtime applies to startup workloads; use cprofile or sample")
        return _profile_module(ref=ref, route=module, tool=tool, iterations=iterations, seconds=seconds)
    spec = BY_NAME.get(workload)
    if spec is None or workload in UNAVAILABLE_WORKLOADS or workload == "django_wsgi_first_request":
        raise LaneError(f"cannot profile {workload!r} standalone; pick another eligible workload")
    if workload == "python_startup":
        tool = "importtime"
    side = resolve(ref)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = LANE / "results" / "perf-profile" / f"{stamp}-{side['name']}-{workload}-{tool}"
    directory.mkdir(parents=True)
    python = str(side["python"])
    with host_lease("test", f"profile {ref} {workload}"), \
            tempfile.TemporaryDirectory(prefix="perf-profile-") as temp:
        site = None
        if spec.packages:
            from benchmarks.harness.inputs import (
                LINUX_CP316_LOCK_PATH,
                MACOS_CP316_LOCK_PATH,
                load_lock,
                prepare_site,
            )

            lock = LINUX_CP316_LOCK_PATH if lb.IS_LINUX else MACOS_CP316_LOCK_PATH
            prepared = prepare_site(side["python"], Path(temp) / "site", groups={"django"},
                                    lock=load_lock(lock))
            site = prepared.site_packages
            subprocess.run([python, "-m", "compileall", "-q", str(site)], check=True, timeout=300)
        env = workload_environment(site)
        target = ["-m", f"benchmarks.workloads.{spec.module}", workload,
                  "--iterations", str(iterations or spec.iterations)]
        report = directory / "report.txt"
        if tool == "importtime":
            import_target = target
            if workload == "python_startup":
                import_target = ["-c", "pass"]
            elif workload == "import_django":
                import_target = ["-c", "import django"]
            with report.open("w") as stream:
                subprocess.run([python, "-X", "importtime", *import_target],
                               cwd=REPO, env=env, stderr=stream,
                               stdout=subprocess.DEVNULL, check=True)
            lines = report.read_text().splitlines()[1:]
            rows = sorted(lines, key=lambda line: -int(line.split("|")[1]) if "|" in line else 0)
            print("\n".join(["import time: self [us] | cumulative | imported package", *rows[:35]]))
        elif tool == "cprofile":
            data = directory / "profile.pstats"
            subprocess.run([python, "-m", "cProfile", "-o", str(data), *target],
                           cwd=REPO, env=env, stdout=subprocess.DEVNULL, check=True)
            with report.open("w") as stream:
                subprocess.run([python, "-c", PSTATS_REPORT, str(data)], cwd=REPO, env=env,
                               stdout=stream, check=True)
            print(report.read_text())
        else:
            process = subprocess.Popen([python, *target], cwd=REPO, env=env,
                                       stdout=subprocess.DEVNULL)
            try:
                subprocess.run(["/usr/bin/sample", str(process.pid), str(seconds), "-file", str(report)],
                               capture_output=True, check=True)
            finally:
                process.wait()
            _print_sample(report)
    print(f"LOG   {report}")
    return 0


def _print_sample(report: Path) -> None:
    text = report.read_text(errors="replace")
    marker = text.find("Sort by top of stack")
    print(text[marker:].split("\n\nBinary Images")[0][:6000] if marker >= 0 else text[:6000])


def _profile_module(*, ref: str, route: str, tool: str, iterations: int | None, seconds: int) -> int:
    side = resolve(ref)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = route.replace(".", "_")
    directory = LANE / "results" / "perf-profile" / f"{stamp}-{side['name']}-module-{slug}-{tool}"
    directory.mkdir(parents=True)
    scratch = directory / "tmp"
    scratch.mkdir()
    python = str(side["python"])
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()), "TMPDIR": str(scratch),
           "PYTHONHASHSEED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    report = directory / "report.txt"
    with host_lease("test", f"profile {ref} module {route}"):
        count = iterations or max(1, round(seconds / max(
            float(_module_sample(side["python"], route, 1, scratch)["cpu_seconds_per_iteration"]), 1e-6)))
        kernel = [str(LANE / "perf_modules.py"), "measure", route, "--iterations", str(count)]
        if tool == "cprofile":
            data = directory / "profile.pstats"
            subprocess.run([python, "-s", "-P", "-m", "cProfile", "-o", str(data), *kernel],
                           cwd=scratch, env=env, stdout=subprocess.DEVNULL, check=True)
            with report.open("w") as stream:
                subprocess.run([python, "-c", PSTATS_REPORT, str(data)], cwd=scratch, env=env,
                               stdout=stream, check=True)
            print(report.read_text())
        else:
            process = subprocess.Popen([python, "-s", "-P", *kernel], cwd=scratch, env=env,
                                       stdout=subprocess.DEVNULL)
            try:
                subprocess.run(["/usr/bin/sample", str(process.pid), str(seconds), "-file", str(report)],
                               capture_output=True, check=True)
            finally:
                process.wait()
            _print_sample(report)
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"LOG   {report}")
    return 0


# ----------------------------------------------------------- worktree setup


def _clone_tree(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = "-a" if lb.IS_LINUX else "-cR"
    subprocess.run(["/bin/cp", flags, str(source), str(target)], check=True)


def setup_worktree() -> int:
    primary = _primary_repo()
    if primary == REPO:
        print("OK    primary checkout; nothing to share")
        return 0
    links = (
        (primary / ".cache" / "objects", REPO / ".cache" / "objects"),
        (primary / "benchmarks" / ".cache" / "wheelhouse", REPO / "benchmarks" / ".cache" / "wheelhouse"),
    )
    for source, target in links:
        if not source.is_dir():
            raise LaneError(f"primary checkout lacks {source}; run fetch there first")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
        print(f"OK    {target} -> {target.resolve()}")
    # The doctor check requires clang's resource directory beneath this
    # worktree's own prefix, and Cargo writes into its home: clone both.
    for source, target in ((primary / ".cache" / "llvm", REPO / ".cache" / "llvm"),
                           (primary / "rust-cpython" / ".cargo-home", LANE / ".cargo-home")):
        if not source.is_dir():
            raise LaneError(f"primary checkout lacks {source}; run fetch there first")
        if not target.exists():
            _clone_tree(source, target)
        print(f"OK    {target} (clone)")
    report = lb.doctor_report()
    if not report["ok"]:
        raise LaneError("doctor still reports problems: " + "; ".join(report.get("problems", [])))
    print("OK    doctor")
    return 0


def fetch() -> int:
    from benchmarks.harness.inputs import (
        LINUX_CP316_LOCK_PATH,
        MACOS_CP316_LOCK_PATH,
        fetch_inputs,
        load_lock,
    )

    lock = LINUX_CP316_LOCK_PATH if lb.IS_LINUX else MACOS_CP316_LOCK_PATH
    print(f"OK    Django closure -> {fetch_inputs(load_lock(lock), groups={'django'})}")
    return 0


def status() -> int:
    holders = lease_holders()
    print("host lease: " + ("free" if not holders else _describe_holders()))
    lanes = {LANE}
    with contextlib.suppress(LaneError):
        lanes.add(_primary_repo() / "rust-cpython")
    for lane in sorted(lanes):
        for report_path in sorted((lane / "results").glob("perf-*.json")):
            report = json.loads(report_path.read_text())
            git_state = report.get("git", {})
            print(f"{report_path.stem:<24} {report.get('status', '?'):<13} "
                  f"commit={git_state.get('commit', '?')[:10]} dirty={git_state.get('overlay_dirty')} "
                  f"incremental={report.get('incremental')} verified={'stage_identity' in report} "
                  f"({lane})")
    return 0


def clean(*, name: str | None = None) -> int:
    if name is not None:
        _check_name(name)
        paths = _paths(name)
        for key in ("work", "stage"):
            if paths[key].exists():
                shutil.rmtree(paths[key])
        for key in ("configure_log", "build_log", "install_log", "cargo_log", "report"):
            if paths[key].is_file():
                paths[key].unlink()
        print(f"OK    removed perf outputs for {_tag(name)}")
        return 0
    perf_work = lb.WORK / "perf"
    if perf_work.exists():
        shutil.rmtree(perf_work)
    for path in LANE.glob("stage-perf-*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    for path in lb.LOGS.glob("perf-*"):
        if path.is_file():
            path.unlink()
    for path in lb.RESULTS.glob("perf-*"):
        if path.is_file():
            path.unlink()
    print("OK    removed perf builds (coverage trees and results/perf-bench untouched)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rust-for-CPython fast-iteration perf builds and hill climbing")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="report host and locked input readiness")
    commands.add_parser("fetch", help="fetch the locked cp316 Django closure (online)")
    commands.add_parser("setup-worktree", help="share verified caches from the primary checkout")
    commands.add_parser("status", help="show the host lease and perf builds")
    build_parser = commands.add_parser("build", help="build an optimized no-PGO no-LTO interpreter")
    build_parser.add_argument("--name", required=True, help="isolated build name, e.g. perf-rust")
    build_parser.add_argument("--empty-overlay", action="store_true",
                              help="build the pristine fork without the Rust overlay")
    build_parser.add_argument("--incremental", action="store_true",
                              help="reuse the configured tree; sync changed overlay files only")
    build_parser.add_argument("--upstream-compression", action="store_true",
                              help="link CPython's stock zlib, _bz2 and binascii modules against "
                                   "zlib-rs and libbz2-rs instead of the private _zlib_rs and _bz2_rs")
    build_parser.add_argument("--jobs", type=int, metavar="N", help="parallel build jobs")
    cargo_parser = commands.add_parser(
        "cargo", help="run the pinned cargo in a perf build's assembled source, after an overlay sync")
    cargo_parser.add_argument("--name", required=True, help="isolated build name to run cargo in")
    cargo_parser.add_argument("cargo_args", nargs="+", metavar="CARGO_ARG",
                              help="arguments forwarded verbatim to cargo, after `--`")
    test_parser = commands.add_parser("test", help="run CPython suites on a perf build")
    test_parser.add_argument("--name", required=True, help="isolated build name to test")
    test_parser.add_argument("--all", action="store_true", help="run all default-resource CPython tests")
    test_parser.add_argument("--jobs", type=int, metavar="N", help="parallel test workers")
    test_parser.add_argument("--suite", action="append", default=[], metavar="TEST_NAME",
                             help="complete CPython test module or package; repeatable")
    for command, help_text in (("bench", "paired measurement with a hill-climbing verdict"),
                               ("calibrate", "self-compare one build; every workload must read within budget")):
        sub = commands.add_parser(command, help=help_text)
        if command == "bench":
            sub.add_argument("--baseline", required=True, help="@control, @incumbent, or a local build name")
            sub.add_argument("--candidate", required=True, help="@incumbent or a local build name")
            sub.add_argument("--record-baselines", action="store_true",
                             help="coordinator only: update benchmarks/baselines/rust-cp316-perf-*.json")
        else:
            sub.add_argument("--ref", required=True, help="@control, @incumbent, or a local build name")
        sub.add_argument("--workload", action="append", default=[], metavar="NAME",
                         help="target application workload; repeatable")
        sub.add_argument("--module", action="append", default=[], metavar="ROUTE",
                         help="target module kernel (checklist route, e.g. json); repeatable")
        sub.add_argument("--all-modules", action="store_true", help="evaluate all 71 module kernels")
        sub.add_argument("--all-workloads", action="store_true",
                         help="evaluate every workload with locked 3.16 inputs")
        sub.add_argument("--gate", action="store_true",
                         help="acceptance mode: clean committed builds, gate workloads as guards, 2 runs")
        sub.add_argument("--runs", type=int, help="independent runs (default 2 with --gate, else 1)")
        sub.add_argument("--profile", choices=("quick", "standard", "rigorous"), default="standard")
        sub.add_argument("--memory-only", action="store_true",
                         help="judge only memory; timing and host quietness do not qualify or block")
        sub.add_argument("--matched-prefix", action="store_true",
                         help="memory-only: use equal-length visible installation home aliases")
        sub.add_argument("--matched-executable", action="store_true",
                         help="with --matched-prefix: also launch through equal-length executable aliases")
        sub.add_argument("--timing-only", action="store_true", help="skip the memory pass (exploration)")
        sub.add_argument("--min-idle", type=float, default=DEFAULT_MIN_IDLE,
                         help="percent CPU idle required around measurements")
    goals_parser = commands.add_parser("goals", help="per-module goal status against the control")
    goals_parser.add_argument("--candidate", default="@incumbent", help="@incumbent or a local build name")
    goals_parser.add_argument("--module", action="append", default=[], metavar="ROUTE",
                              help="module route; repeatable (default: all 71)")
    goals_parser.add_argument("--runs", type=int, default=2, help="independent runs (at least 2)")
    goals_parser.add_argument("--profile", choices=("quick", "standard", "rigorous"), default="standard")
    goals_parser.add_argument("--memory-only", action="store_true",
                              help="classify goals using load footprint and working peak only")
    goals_parser.add_argument("--matched-prefix", action="store_true",
                              help="memory-only: use equal-length visible installation home aliases")
    goals_parser.add_argument("--matched-executable", action="store_true",
                              help="with --matched-prefix: also match executable launch-path lengths")
    goals_parser.add_argument("--min-idle", type=float, default=DEFAULT_MIN_IDLE)
    commands.add_parser("modules", help="list module kernel routes")
    profile_parser = commands.add_parser("profile", help="profile one workload or module kernel on one build")
    profile_parser.add_argument("--ref", required=True, help="@control, @incumbent, or a local build name")
    profile_parser.add_argument("--workload", help="application workload")
    profile_parser.add_argument("--module", help="module kernel route, e.g. json")
    profile_parser.add_argument("--tool", choices=("cprofile", "sample", "importtime"), default="cprofile",
                                help="cprofile: Python call costs; sample: native stacks incl. Rust; "
                                     "importtime: import costs")
    profile_parser.add_argument("--iterations", type=int, help="workload iterations (default: registry value)")
    profile_parser.add_argument("--seconds", type=int, default=5, help="sample duration")
    clean_parser = commands.add_parser("clean", help="remove perf builds only")
    clean_parser.add_argument("--name", default=None, help="isolated build name; omit for all perf builds")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            report = lb.doctor_report()
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        if args.command == "fetch":
            return fetch()
        if args.command == "setup-worktree":
            return setup_worktree()
        if args.command == "status":
            return status()
        if args.command == "build":
            return build(name=args.name, empty_overlay=args.empty_overlay, jobs=args.jobs,
                         incremental=args.incremental, upstream_compression=args.upstream_compression)
        if args.command == "cargo":
            return cargo(name=args.name, arguments=args.cargo_args)
        if args.command == "test":
            return test(name=args.name, suites=args.suite, all_suites=args.all, jobs=args.jobs)
        if args.command == "bench":
            return bench(baseline_ref=args.baseline, candidate_ref=args.candidate,
                         workloads=args.workload, gate=args.gate, runs=args.runs,
                         profile=args.profile, timing_only=args.timing_only,
                         min_idle=args.min_idle, modules=args.module,
                         all_workloads=args.all_workloads, all_modules=args.all_modules,
                         record_baselines=args.record_baselines, memory_only=args.memory_only,
                         matched_prefix=args.matched_prefix, matched_executable=args.matched_executable)
        if args.command == "calibrate":
            return bench(baseline_ref=args.ref, candidate_ref=args.ref, workloads=args.workload,
                         gate=args.gate, runs=args.runs, profile=args.profile,
                         timing_only=args.timing_only, min_idle=args.min_idle, modules=args.module,
                         all_workloads=args.all_workloads, all_modules=args.all_modules,
                         self_compare=True, memory_only=args.memory_only,
                         matched_prefix=args.matched_prefix, matched_executable=args.matched_executable)
        if args.command == "goals":
            return goals(candidate_ref=args.candidate, modules=args.module, runs=args.runs,
                         profile=args.profile, min_idle=args.min_idle, memory_only=args.memory_only,
                         matched_prefix=args.matched_prefix, matched_executable=args.matched_executable)
        if args.command == "modules":
            print("\n".join(module_routes()))
            return 0
        if args.command == "profile":
            return profile(ref=args.ref, workload=args.workload, module=args.module, tool=args.tool,
                           iterations=args.iterations, seconds=args.seconds)
        return clean(name=args.name)
    except (LaneError, lb.InputError, lb.BootstrapError, lb.SandboxError,
            OSError, ValueError) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
