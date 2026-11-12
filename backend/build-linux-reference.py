from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from buildsys.bootstrap import BootstrapError, toolchain_for
from buildsys.ca_bundle import CABundleError, install_fallback_ca_bundle
from buildsys.cpython import configuration
from buildsys.inputs import Cache, InputError, locked_inputs, safe_extract
from buildsys.patches import PatchError, apply_patch_set
from buildsys.product import PRODUCT
from buildsys.recipes import RECIPE_LAYOUTS, Recipe, Toolchain, build_recipe, run
from buildsys.recipes import BuildError as RecipeBuildError
from buildsys.relocate import RelocationError, relocate
from buildsys.scope import ScopeError, enforce_exclusions, verify_exclusions
from buildsys.targets import Target, UnsupportedTargetError, native_target

LINUX_CPU_BASELINES = {
    "aarch64-unknown-linux-musl": "-march=armv8-a+crypto",
    "x86_64-unknown-linux-musl": "-march=broadwell",
}
LINUX_CPU_FEATURES = {
    "aarch64-unknown-linux-musl": {"aes", "pmull", "sha2", "asimd"},
    "x86_64-unknown-linux-musl": {"aes", "pclmulqdq", "avx", "avx2", "bmi1", "bmi2", "fma", "movbe"},
}
TARGET_TRIPLE = "aarch64-unknown-linux-musl"
OPENSSL_VERSION = "3.5.7"
OPENSSL_ENVIRONMENT = (
    "OPENSSL_INCLUDES",
    "OPENSSL_LDFLAGS",
    "OPENSSL_LIBS",
    "OPENSSL_RPATH",
)


class ReferenceBuildError(Exception):
    pass


def load_build_driver(name: str):
    path = REPO / "build" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"python_build_{name}_driver", path)
    if spec is None or spec.loader is None:
        raise ReferenceBuildError(f"cannot load existing build driver: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CPYTHON_DRIVER = load_build_driver("cpython")
DEPENDENCY_DRIVER = load_build_driver("deps")
CONFIGURE_PREFIX = CPYTHON_DRIVER.CONFIGURE_PREFIX


@dataclass
class LinuxReferenceToolchain(Toolchain):
    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        environment = super().env(extra)
        supplied = extra or {}
        for name in OPENSSL_ENVIRONMENT:
            if name not in supplied:
                environment.pop(name, None)
        for name in ("PKG_CONFIG_LIBDIR", "PKG_CONFIG_SYSROOT_DIR"):
            if name not in supplied:
                environment.pop(name, None)
        return environment

    def ldflags(self, prefix: Path, extra: str = "") -> str:
        flags = super().ldflags(prefix, extra)
        if "-fuse-ld=lld" not in flags:
            raise ReferenceBuildError("Linux linker flags omitted the expected LLD selector")
        return flags


def require_work_path(path: Path, label: str) -> Path:
    work_root = REPO / ".work"
    if work_root.is_symlink():
        raise ReferenceBuildError(f"working directory must not be a symlink: {work_root}")
    lexical = Path(os.path.abspath(path))
    if not lexical.is_relative_to(work_root):
        raise ReferenceBuildError(f"{label} must stay under {work_root}: {lexical}")
    current = work_root
    for part in lexical.relative_to(work_root).parts:
        current /= part
        if current.is_symlink():
            raise ReferenceBuildError(f"{label} must not traverse a symlink: {current}")
    resolved = lexical.resolve()
    if not resolved.is_relative_to(work_root.resolve()):
        raise ReferenceBuildError(f"{label} resolves outside {work_root}: {resolved}")
    return resolved


def repository_common_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve().parent


def require_same_locks(common_root: Path) -> None:
    for name in ("sources.lock.json", "bootstrap.lock.json"):
        if (REPO / name).read_bytes() != (common_root / name).read_bytes():
            raise ReferenceBuildError(
                f"{name} differs between this worktree and the shared cache owner"
            )


def require_fresh_run_dir(path: Path) -> Path:
    run_dir = require_work_path(path, "reference output")
    if run_dir.exists():
        if run_dir.is_symlink() or not run_dir.is_dir() or any(run_dir.iterdir()):
            raise ReferenceBuildError(f"reference output must be a fresh directory: {run_dir}")
    else:
        run_dir.mkdir(parents=True)
    return run_dir


def require_candidate(candidate_run_dir: Path) -> tuple[Path, Path, Path]:
    candidate = require_work_path(candidate_run_dir, "candidate run directory")
    private = candidate / "private"
    relocated = candidate / "relocated prefix"
    prefix = relocated if relocated.exists() else candidate / "prefix"
    if not private.is_dir() or private.is_symlink():
        raise ReferenceBuildError(f"candidate private dependency prefix is missing: {private}")
    if not prefix.is_dir() or prefix.is_symlink():
        raise ReferenceBuildError(f"candidate CPython prefix is missing: {prefix}")
    if not PRODUCT.interpreter(prefix).is_file() or not PRODUCT.stdlib(prefix).is_dir():
        raise ReferenceBuildError(f"candidate CPython installation is incomplete: {prefix}")
    if private == prefix or private.is_relative_to(prefix) or prefix.is_relative_to(private):
        raise ReferenceBuildError("candidate dependency prefix and CPython prefix overlap")
    return candidate, private, prefix


def is_openssl_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(("libssl", "libcrypto")) or "openssl" in lowered


def verify_candidate_inputs(candidate_prefix: Path, dependency_prefix: Path) -> None:
    for path in dependency_prefix.rglob("*"):
        if is_openssl_name(path.name):
            raise ReferenceBuildError(f"candidate dependency prefix contains OpenSSL: {path}")
    if (dependency_prefix / "include" / "openssl").exists():
        raise ReferenceBuildError("candidate dependency prefix contains OpenSSL headers")
    dynamic = PRODUCT.stdlib(candidate_prefix) / "lib-dynload"
    for name in ("_ssl", "_hashlib"):
        extensions = [
            path
            for pattern in (f"{name}.cpython-*.so", f"{name}.abi3.so", f"{name}.so")
            for path in dynamic.glob(pattern)
        ]
        if extensions:
            raise ReferenceBuildError(
                f"OpenSSL-free candidate prefix unexpectedly contains {name}: {extensions}"
            )


def reference_toolchain(target: Target, jobs: int) -> LinuxReferenceToolchain:
    base = toolchain_for(target, REPO / "bootstrap.lock.json", jobs=jobs)
    values = asdict(base)
    values.update(
        {
            "cpu_baseline": target.cpu_baseline_cflag,
            "pkg_config_path": "",
        }
    )
    toolchain = LinuxReferenceToolchain(**values)
    if toolchain.lto != "-flto=thin":
        raise ReferenceBuildError(f"expected ThinLTO, found {toolchain.lto!r}")
    for command in (
        toolchain.cc,
        toolchain.cxx,
        toolchain.ar,
        toolchain.ranlib,
        toolchain.make,
        "ld.lld",
        "llvm-readelf",
    ):
        if shutil.which(command) is None:
            raise ReferenceBuildError(f"locked Linux build tool is missing: {command}")
    clang = subprocess.run(
        [toolchain.cc, "--version"], capture_output=True, text=True, check=True
    ).stdout
    llvm_version = json.loads((REPO / "bootstrap.lock.json").read_text())["linux_toolchain"]["llvm"]["version"]
    if not re.search(rf"clang version {re.escape(llvm_version)}(?:\s|\()", clang):
        raise ReferenceBuildError(f"expected locked LLVM {llvm_version} clang, found {clang.splitlines()[0]!r}")
    linker = subprocess.run(
        ["ld.lld", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()[0]
    print(f"TOOLCHAIN clang={clang.splitlines()[0]} linker={linker}", flush=True)
    return toolchain


def verify_cpu_features(target: Target) -> list[str]:
    feature_sets = [
        set(line.partition(":")[2].split())
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith(("Features", "flags"))
    ]
    try:
        required = LINUX_CPU_FEATURES[target.triple]
    except KeyError:
        raise ReferenceBuildError(f"reference builder has no CPU feature floor for {target.triple}")
    if not feature_sets or any(not required.issubset(features) for features in feature_sets):
        raise ReferenceBuildError(
            f"native Linux {target.machine} reference requires {sorted(required)} on every CPU"
        )
    return sorted(set.intersection(*feature_sets))


def verify_thin_lto(toolchain: LinuxReferenceToolchain, target: Target, run_dir: Path) -> None:
    from buildsys.elf import machine_report

    fixture = run_dir / "lto-fixture"
    fixture.mkdir()
    library_source = fixture / "library.c"
    main_source = fixture / "main.c"
    object = fixture / "library.o"
    archive = fixture / "libfixture.a"
    binary = fixture / "lto-smoke"
    library_source.write_text("int answer(void) { return 42; }\n", encoding="ascii")
    main_source.write_text(
        "#include <stdio.h>\n"
        "extern int answer(void);\n"
        "int main(void) { printf(\"%d\\n\", answer()); return answer() != 42; }\n",
        encoding="ascii",
    )
    base = [toolchain.cc, "-O3", target.cpu_baseline_cflag, "-flto=thin"]
    run(
        [*base, "-c", str(library_source), "-o", str(object)],
        cwd=fixture,
        env=toolchain.env(),
        log=run_dir / "logs" / "lto-compile.log",
    )
    if object.read_bytes()[:4] != b"BC\xc0\xde":
        raise ReferenceBuildError("ThinLTO smoke object is not LLVM bitcode")
    run(
        [toolchain.ar, "cr", str(archive), str(object)],
        cwd=fixture,
        env=toolchain.env(),
        log=run_dir / "logs" / "lto-archive.log",
    )
    run(
        [*base, "-fuse-ld=lld", str(main_source), str(archive), "-o", str(binary)],
        cwd=fixture,
        env=toolchain.env(),
        log=run_dir / "logs" / "lto-link.log",
    )
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, env=toolchain.env(), check=False
    )
    if result.returncode or result.stdout.strip() != "42":
        raise ReferenceBuildError(
            f"ThinLTO smoke binary failed: rc={result.returncode} output={result.stdout!r}"
        )
    header = subprocess.run(
        ["llvm-readelf", "-h", str(binary)], capture_output=True, text=True, check=True
    ).stdout
    machine = machine_report(header, target.machine)
    if not machine["ok"]:
        raise ReferenceBuildError(f"ThinLTO smoke architecture mismatch: {machine}")
    print(f"LTO_THIN=ok linker=lld machine={target.machine}", flush=True)


def pkg_config_environment(
    toolchain: LinuxReferenceToolchain,
    dependency_prefix: Path,
    openssl_prefix: Path,
) -> dict[str, str]:
    directories = [
        openssl_prefix / "lib" / "pkgconfig",
        openssl_prefix / "share" / "pkgconfig",
        dependency_prefix / "lib" / "pkgconfig",
        dependency_prefix / "share" / "pkgconfig",
    ]
    paths = ":".join(str(path) for path in directories if path.is_dir())
    if not (openssl_prefix / "lib" / "pkgconfig" / "openssl.pc").is_file():
        raise ReferenceBuildError(f"pinned OpenSSL pkg-config metadata is missing under {openssl_prefix}")
    return toolchain.env(
        {
            "PKG_CONFIG": "pkg-config",
            "PKG_CONFIG_PATH": paths,
            "PKG_CONFIG_LIBDIR": paths,
            "PKG_CONFIG_SYSROOT_DIR": "",
        }
    )


def openssl_flags(
    toolchain: LinuxReferenceToolchain,
    dependency_prefix: Path,
    openssl_prefix: Path,
) -> dict[str, str]:
    environment = pkg_config_environment(toolchain, dependency_prefix, openssl_prefix)

    def query(*arguments: str) -> str:
        result = subprocess.run(
            ["pkg-config", *arguments],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        if result.returncode:
            raise ReferenceBuildError(
                f"locked OpenSSL pkg-config query failed: {arguments}: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    version = query("--modversion", "openssl")
    prefix = Path(query("--variable=prefix", "openssl")).resolve()
    if version != OPENSSL_VERSION or prefix != openssl_prefix.resolve():
        raise ReferenceBuildError(
            f"pkg-config selected OpenSSL {version} at {prefix}, expected "
            f"{OPENSSL_VERSION} at {openssl_prefix.resolve()}"
        )
    includes = query("--cflags-only-I", "openssl")
    link_tokens = shlex.split(query("--static", "--libs", "openssl"))
    search_paths = [token for token in link_tokens if token.startswith("-L")]
    libraries = [token for token in link_tokens if not token.startswith("-L")]
    expected_library_directory = (openssl_prefix / "lib").resolve()
    unexpected_search_paths = [
        token[2:]
        for token in search_paths
        if Path(token[2:]).resolve() != expected_library_directory
    ]
    if unexpected_search_paths or not {"-lssl", "-lcrypto"}.issubset(libraries):
        raise ReferenceBuildError(
            "locked OpenSSL pkg-config metadata has unexpected static link flags: "
            f"search_paths={search_paths}, libraries={libraries}"
        )
    flags = {
        "OPENSSL_INCLUDES": includes,
        "OPENSSL_LDFLAGS": shlex.join(search_paths),
        "OPENSSL_LIBS": shlex.join(libraries),
        "OPENSSL_RPATH": "",
    }
    if str(openssl_prefix.resolve()) not in " ".join(flags.values()):
        raise ReferenceBuildError("OpenSSL pkg-config flags do not name the isolated reference prefix")
    print(
        "OPENSSL_FLAGS "
        + " ".join(f"{name}={shlex.quote(value)}" for name, value in flags.items()),
        flush=True,
    )
    pkg_config_values = {
        name: environment[name]
        for name in (
            "PKG_CONFIG",
            "PKG_CONFIG_PATH",
            "PKG_CONFIG_LIBDIR",
            "PKG_CONFIG_SYSROOT_DIR",
        )
    }
    return {**pkg_config_values, **flags}


def build_openssl(
    run_dir: Path,
    cache: Cache,
    pins: dict,
    target: Target,
    toolchain: LinuxReferenceToolchain,
) -> Path:
    entry = pins.get("openssl")
    if entry is None or entry.version != OPENSSL_VERSION:
        raise ReferenceBuildError(f"sources.lock.json does not pin OpenSSL {OPENSSL_VERSION}")
    blob = cache.require(entry)
    prefix = run_dir / "openssl"
    prefix.mkdir()
    recipe = Recipe(
        name="openssl",
        layout=RECIPE_LAYOUTS["openssl"],
        configure_args=DEPENDENCY_DRIVER.configure_args("openssl", prefix, target),
        make_targets=DEPENDENCY_DRIVER.make_targets("openssl"),
        log_path=run_dir / "logs" / "openssl",
    )
    work = run_dir / "openssl-work"
    work.mkdir()
    try:
        build_recipe(recipe, blob, work, prefix, toolchain=toolchain)
    except RecipeBuildError as error:
        raise ReferenceBuildError(f"pinned OpenSSL build failed: {error}") from error
    for archive in (prefix / "lib" / "libssl.a", prefix / "lib" / "libcrypto.a"):
        if not archive.is_file():
            raise ReferenceBuildError(f"pinned OpenSSL install is missing {archive}")
    if (prefix / "lib" / "libssl.so").exists() or (prefix / "lib" / "libcrypto.so").exists():
        raise ReferenceBuildError("reference OpenSSL unexpectedly installed shared libraries")
    print(f"OPENSSL_BUILT={OPENSSL_VERSION} prefix={prefix}", flush=True)
    return prefix


def build_cpython_reference(
    run_dir: Path,
    dependency_prefix: Path,
    openssl_prefix: Path,
    cache: Cache,
    pins: dict,
    target: Target,
    toolchain: LinuxReferenceToolchain,
) -> Path:
    cpython = pins.get("cpython")
    if cpython is None or cpython.version != PRODUCT.version_string:
        raise ReferenceBuildError(
            f"sources.lock.json does not pin CPython {PRODUCT.version_string}"
        )
    blob = cache.require(cpython)
    source_root = run_dir / "cpython-source"
    source = safe_extract(blob, source_root) / f"Python-{PRODUCT.version_string}"
    apply_patch_set(source, REPO / "patches" / "cpython")
    apply_patch_set(source, REPO / "backend" / "cpython-linux-patches")
    build_directory = run_dir / "cpython-build"
    build_directory.mkdir()
    prefix = run_dir / "prefix"
    staged = run_dir / "stage"
    if prefix.exists() or staged.exists():
        raise ReferenceBuildError("reference CPython prefix or stage already exists")
    staged.mkdir()

    args, environment = configuration(dependency_prefix, target)
    args = [
        argument
        for argument in args
        if not argument.startswith("--with-openssl=")
        and argument != "--with-openssl-rpath=no"
    ]
    args.extend((f"--with-openssl={openssl_prefix}", "--with-openssl-rpath=no"))
    if "--enable-optimizations" in args:
        raise ReferenceBuildError("Linux reference configuration unexpectedly enables PGO")
    if any("--disable-gil" in argument for argument in args):
        raise ReferenceBuildError("reference configuration unexpectedly disables the GIL")
    if "--enable-shared" not in args or "--without-static-libpython" not in args:
        raise ReferenceBuildError("reference configuration must build shared libpython")
    if "--with-ensurepip=no" not in args:
        raise ReferenceBuildError("reference configuration must disable ensurepip")

    explicit_openssl = openssl_flags(toolchain, dependency_prefix, openssl_prefix)
    environment.update(explicit_openssl)
    environment["LDFLAGS"] += " -flto=thin"
    if "-fuse-ld=lld" not in environment["LDFLAGS"]:
        raise ReferenceBuildError("CPython link flags do not select lld")
    environment.update(
        {
            "SOURCE_DATE_EPOCH": "1704067200",
            "PKG_CONFIG": "pkg-config",
        }
    )
    env = toolchain.env(environment)
    configure = source / "configure"
    run(
        [str(configure), f"--prefix={CONFIGURE_PREFIX}", *args],
        cwd=build_directory,
        env=env,
        log=run_dir / "logs" / "cpython-configure.log",
    )
    print(f"BUILD CPython {PRODUCT.version_string} reference without PGO", flush=True)
    run(
        [toolchain.make, "-j", str(toolchain.jobs)],
        cwd=build_directory,
        env=env,
        log=run_dir / "logs" / "cpython-build.log",
    )
    run(
        [toolchain.make, "install", f"DESTDIR={staged}"],
        cwd=build_directory,
        env=env,
        log=run_dir / "logs" / "cpython-install.log",
    )
    install = staged / CONFIGURE_PREFIX.lstrip("/")
    if not install.is_dir():
        raise ReferenceBuildError(f"CPython install did not produce {install}")
    shutil.move(str(install), str(prefix))

    try:
        install_fallback_ca_bundle(
            prefix,
            cache_root=repository_common_root() / ".cache",
            lock_path=REPO / "sources.lock.json",
        )
    except CABundleError as error:
        raise ReferenceBuildError(f"cannot install the locked CA fallback: {error}") from error

    command_paths = tuple(
        Path(path)
        for path in (
            toolchain.cc,
            toolchain.cxx,
            toolchain.ar,
            toolchain.ranlib,
            toolchain.ld,
            toolchain.nm,
            toolchain.strip,
            toolchain.make,
            shutil.which("ld.lld"),
            shutil.which("pkg-config"),
        )
        if path
    )
    try:
        relocate(
            prefix,
            dependency_prefix,
            configure_prefix=CONFIGURE_PREFIX,
            builder_paths=(source_root, build_directory, staged, openssl_prefix),
            command_paths=command_paths,
        )
    except RelocationError as error:
        raise ReferenceBuildError(f"cannot relocate OpenSSL reference interpreter: {error}") from error
    try:
        exclusions = enforce_exclusions(prefix)
    except ScopeError as error:
        raise ReferenceBuildError(f"cannot apply product exclusions: {error}") from error
    if verify_exclusions(prefix):
        raise ReferenceBuildError("reference interpreter retained product-excluded files")
    test_library = PRODUCT.stdlib(prefix) / "test"
    if not test_library.is_dir():
        raise ReferenceBuildError("reference prefix must retain Lib/test for qualification")
    print(f"PRODUCT_EXCLUSIONS={len(exclusions['removed'])} retained={test_library}", flush=True)
    verify_reference_runtime(prefix, run_dir)
    return prefix


def verify_reference_runtime(prefix: Path, run_dir: Path) -> None:
    from buildsys.product import PRODUCT

    binary = PRODUCT.interpreter(prefix)
    library = prefix / "lib" / f"libpython{PRODUCT.major_minor}.so.1.0"
    if not binary.is_file() or not library.is_file():
        raise ReferenceBuildError("reference interpreter or shared libpython is missing")
    code = (
        "import _hashlib, _ssl, hashlib, json, ssl, sys; "
        f"assert sys.version_info[:3] == {PRODUCT.version!r}; "
        "assert _ssl.__file__.endswith('.so'); "
        "assert _hashlib.__file__.endswith('.so'); "
        f"assert ssl.OPENSSL_VERSION.startswith('OpenSSL {OPENSSL_VERSION}'), ssl.OPENSSL_VERSION; "
        "print(json.dumps({'openssl_version': ssl.OPENSSL_VERSION, "
        "'ssl_extension': _ssl.__file__, 'hashlib_extension': _hashlib.__file__, "
        "'default_paths': ssl.get_default_verify_paths()._asdict(), "
        "'sha256': hashlib.sha256(b'reference').hexdigest()}, sort_keys=True))"
    )
    environment = {"HOME": str(run_dir / "home"), "TMPDIR": str(run_dir / "tmp")}
    result = subprocess.run(
        [str(binary), "-I", "-S", "-B", "-c", code],
        cwd=run_dir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    log = run_dir / "logs" / "reference-runtime-check.log"
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ReferenceBuildError(
            f"reference interpreter failed its OpenSSL startup check; see {log}: "
            f"{result.stdout}{result.stderr}"
        )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ReferenceBuildError(f"reference runtime check emitted invalid JSON: {error}") from error
    if not report["openssl_version"].startswith(f"OpenSSL {OPENSSL_VERSION}"):
        raise ReferenceBuildError(f"reference runtime reported wrong OpenSSL: {report}")
    if not report["sha256"]:
        raise ReferenceBuildError("reference hashlib smoke result is empty")
    print(
        f"REFERENCE_RUNTIME={PRODUCT.version_string} "
        f"OPENSSL={report['openssl_version']} PREFIX={prefix}",
        flush=True,
    )

    from buildsys.elf import dynamic_tags

    for name in ("_ssl", "_hashlib"):
        extension = Path(report["ssl_extension"] if name == "_ssl" else report["hashlib_extension"])
        dynamic = subprocess.run(
            ["llvm-readelf", "-d", str(extension)], capture_output=True, text=True, check=True
        ).stdout
        tags = dynamic_tags(dynamic)
        if any("libssl.so" in library or "libcrypto.so" in library for library in tags["needed"]):
            raise ReferenceBuildError(f"{name} dynamically depends on ambient OpenSSL: {tags['needed']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a native Linux CPython reference with locked OpenSSL"
    )
    parser.add_argument("--candidate-run-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, required=True)
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least one")

    try:
        common_root = repository_common_root()
        require_same_locks(common_root)
        target = native_target()
        if target.triple not in LINUX_CPU_BASELINES or platform.system() != "Linux":
            raise ReferenceBuildError(
                f"reference builder requires native Linux musl, found {target.triple}"
            )
        target = replace(target, cpu_baseline_cflag=LINUX_CPU_BASELINES[target.triple])
        candidate, dependency_prefix, candidate_prefix = require_candidate(args.candidate_run_dir)
        verify_candidate_inputs(candidate_prefix, dependency_prefix)
        cpu_features = verify_cpu_features(target)
        print(
            f"CPU_FEATURES={','.join(cpu_features)} BASELINE={target.cpu_baseline_cflag}",
            flush=True,
        )
        requested_run_dir = require_work_path(args.run_dir, "reference output")
        if (
            requested_run_dir == candidate
            or candidate.is_relative_to(requested_run_dir)
            or any(requested_run_dir.is_relative_to(candidate / name) for name in (
                "private", "prefix", "relocated prefix", "inputs", "rtls-build", "hashing-build",
                "cargo-home", "cargo-target", "cpython-source", "cpython-build", "logs",
            ))
        ):
            raise ReferenceBuildError("reference output overlaps protected candidate inputs or outputs")
        run_dir = require_fresh_run_dir(requested_run_dir)
        for directory in (run_dir / "logs", run_dir / "tmp", run_dir / "home"):
            directory.mkdir()
        os.environ["TMPDIR"] = str(run_dir / "tmp")
        os.environ["HOME"] = str(run_dir / "home")
        toolchain = reference_toolchain(target, args.jobs)
        verify_thin_lto(toolchain, target, run_dir)
        entries = {entry.name: entry for entry in locked_inputs(REPO / "sources.lock.json")}
        cache = Cache(common_root / ".cache")
        openssl_prefix = build_openssl(run_dir, cache, entries, target, toolchain)
        prefix = build_cpython_reference(
            run_dir,
            dependency_prefix,
            openssl_prefix,
            cache,
            entries,
            target,
            toolchain,
        )
        print(f"LINUX_REFERENCE_PREFIX={prefix}", flush=True)
        print(f"LINUX_REFERENCE_LOGS={run_dir / 'logs'}", flush=True)
        return 0
    except (
        BootstrapError,
        InputError,
        PatchError,
        ReferenceBuildError,
        RecipeBuildError,
        RelocationError,
        ScopeError,
        UnsupportedTargetError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"FAIL Linux reference build: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
