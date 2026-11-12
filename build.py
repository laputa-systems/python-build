"""Typed controller front door for the product distribution build.

The CLI validates arguments and delegates to buildsys; heavy work lives in
buildsys and per-phase drivers. Unsupported targets fail closed: no artifacts
are created for a target that has no implemented native executor.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buildsys.targets import (
    TARGETS,
    UnsupportedTargetError,
    docker_execution_mode,
    native_target,
)

SUPPORTED = set(TARGETS)
try:
    _NATIVE = native_target()
except UnsupportedTargetError:
    _NATIVE = None
# `doctor` reports whatever this host/container actually is, even if
# unsupported; every other command needs --target to name a SUPPORTED
# triple explicitly, so this default is a convenience, not a fallback that
# papers over an unsupported machine.
DEFAULT_TARGET = _NATIVE.triple if _NATIVE else "x86_64-unknown-linux-musl"

# Commands that run compiled recipes/the interpreter itself: these are never
# cross-compiled, so the requested --target must be the
# architecture this process is actually running as right now.
NATIVE_EXECUTION_COMMANDS = {
    "build", "package", "compare-reference", "reproduce"
}


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _require_target(target: str) -> None:
    if target not in SUPPORTED:
        _fail(f"target not implemented: {target} (supported: {sorted(SUPPORTED)})")


def _require_native_target(target: str) -> None:
    _require_target(target)
    if _NATIVE is None:
        _fail(f"this machine ({platform.machine()}) has no target description; "
              f"run inside a container built for one of {sorted(SUPPORTED)}")
    if target != _NATIVE.triple:
        if _NATIVE.is_macos:
            _fail(f"--target {target} does not match the running machine "
                  f"({_NATIVE.triple}); the macOS target builds and runs "
                  f"natively on Apple Silicon, it does not cross-compile")
        _fail(f"--target {target} does not match the running machine "
              f"({_NATIVE.triple}); this project builds natively per-arch "
              f"via `docker build --platform {TARGETS[target].docker_platform}`, "
              f"it does not cross-compile")


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _qualification_available(target=None) -> bool:
    """Sealed runs execute under a platform-appropriate isolation mechanism.

    Linux targets use Dockerfile-defined BuildKit stages; macOS cannot build
    or run Mach-O binaries in a Linux container, so it uses a `sandbox-exec`
    profile with `(deny network*)` instead. Neither is
    assumed to work — each is probed.
    """
    target = target or _NATIVE
    if target is not None and target.is_macos:
        return Path(SANDBOX_EXEC).is_file()
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(
        ["docker", "buildx", "version"], capture_output=True, timeout=30,
        check=False,
    )
    return probe.returncode == 0


def _macos_report(report: dict) -> None:
    """Host, SDK, and toolchain facts the macOS build depends on."""
    from buildsys.bootstrap import (
        BootstrapError,
        linker_identity,
        load_macos_toolchain,
        problems,
    )

    report["macos"] = {"product_version": platform.mac_ver()[0]}
    report["macos"]["linker"] = linker_identity()
    report["macos"]["sandbox_exec"] = SANDBOX_EXEC if Path(SANDBOX_EXEC).is_file() else None
    report["macos"]["codesign"] = shutil.which("codesign")
    lock = Path(__file__).resolve().parent / "bootstrap.lock.json"
    try:
        locked = load_macos_toolchain(lock)
    except BootstrapError as error:
        report["macos"]["toolchain_lock"] = {"ok": False, "error": str(error)}
        return
    found = problems(locked, host_floor=locked.host_floor)
    report["macos"]["developer_dir"] = (
        str(locked.xcode.developer_dir) if locked.xcode.developer_dir else None
    )
    report["macos"]["sdk_path"] = (
        str(locked.xcode.sdkroot) if locked.xcode.sdkroot else None
    )
    report["macos"]["sdk_version"] = locked.xcode.sdk_version
    report["macos"]["xcode_version"] = locked.xcode.xcode_version
    report["macos"]["xcode_build"] = locked.xcode.xcode_build
    report["macos"]["toolchain_lock"] = {
        "ok": not found,
        "llvm_version": locked.llvm_version,
        "llvm_archive_sha256": locked.llvm_archive_sha256,
        "llvm_archive_size": locked.llvm_archive_size,
        "llvm_prefix": str(locked.llvm_prefix),
        "host_floor": locked.host_floor,
        "deployment_target": locked.deployment_target,
        "xcode_major": locked.xcode_major,
        "cpu_baseline": locked.cpu_baseline,
        "problems": found,
    }
    clang = locked.llvm_prefix / "bin" / "clang"
    report["macos"]["clang"] = str(clang) if clang.is_file() else None
    report["macos"]["clang_reported"] = (
        subprocess.run([str(clang), "--version"], capture_output=True, text=True, check=False)
        .stdout.splitlines()[0] if clang.is_file() else None
    )
    profdata = locked.llvm_profdata
    report["macos"]["llvm_profdata"] = (
        str(profdata) if profdata.is_file() else None
    )
    report["macos"]["llvm_profdata_reported"] = (
        subprocess.run([str(profdata), "--version"], capture_output=True, text=True, check=False)
        .stdout.splitlines()[0] if profdata.is_file() else None
    )


def doctor() -> int:
    container = Path("/.dockerenv").is_file()
    report = {
        "host": platform.machine(),
        "platform": platform.system(),
        "python": sys.version.split()[0],
        "native_target": _NATIVE.triple if _NATIVE else None,
        "native_family": _NATIVE.family if _NATIVE else None,
        "supported_targets": sorted(SUPPORTED),
        "qualification": (
            "unavailable"
            if not _qualification_available()
            else ("sandbox-exec" if _NATIVE and _NATIVE.is_macos else "docker-buildkit")
        ),
        "in_container": container,
    }
    if _NATIVE is not None and _NATIVE.is_macos:
        _macos_report(report)
    else:
        for tool in ("clang", "ld.lld", "llvm-ar", "patchelf", "pkg-config"):
            report[tool] = shutil.which(tool)
    print(json.dumps(report, indent=2))
    return 0



def _controller_test() -> int:
    """Run every test module in its own process, in parallel."""
    import re
    import time
    from concurrent.futures import ThreadPoolExecutor

    root = Path(__file__).parent
    modules = sorted(path.name for path in (root / "tests").glob("test_*.py"))

    def run(module: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", module],
            cwd=root, capture_output=True, text=True, check=False,
        )

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = list(pool.map(run, modules))
    total = 0
    failed = []
    for module, result in zip(modules, results):
        counted = re.search(r"^Ran (\d+) tests?", result.stderr, re.MULTILINE)
        total += int(counted.group(1)) if counted else 0
        if result.returncode != 0 or counted is None:
            failed.append(module)
            print(f"--- {module} (exit {result.returncode})\n{result.stdout}{result.stderr}")
    print(
        f"Ran {total} tests in {len(modules)} modules in {time.monotonic() - started:.1f}s; "
        f"{len(failed)} failed{': ' + ', '.join(failed) if failed else ''}"
    )
    return 1 if failed else 0


def build(args: argparse.Namespace) -> int:
    _require_native_target(args.target)
    if args.pgo_jobs is not None and not _NATIVE.pgo:
        _fail("--pgo-jobs requires a target with PGO enabled")
    result = subprocess.run(
        [sys.executable, "build/deps.py"], cwd=Path(__file__).parent,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    cpython_command = [sys.executable, "build/cpython.py"]
    if args.pgo_jobs is not None:
        cpython_command.extend(["--pgo-jobs", str(args.pgo_jobs)])
    return subprocess.run(cpython_command, cwd=Path(__file__).parent, check=False).returncode


def verify(target: str) -> int:
    from buildsys.generations import GenerationError, active_generation
    from buildsys.uvmirror import UvMirrorError, validate_artifact

    root = Path(__file__).parent
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        directory = active_generation(root / "dist" / target)
        archive, _ = validate_artifact(directory, target, revision)
    except (OSError, subprocess.SubprocessError, GenerationError, UvMirrorError) as error:
        _fail(f"{target}: published artifact failed verification: {error}")
    print(f"OK    verify {archive}")
    return 0


def qualify_linux(target: str) -> int:
    description = TARGETS[target]
    if description.family != "linux-musl":
        _fail(f"target {target} does not use Linux container qualification")
    if not shutil.which("docker") or subprocess.run(
        ["docker", "buildx", "version"], capture_output=True, timeout=30,
        check=False,
    ).returncode != 0:
        _fail("Linux qualification requires Docker Buildx")
    try:
        docker_host = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}} {{.Architecture}}"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError) as error:
        _fail(f"cannot identify the Docker runtime architecture: {error}")
    if len(docker_host) != 2 or docker_host[0] != "linux":
        _fail(f"Docker runtime did not report a Linux architecture: {docker_host!r}")
    try:
        execution = docker_execution_mode(description, docker_host[1])
    except UnsupportedTargetError as error:
        _fail(str(error))

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=Path(__file__).parent,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        _fail(f"cannot establish qualification source identity: {error}")
    if dirty:
        _fail("Linux release qualification requires a clean source checkout")

    fetch = subprocess.run(
        [sys.executable, "build/fetch.py", "--target", target],
        cwd=Path(__file__).parent,
        check=False,
    )
    if fetch.returncode != 0:
        return fetch.returncode

    from buildsys.generations import GenerationError, publish_generation
    from buildsys.package import PackageError, write_manifest
    from buildsys.uvmirror import UvMirrorError, record_runtime_container

    scratch = Path(__file__).parent / ".work" / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"qualify-{target}-", dir=scratch) as temporary:
        temporary_root = Path(temporary)
        output = temporary_root / "buildkit-output"
        environment = os.environ.copy()
        environment["TMPDIR"] = str(scratch)
        command = [
            "docker", "buildx", "build",
            "--platform", description.docker_platform,
            "--target", "checked",
            "--build-arg", f"PYTHON_BUILD_SOURCE_REVISION={revision}",
            "--build-arg", "PYTHON_BUILD_SOURCE_DIRTY=0",
            "--output", f"type=local,dest={output}",
            ".",
        ]
        result = subprocess.run(
            command, cwd=Path(__file__).parent, env=environment,
            check=False,
        )
        if result.returncode != 0:
            return result.returncode

        artifact_root = output / "artifacts"
        if not artifact_root.is_dir():
            _fail(f"checked Docker output has no artifacts directory: {artifact_root}")
        entries = list(artifact_root.iterdir())
        files = {path.name for path in entries if path.is_file() and not path.is_symlink()}
        archives = sorted(name for name in files if name.endswith(".tar.gz"))
        if len(archives) != 1 or len(entries) != len(files) or files != {
            "artifact.json", "runtime-smoke.json", *archives,
        }:
            _fail(f"checked Docker output has unexpected files: {sorted(files)}")

        package_directory = temporary_root / "dist" / target
        package_directory.mkdir(parents=True)
        for name in files:
            shutil.copyfile(artifact_root / name, package_directory / name)
        try:
            manifest = record_runtime_container(
                package_directory, target, revision, execution
            )
        except (OSError, json.JSONDecodeError, UvMirrorError) as error:
            _fail(f"checked artifact validation failed: {error}")
        archive_name = manifest["archive"]["filename"]
        archive = package_directory / archive_name
        manifest_temp = temporary_root / "artifact.json"
        write_manifest(manifest_temp, manifest)
        output_directory = Path(__file__).parent / "dist" / target
        try:
            publish_generation(output_directory, {
                archive_name: archive,
                "artifact.json": manifest_temp,
            })
        except (OSError, GenerationError, PackageError) as error:
            _fail(f"cannot publish qualified Linux artifact: {error}")
    print(f"OK    qualify -> {Path(__file__).parent / 'dist' / target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--target", default=DEFAULT_TARGET)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--target", default=DEFAULT_TARGET)
    build_parser.add_argument(
        "--pgo-jobs", type=_positive_int,
        help="number of workers for macOS CPython's --pgo training task "
             "(default: CPython compile jobs, one fewer than host CPUs)",
    )
    commands.add_parser("controller-test")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--target", default=DEFAULT_TARGET)
    qualify = commands.add_parser("qualify")
    qualify.add_argument("--target", default=DEFAULT_TARGET)
    qualify.add_argument(
        "--pgo-jobs", type=_positive_int,
        help="number of workers for macOS CPython's PGO task",
    )
    package = commands.add_parser("package")
    package.add_argument("--target", default=DEFAULT_TARGET)
    compare = commands.add_parser("compare-reference")
    compare.add_argument("--target", default=DEFAULT_TARGET)
    reproduce = commands.add_parser("reproduce")
    reproduce.add_argument("--target", default=DEFAULT_TARGET)
    reproduce.add_argument(
        "--pgo-jobs", type=_positive_int,
        help="reuse this worker count for each clean macOS PGO build",
    )
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "fetch":
        _require_target(args.target)
        result = subprocess.run(
            [sys.executable, "build/fetch.py", "--target", args.target],
            cwd=Path(__file__).parent,
            check=False,
        )
        return result.returncode
    if args.command == "build":
        return build(args)
    if args.command in NATIVE_EXECUTION_COMMANDS - {"build"}:
        _require_native_target(args.target)
    if args.command == "controller-test":
        return _controller_test()
    if args.command == "verify":
        _require_target(args.target)
        return verify(args.target)
    if args.command == "qualify":
        _require_target(args.target)
        target = TARGETS[args.target]
        if target.family == "linux-musl":
            if args.pgo_jobs is not None:
                _fail("--pgo-jobs requires a target with PGO enabled")
            return qualify_linux(args.target)
        _require_native_target(args.target)
        if args.pgo_jobs is not None and not target.pgo:
            _fail("--pgo-jobs requires a target with PGO enabled")
        sealed_command = [sys.executable, "build/sealed.py"]
        if args.pgo_jobs is not None:
            sealed_command.extend(["--pgo-jobs", str(args.pgo_jobs)])
        result = subprocess.run(sealed_command, cwd=Path(__file__).parent, check=False)
        if result.returncode != 0:
            return result.returncode
        result = subprocess.run(
            [sys.executable, "build/package.py", "--target", args.target],
            cwd=Path(__file__).parent,
            check=False,
        )
        return result.returncode
    if args.command == "compare-reference":
        result = subprocess.run(
            [sys.executable, "build/compare.py", "--target", args.target],
            cwd=Path(__file__).parent,
            check=False,
        )
        return result.returncode
    if args.command == "package":
        result = subprocess.run(
            [sys.executable, "build/package.py", "--target", args.target],
            cwd=Path(__file__).parent,
            check=False,
        )
        return result.returncode
    if args.command == "reproduce":
        _require_target(args.target)
        if not _qualification_available():
            isolation = (
                "sandbox-exec" if _NATIVE and _NATIVE.is_macos
                else "Docker BuildKit"
            )
            _fail(f"reproduce needs the target's sealed build isolation; {isolation} is unavailable")
        result = subprocess.run(
            [sys.executable, "build/reproduce.py", "--target", args.target]
            + (["--pgo-jobs", str(args.pgo_jobs)] if args.pgo_jobs is not None else []),
            cwd=Path(__file__).parent,
            check=False,
        )
        return result.returncode
    _fail(f"command not implemented: {args.command}")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
