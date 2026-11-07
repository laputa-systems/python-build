"""Runtime, ABI, and capability validation for the macOS distribution.

Every check here runs against the *installed* tree, not the build tree, and
exercises real behaviour rather than asserting a filename exists. The bias is
deliberate: this project's failure mode is not a crash, it is an artifact
that looks right and quietly depends on something the builder happened to
have. So the checks that matter most are the ones that would still pass if
something were subtly wrong — extension building without an installer, an
embedder linking the shipped libpython, ctypes loading a private dylib with
no build tooling present, and TLS verification in both directions.

`run_validation` returns a report shaped for `dist/validation.json`; the
caller decides what is fatal.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

from . import macho
from .bootstrap import load_macos_toolchain
from .elf import ELF_READER
from .package import copy_product_tree
from .product import PRODUCT
from .product_process import product_environment, run_product_process
from .relocate import stale_sysconfig_values
from .scope import probe_in_process, verify_exclusions

ROOT = Path(__file__).resolve().parents[1]

# Load commands the payload may have, by prefix. Anything else is a leak:
# either a build-machine path, or a library this project did not bundle and
# the platform does not provide.
REQUIRED_MODULES = (
    "ssl", "hashlib", "sqlite3", "dbm", "dbm.ndbm", "decimal", "_decimal", "uuid",
    "xml.etree.ElementTree", "zlib", "bz2", "lzma", "compression.zstd",
    "ctypes", "readline", "curses", "curses.panel", "zoneinfo", "socket",
    "threading", "concurrent.futures", "subprocess", "multiprocessing",
    "select", "fcntl", "termios", "resource", "mmap", "signal", "locale",
)

# Absent by scope decision, not by accident. `_gdbm` is absent because the
# dbm backend is the platform's ndbm and no gdbm is built.
EXCLUDED_MODULES = (
    # packaging, by the 2026-09-18 scope decision
    "pip", "ensurepip", "venv",
    # GUI closure, by the 2026-09-17 scope decision. `tkinter` is pure Python
    # and would otherwise be installed-but-unimportable, so it is removed
    # rather than merely left unbuilt.
    "_tkinter", "tkinter", "idlelib", "turtle",
    # no gdbm is built; the dbm backend is the platform's ndbm
    "_gdbm",
    # native test extensions, pruned from the shipped payload after the
    # pre-prune regression suite and C-API probes exercise them
    "_ctypes_test", "_testbuffer", "_testcapi", "_testclinic",
    "_testclinic_limited", "_testimportmultiple", "_testinternalcapi",
    "_testlimitedcapi", "_testmultiphase", "_testsinglephase",
    "_xxtestfuzz", "xxlimited", "xxlimited_3_13", "xxlimited_35",
    "xxsubtype",
)


class ValidationError(Exception):
    """A validation check could not produce evidence."""


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, **kwargs, check=False)


def _product_run(
    argv: list[str | Path],
    install: Path,
    cwd: Path,
    *,
    timeout_seconds: float = 120,
    environment_overrides: dict[str, str] | None = None,
    executable: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_product_process(
        argv,
        install,
        cwd,
        timeout_seconds=timeout_seconds,
        executable=executable,
        environment_overrides=environment_overrides,
    )


def _python_code(
    python: Path,
    code: str,
    *,
    cwd: Path | None = None,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    python = Path(python).resolve()
    install = python.parent.parent
    return _product_run(
        [python, "-I", "-B", "-c", textwrap.dedent(code)],
        install,
        Path.cwd() if cwd is None else cwd,
        environment_overrides=environment_overrides,
    )


_FAT_MACHO_MAGICS = frozenset({
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
})


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _resolve_image_path(
    value: str,
    image: Path,
    install: Path,
    executable: Path | None = None,
) -> Path | None:
    if value == "@loader_path":
        candidate = image.parent
    elif value.startswith("@loader_path/"):
        candidate = image.parent / value.removeprefix("@loader_path/")
    elif value == "@executable_path":
        if executable is None:
            return None
        candidate = executable.parent
    elif value.startswith("@executable_path/"):
        if executable is None:
            return None
        candidate = executable.parent / value.removeprefix("@executable_path/")
    elif value.startswith("/"):
        candidate = Path(value)
    else:
        return None
    resolved = candidate.resolve()
    return resolved if _inside(resolved, install) else None


def _rpath_directories(
    rpath: str,
    image: Path,
    install: Path,
    executable_images: list[Path],
) -> list[Path | None]:
    if rpath == "@loader_path" or rpath.startswith("@loader_path/"):
        return [_resolve_image_path(rpath, image, install)]
    if rpath == "@executable_path" or rpath.startswith("@executable_path/"):
        return [
            _resolve_image_path(rpath, image, install, executable)
            for executable in executable_images
        ]
    return []


def _system_load_path(value: str) -> bool:
    if not value.startswith(("/usr/lib/", "/System/Library/")):
        return False
    normalized = os.path.normpath(value)
    return normalized == value and not normalized.endswith("/")


def macho_checks(install: Path, target) -> dict:
    """Inspect every shipped Mach-O and resolve its product load paths."""
    install = Path(install).resolve()
    images: list[Path] = []
    universal_images: list[str] = []
    malformed_images: list[str] = []
    for path in install.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as handle:
                magic = handle.read(4)
        except OSError as error:
            malformed_images.append(f"{path.relative_to(install)}: {error}")
            continue
        if magic in _FAT_MACHO_MAGICS:
            universal_images.append(str(path.relative_to(install)))
        elif macho.is_macho(path):
            images.append(path)

    bad_arch: list[str] = []
    bad_platform: list[str] = []
    bad_minos: list[str] = []
    bad_deps: list[str] = []
    unresolved_deps: list[str] = []
    bad_rpaths: list[str] = []
    unresolved_rpaths: list[str] = []
    bad_signature: list[str] = []
    missing_version: list[str] = []
    non_pie_executables: list[str] = []
    executable_images: list[Path] = []
    for image in images:
        try:
            if macho.read_header(image).kind == "execute":
                executable_images.append(image)
        except macho.MachOError:
            continue
    required_images = (
        PRODUCT.interpreter(install),
        install / "lib" / f"lib{PRODUCT.shared_library_stem}.dylib",
    )
    missing_images = [
        str(image.relative_to(install))
        for image in required_images
        if image not in images
    ]

    for image in images:
        name = str(image.relative_to(install))
        try:
            header = macho.read_header(image)
            if header.kind == "execute" and not header.pie:
                non_pie_executables.append(name)
            version_commands = [
                command for command in macho.load_commands(image)
                if command.cmd in (macho.LC_BUILD_VERSION, macho.LC_VERSION_MIN_MACOSX)
            ]
            version = macho.build_version(image) if len(version_commands) == 1 else None
            dependencies = macho.dependencies(image)
            rpaths = macho.rpaths(image)
        except macho.MachOError as error:
            malformed_images.append(f"{name}: {error}")
            continue
        if header.arch != target.machine or not header.is_64:
            bad_arch.append(f"{name}: {header.arch}")
        if version is None:
            missing_version.append(name)
        else:
            if version.platform != macho.PLATFORM_MACOS:
                bad_platform.append(f"{name}: {version.platform_name}")
            if version.minos != target.deployment_target:
                bad_minos.append(
                    f"{name}: expected {target.deployment_target}, got {version.minos}"
                )

        for rpath in rpaths:
            if rpath.startswith(("@loader_path", "@executable_path")):
                directories = _rpath_directories(
                    rpath, image, install, executable_images
                )
                if not directories or any(directory is None for directory in directories):
                    bad_rpaths.append(f"{name}: {rpath}")
                elif not all(directory.is_dir() for directory in directories if directory):
                    unresolved_rpaths.append(f"{name}: {rpath}")
            elif _system_load_path(rpath):
                if not Path(rpath).is_dir():
                    unresolved_rpaths.append(f"{name}: {rpath}")
            else:
                bad_rpaths.append(f"{name}: {rpath}")

        for dependency in dependencies:
            if dependency.startswith("@rpath/"):
                suffix = dependency.removeprefix("@rpath/")
                if not suffix or Path(suffix).is_absolute() or ".." in Path(suffix).parts:
                    bad_deps.append(f"{name} -> {dependency}")
                    continue
                directories = [
                    directory
                    for rpath in rpaths
                    for directory in _rpath_directories(
                        rpath, image, install, executable_images
                    )
                ]
                candidates = [
                    (directory / suffix).resolve()
                    for directory in directories
                    if directory is not None and directory.is_dir()
                ]
                if not any(_inside(candidate, install) and candidate.is_file()
                           for candidate in candidates):
                    unresolved_deps.append(f"{name} -> {dependency}")
            elif dependency.startswith("@loader_path/"):
                resolved = _resolve_image_path(dependency, image, install)
                if resolved is None:
                    bad_deps.append(f"{name} -> {dependency}")
                elif not resolved.is_file():
                    unresolved_deps.append(f"{name} -> {dependency}")
            elif dependency.startswith("@executable_path/"):
                resolved = [
                    _resolve_image_path(dependency, image, install, executable)
                    for executable in executable_images
                ]
                if not resolved or any(path is None for path in resolved):
                    bad_deps.append(f"{name} -> {dependency}")
                elif not all(path.is_file() for path in resolved if path is not None):
                    unresolved_deps.append(f"{name} -> {dependency}")
            elif _system_load_path(dependency):
                continue
            else:
                bad_deps.append(f"{name} -> {dependency}")
        valid, detail = macho.signature_status(image)
        if not valid:
            bad_signature.append(f"{name}: {detail[:120]}")

    failures = (
        universal_images or malformed_images or bad_arch or bad_platform or bad_minos
        or bad_deps or unresolved_deps or bad_rpaths or unresolved_rpaths
        or bad_signature or missing_version or missing_images
        or non_pie_executables
    )
    return {
        "image_count": len(images),
        "missing_product_images": missing_images,
        "universal_images": universal_images,
        "malformed_images": malformed_images,
        "arch_mismatches": bad_arch,
        "platform_mismatches": bad_platform,
        "minos_mismatches": bad_minos,
        "unexpected_dependencies": bad_deps,
        "unresolved_dependencies": unresolved_deps,
        "unexpected_rpaths": bad_rpaths,
        "unresolved_rpaths": unresolved_rpaths,
        "missing_build_version": missing_version,
        "non_pie_executables": non_pie_executables,
        "invalid_signatures": bad_signature,
        "ok": not failures,
    }


def version_and_abi(python: Path, target, *, cwd: Path | None = None) -> dict:
    """The exact version, ABI, and package tags."""
    code = """
        import json, sys, sysconfig, platform
        print(json.dumps({
            "version": list(sys.version_info[:3]),
            "version_string": sys.version.split()[0],
            "platform": sys.platform,
            "machine": platform.machine(),
            "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag,
            "python_platform": sysconfig.get_platform(),
            "soabi": sysconfig.get_config_var("SOABI"),
            "ext_suffix": sysconfig.get_config_var("EXT_SUFFIX"),
            "gil_enabled": sys._is_gil_enabled(),
            "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
            "lto": "--with-lto" in str(sysconfig.get_config_var("CONFIG_ARGS") or ""),
            # configure always substitutes PGO_PROF_USE_FLAG; it is only
            # *used* when optimizations are enabled, so the flag's presence
            # says nothing. The configure argument is the real signal.
            "pgo": "--enable-optimizations" in str(
                sysconfig.get_config_var("CONFIG_ARGS") or ""),
            "tail_call": str(sysconfig.get_config_var("PY_TAIL_CALL_INTERP") or "0").strip()
                         not in ("", "0", "None"),
            "shared_libpython": bool(sysconfig.get_config_var("Py_ENABLE_SHARED")),
            "INSTSONAME": sysconfig.get_config_var("INSTSONAME"),
            "config_args": sysconfig.get_config_var("CONFIG_ARGS"),
        }))
    """
    result = _python_code(python, code, cwd=cwd)
    if result.returncode != 0:
        raise ValidationError(f"interpreter failed: {result.stderr.strip()}")
    info = json.loads(result.stdout.strip().splitlines()[-1])
    expected_version = list(PRODUCT.version)
    if target.is_macos:
        expected_soabi = f"cpython-{PRODUCT.abi_tag[2:]}-darwin"
        expected_platform = f"macosx-{target.deployment_target}-{target.machine}"
        expected_instsoname = f"lib{PRODUCT.shared_library_stem}.dylib"
    else:
        expected_soabi = (
            f"cpython-{PRODUCT.abi_tag[2:]}-{target.machine}-linux-musl"
        )
        expected_platform = f"linux-{target.machine}"
        expected_instsoname = f"lib{PRODUCT.shared_library_stem}.so.1.0"
    expected_ext_suffix = f".{expected_soabi}.so"
    info["expected_version"] = expected_version
    info["expected_soabi"] = expected_soabi
    info["expected_ext_suffix"] = expected_ext_suffix
    info["expected_platform"] = expected_platform
    info["expected_instsoname"] = expected_instsoname
    info["version_ok"] = info["version"] == expected_version
    info["arch_ok"] = info["machine"] == target.machine
    info["abi_ok"] = bool(
        info["implementation"] == "cpython"
        and info["cache_tag"] == PRODUCT.abi_tag.replace("cp", "cpython-")
        and info["soabi"] == expected_soabi
        and info["ext_suffix"] == expected_ext_suffix
        and info["python_platform"] == expected_platform
    )
    info["shared_library_ok"] = bool(
        info["shared_libpython"] and info["INSTSONAME"] == expected_instsoname
    )
    info["platform_tag"] = f"{info['platform']}-{info['machine']}"
    ldf = sysconfig_text = None
    flag_result = _python_code(
        python,
        "import sysconfig;print((sysconfig.get_config_var('LDFLAGS') or '')"
        "+ '|' + (sysconfig.get_config_var('CFLAGS') or ''))",
        cwd=cwd,
    )
    if flag_result.returncode == 0:
        sysconfig_text, _, ldf = flag_result.stdout.strip().partition("|")
    info["sysconfig_ldflags"] = sysconfig_text
    info["sysconfig_cflags"] = ldf
    info["ok"] = bool(
        info["version_ok"] and info["arch_ok"] and info["abi_ok"]
        and info["shared_library_ok"]
        and info["platform"] == ("darwin" if target.is_macos else "linux")
        and info["gil_enabled"] and not info["free_threaded"] and info["lto"]
        and info["pgo"] is target.pgo
        and not info["tail_call"]
    )
    return info


def module_inventory(python: Path, *, cwd: Path | None = None) -> dict:
    """What imports, what does not, and what must not."""
    code = f"""
        import importlib, importlib.util, json, os, sys
        required = {list(REQUIRED_MODULES)!r}
        excluded = {list(EXCLUDED_MODULES)!r}
        missing = []
        outside_install = []
        for name in required:
            try:
                module = importlib.import_module(name)
                origin = getattr(getattr(module, "__spec__", None), "origin", None)
                if origin in ("built-in", "frozen"):
                    continue
                if origin is None:
                    outside_install.append(f"{{name}}: no module origin")
                else:
                    root = os.path.realpath(sys.prefix)
                    resolved = os.path.realpath(origin)
                    if os.path.commonpath((resolved, root)) != root:
                        outside_install.append(f"{{name}}: {{origin}}")
            except Exception as error:
                missing.append(f"{{name}}: {{type(error).__name__}}: {{error}}")
        leaked = [n for n in excluded
                  if importlib.util.find_spec(n) is not None]
        print(json.dumps({{"missing_required": missing, "excluded_present": leaked,
                          "outside_install": outside_install}}))
    """
    result = _python_code(python, code, cwd=cwd)
    if result.returncode != 0:
        raise ValidationError(f"module probe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout.strip().splitlines()[-1])
    data["required_count"] = len(REQUIRED_MODULES)
    data["excluded_count"] = len(EXCLUDED_MODULES)
    data["ok"] = not (
        data["missing_required"] or data["excluded_present"] or data["outside_install"]
    )
    return data


def capability_checks(python: Path, *, cwd: Path | None = None) -> dict:
    """Round trips through the bundled and platform-backed modules."""
    code = """
        import _decimal, bz2, dbm, dbm.ndbm, decimal, hashlib, json, lzma, os
        import sqlite3, tempfile, uuid, zlib
        import compression.zstd
        out = {}
        out["zlib"] = zlib.decompress(zlib.compress(b"a" * 4096)) == b"a" * 4096
        out["bz2"] = bz2.decompress(bz2.compress(b"b" * 4096)) == b"b" * 4096
        out["lzma"] = lzma.decompress(lzma.compress(b"c" * 4096)) == b"c" * 4096
        out["zstd"] = compression.zstd.decompress(
            compression.zstd.compress(b"d" * 4096)) == b"d" * 4096
        out["sha256"] = hashlib.sha256(b"abc").hexdigest() == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        out["sqlite"] = sqlite3.sqlite_version
        out["sqlite_roundtrip"] = False
        with tempfile.TemporaryDirectory() as d:
            con = sqlite3.connect(os.path.join(d, "x.db"))
            con.execute("create table t(a)")
            con.execute("insert into t values (?)", (42,))
            out["sqlite_roundtrip"] = con.execute("select a from t").fetchone() == (42,)
            con.close()
        out["decimal"] = (
            str(decimal.Decimal(2).sqrt())[:9],
            decimal.Decimal is _decimal.Decimal,
        )
        out["uuid4"] = len(uuid.uuid4().hex) == 32
        out["ndbm_roundtrip"] = False
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n")
            with dbm.ndbm.open(p, "c") as db:
                db[b"k"] = b"v"
            with dbm.ndbm.open(p, "r") as db:
                out["ndbm_roundtrip"] = db[b"k"] == b"v"
            out["whichdb"] = dbm.whichdb(p)
        out["dbm_default"] = None
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "d")
            with dbm.open(p, "c") as db:
                db[b"k"] = b"v"
            with dbm.open(p, "r") as db:
                out["dbm_default"] = (dbm._defaultmod.__name__, db[b"k"] == b"v")
        print(json.dumps(out))
    """
    result = _python_code(python, code, cwd=cwd)
    if result.returncode != 0:
        raise ValidationError(f"capability probe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout.strip().splitlines()[-1])
    data["ok"] = all(
        value for key, value in data.items()
        if key not in ("sqlite", "decimal", "whichdb", "dbm_default")
    ) and data["decimal"][1]
    return data


def mimalloc_allocator_checks(
    python: Path, install: Path, workdir: Path, target
) -> dict:
    """The bundled mimalloc allocator is configured and statically linked."""
    code = """
        import json, sysconfig
        print(json.dumps({
            "with_mimalloc": sysconfig.get_config_var("WITH_MIMALLOC"),
            "config_args": sysconfig.get_config_var("CONFIG_ARGS"),
        }))
    """
    result = _python_code(python, code, cwd=workdir)
    if result.returncode != 0:
        raise ValidationError(f"allocator probe failed: {result.stderr.strip()}")
    info = json.loads(result.stdout.strip().splitlines()[-1])
    configured = (
        info["with_mimalloc"] == 1
        and "--with-mimalloc" in str(info["config_args"] or "")
    )
    domain = _python_code(
        python, "pass", cwd=workdir, environment_overrides={"PYTHONMALLOC": "mimalloc"}
    )
    domain_ok = domain.returncode == 0
    install = Path(install).resolve()
    if target.is_macos:
        images = [
            PRODUCT.interpreter(install),
            install / "lib" / f"lib{PRODUCT.shared_library_stem}.dylib",
        ]
        linked = [
            dependency
            for image in images
            for dependency in macho.dependencies(image)
        ]
    else:
        images = [
            PRODUCT.interpreter(install),
            install / "lib" / f"lib{PRODUCT.shared_library_stem}.so.1.0",
        ]
        linked = []
        for image in images:
            dynamic = subprocess.run(
                [ELF_READER, "-dW", str(image)],
                capture_output=True, text=True, check=True,
            ).stdout
            linked.extend(
                match.group(1)
                for line in dynamic.splitlines()
                if "(NEEDED)" in line
                for match in [re.search(r"\[([^\]]+)\]", line)]
                if match is not None
            )
    static = not any("mimalloc" in str(library).lower() for library in linked)
    return {
        "configured": configured,
        "allocator_domain": domain_ok,
        "statically_linked": static,
        "ok": bool(configured and domain_ok and static),
    }


def multiprocessing_checks(python: Path, *, cwd: Path | None = None) -> dict:
    """Real worker processes, not just `import multiprocessing`.

    `SemLock` is absent when POSIX semaphores are disabled at configure time,
    which happens silently if the probe cannot run — so this exercises a queue
    and a pool rather than trusting the import.
    """
    code = """
        import json, multiprocessing as mp
        out = {}
        try:
            from _multiprocessing import SemLock
            out["semlock"] = True
        except ImportError as error:
            out["semlock"] = False
            out["semlock_error"] = str(error)
        ctx = mp.get_context("spawn")
        try:
            queue = ctx.Queue()
            queue.put(41)
            out["queue"] = queue.get(timeout=30) == 41
        except Exception as error:
            out["queue"] = False
            out["queue_error"] = f"{type(error).__name__}: {error}"
        try:
            with ctx.Pool(2) as pool:
                out["pool"] = pool.map(abs, [-1, -2, -3]) == [1, 2, 3]
        except Exception as error:
            out["pool"] = False
            out["pool_error"] = f"{type(error).__name__}: {error}"
        print(json.dumps(out))
    """
    result = _python_code(python, code, cwd=cwd)
    if result.returncode != 0:
        raise ValidationError(f"multiprocessing probe failed: {result.stderr.strip()[-300:]}")
    data = json.loads(result.stdout.strip().splitlines()[-1])
    data["ok"] = bool(data["semlock"] and data["queue"] and data["pool"])
    return data


def terminal_checks(python: Path, install: Path, *, cwd: Path | None = None) -> dict:
    """Curses, panel, readline and terminfo after relocation."""
    code = """
        import json, os, sys
        out = {}
        try:
            import curses, curses.panel
            version = curses.version
            out["curses_version"] = (
                version.decode("ascii", "replace") if isinstance(version, bytes)
                else str(version))
            out["has_termios"] = True
            # Deliberately no has_colors()/COLORS: those require initscr() and
            # a real terminal, which a non-interactive validation run does not
            # have. Asserting the module loads and reports its ABI is the
            # check that is meaningful without a TTY; the terminal-session
            # behaviour is covered by CPython's own test_curses, which is
            # resource-gated for exactly this reason.
            out["panel_module"] = curses.panel.__name__
        except Exception as error:
            out["curses_error"] = f"{type(error).__name__}: {error}"
        try:
            import readline
            out["readline_backend"] = readline.__doc__.split()[0] if readline.__doc__ else "?"
            out["readline_parse"] = True
        except Exception as error:
            out["readline_error"] = f"{type(error).__name__}: {error}"
        try:
            import termios, tty
            out["termios"] = True
        except Exception as error:
            out["termios_error"] = f"{type(error).__name__}: {error}"
        out["terminfo_dirs"] = [d for d in (
            os.environ.get("TERMINFO_DIRS", "").split(":")
            + ["/usr/share/terminfo", "/etc/terminfo"]
        ) if d and os.path.isdir(d)]
        print(json.dumps(out))
    """
    result = _python_code(python, code, cwd=cwd)
    if result.returncode != 0:
        raise ValidationError(f"terminal probe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout.strip().splitlines()[-1])
    data["ok"] = not any(key.endswith("_error") for key in data)
    return data


def _external_compiler(target) -> str:
    if target.is_macos:
        return str(load_macos_toolchain(ROOT / "bootstrap.lock.json").llvm_prefix / "bin" / "clang")
    compiler = shutil.which("clang")
    if compiler is None:
        raise ValidationError("locked Linux clang is unavailable")
    return compiler


def _compiler_environment(install: Path, workdir: Path, target) -> dict[str, str]:
    overrides = None
    compiler = Path(_external_compiler(target)).resolve()
    if target.is_macos:
        toolchain = load_macos_toolchain(ROOT / "bootstrap.lock.json")
        overrides = {"SDKROOT": str(toolchain.sdkroot)}
    environment = product_environment(install, workdir, overrides)
    environment["PATH"] = os.pathsep.join((str(compiler.parent), os.defpath))
    return environment


def _compiler_run(command: list[str], install: Path, workdir: Path, target):
    return _run(
        command,
        cwd=workdir,
        env=_compiler_environment(install, workdir, target),
        timeout=180,
    )


def ctypes_checks(python: Path, install: Path, workdir: Path, target) -> dict:
    """ctypes in both directions, with no build tooling present."""
    helper_c = workdir / "helper.c"
    helper_suffix = ".dylib" if target.is_macos else ".so"
    helper_dylib = workdir / f"libfixture_helper{helper_suffix}"
    helper_c.write_text("int helper_answer(void) { return 7; }\n")
    build_command = [_external_compiler(target), "-fPIC", "-shared", "-o", str(helper_dylib), str(helper_c)]
    if target.is_macos:
        build_command.extend(["-install_name", f"@rpath/{helper_dylib.name}"])
    else:
        build_command.append(f"-Wl,-soname,{helper_dylib.name}")
    build = _compiler_run(build_command, install, workdir, target)
    if build.returncode != 0:
        raise ValidationError(f"helper dylib build failed: {build.stderr.strip()}")

    code = f"""
        import ctypes, ctypes.util, json, os, sys
        out = {{}}
        libc = ctypes.CDLL(None)
        out["cdll_none"] = ctypes.c_int(0).value == 0
        strlen = libc.strlen
        strlen.argtypes = [ctypes.c_char_p]
        strlen.restype = ctypes.c_size_t
        out["libc_symbol"] = strlen(b"hello") == 5
        CMP = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        calls = []
        def compare(a, b):
            calls.append(1)
            return (ctypes.cast(a, ctypes.POINTER(ctypes.c_int))[0]
                    - ctypes.cast(b, ctypes.POINTER(ctypes.c_int))[0])
        libc.qsort.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                               ctypes.c_size_t, CMP]
        data = (ctypes.c_int * 5)(5, 3, 1, 4, 2)
        libc.qsort(data, 5, ctypes.sizeof(ctypes.c_int), CMP(compare))
        out["callback_used"] = bool(calls)
        out["callback_sorted"] = list(data) == [1, 2, 3, 4, 5]
        out["find_library_c"] = ctypes.util.find_library("c")
        out["find_library_system"] = ctypes.util.find_library("System")
        helper = ctypes.CDLL({str(helper_dylib)!r})
        out["private_dylib_answer"] = helper.helper_answer()
        print(json.dumps(out))
    """
    result = _python_code(python, code, cwd=workdir)
    if result.returncode != 0:
        raise ValidationError(f"ctypes probe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout.strip().splitlines()[-1])
    data["ok"] = bool(
        data["cdll_none"] and data["libc_symbol"] and data["callback_used"]
        and data["callback_sorted"] and data["private_dylib_answer"] == 7
    )
    return data


def tls_checks(python: Path, workdir: Path) -> dict:
    """TLS verification in both directions against local fixtures.

    Verification is never disabled to make a test pass: the success case
    establishes a *narrow* trust anchor containing exactly the fixture's own
    certificate, and the failure case uses the system default context, which
    must reject it.
    """
    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    cert, key = fixtures / "localhost.crt", fixtures / "localhost.key"
    if not cert.is_file() or not key.is_file():
        return {"ok": False, "skipped": "TLS fixtures missing", "fixtures": str(fixtures)}
    code = f"""
        import json, os, socket, ssl, subprocess, sys, tempfile, threading
        cert, key = {str(cert)!r}, {str(key)!r}
        out = {{}}
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        received = {{}}

        stop = threading.Event()
        server.settimeout(10)

        def serve():
            # One connection per probe: the failure cases abort during the
            # handshake, so a single-accept server would refuse the later
            # probes and make them look like product failures.
            while not stop.is_set():
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                try:
                    with ctx.wrap_socket(conn, server_side=True) as tls:
                        tls.recv(16)
                        tls.sendall(b"ok")
                except Exception:
                    pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        # Failure first: the system trust store must reject a self-signed cert.
        try:
            ssl.create_default_context().wrap_socket(
                socket.create_connection(("127.0.0.1", port), timeout=5),
                server_hostname="localhost")
            out["untrusted_rejected"] = False
        except ssl.SSLCertVerificationError as error:
            out["untrusted_rejected"] = True
            out["untrusted_error"] = str(error)[:80]
        except Exception as error:
            out["untrusted_rejected"] = False
            out["untrusted_error"] = f"{{type(error).__name__}}: {{error}}"

        # Success: a context anchored at exactly this certificate.
        anchor = ssl.create_default_context(cafile=cert)
        anchor.check_hostname = True
        try:
            with anchor.wrap_socket(
                    socket.create_connection(("127.0.0.1", port), timeout=5),
                    server_hostname="localhost") as tls:
                tls.sendall(b"ping")
                out["trusted_accepted"] = tls.recv(16) == b"ok"
        except Exception as error:
            out["trusted_accepted"] = False
            out["trusted_error"] = f"{{type(error).__name__}}: {{error}}"

        # Hostname mismatch must also fail, with the anchor in place.
        try:
            mismatched = ssl.create_default_context(cafile=cert)
            ssl.SSLContext.wrap_socket(
                mismatched,
                socket.create_connection(("127.0.0.1", port), timeout=5),
                server_hostname="wrong.example")
            out["hostname_mismatch_rejected"] = False
        except ssl.SSLCertVerificationError:
            out["hostname_mismatch_rejected"] = True
        except Exception as error:
            out["hostname_mismatch_rejected"] = False
            out["hostname_error"] = f"{{type(error).__name__}}: {{error}}"

        default = ssl.create_default_context()
        out["system_trust_roots"] = len(default.get_ca_certs()) if hasattr(default, "get_ca_certs") else None
        out["openssl"] = ssl.OPENSSL_VERSION
        stop.set()
        server.close()
        thread.join(timeout=5)
        print(json.dumps(out))
    """
    result = _python_code(python, code, cwd=workdir)
    if result.returncode != 0:
        raise ValidationError(f"TLS probe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout.strip().splitlines()[-1])
    data["ok"] = bool(
        data.get("untrusted_rejected") and data.get("trusted_accepted")
        and data.get("hostname_mismatch_rejected")
    )
    return data


def extension_checks(python: Path, install: Path, workdir: Path, target) -> dict:
    """Build and load a native extension with no installer present.

    Flags come from the interpreter's own advertised configuration
    (`python3-config`), which is the contract a consumer actually uses —
    not from anything the build happened to know.
    """
    config = PRODUCT.config_tool(install)
    if not config.is_file():
        return {"ok": False, "error": f"{config.name} not installed"}
    cflags = _product_run([config, "--cflags"], install, workdir)
    ldflags = _product_run([config, "--ldflags", "--embed"], install, workdir)
    if cflags.returncode != 0 or ldflags.returncode != 0:
        raise ValidationError(f"{config.name} failed")
    if "build/prefix" in (cflags.stdout + ldflags.stdout):
        return {"ok": False, "error": f"{config.name} leaks the builder prefix"}

    source = workdir / "fixture_ext.c"
    source.write_text(textwrap.dedent("""
        #define PY_SSIZE_T_CLEAN
        #include <Python.h>

        static PyObject *answer(PyObject *self, PyObject *args) {
            return PyLong_FromLong(42);
        }
        static PyObject *call_back(PyObject *self, PyObject *fn) {
            return PyObject_CallFunction(fn, "i", 5);
        }
        static PyMethodDef methods[] = {
            {"answer", answer, METH_NOARGS, "the answer"},
            {"call_back", call_back, METH_O, "invoke a python callable"},
            {NULL, NULL, 0, NULL}
        };
        static struct PyModuleDef module = {
            PyModuleDef_HEAD_INIT, "fixture_ext",
            "build fixture", -1, methods
        };
        PyMODINIT_FUNC PyInit_fixture_ext(void) {
            return PyModule_Create(&module);
        }
    """))
    extension = workdir / "fixture_ext.so"
    build = _compiler_run(
        [_external_compiler(target), "-O2", "-fPIC", "-shared", *shlex.split(cflags.stdout),
         str(source), "-o", str(extension), *shlex.split(ldflags.stdout)],
        install, workdir, target,
    )
    if build.returncode != 0:
        return {"ok": False, "error": f"extension build failed: {build.stderr.strip()[:400]}"}

    probe = _product_run([python, "-I", "-B", "-c", textwrap.dedent(f"""
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("fixture_ext", {str(extension)!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print(json.dumps({{
            "answer": mod.answer(),
            "callback": mod.call_back(lambda n: n * 2),
            "suffix_matches": {str(extension)!r}.endswith(".so"),
        }}))
    """)], install, workdir)
    if probe.returncode != 0:
        return {"ok": False, "error": f"extension import failed: {probe.stderr.strip()[:400]}"}
    data = json.loads(probe.stdout.strip().splitlines()[-1])
    suffix = _product_run(
        [python, "-I", "-B", "-c", "import sysconfig;print(sysconfig.get_config_var('EXT_SUFFIX'))"],
        install,
        workdir,
    )
    data["installed_suffix"] = suffix.stdout.strip()
    data["cflags"] = cflags.stdout.strip()
    data["ok"] = data["answer"] == 42 and data["callback"] == 10
    return data


def abi3_checks(python: Path, install: Path, workdir: Path, target) -> dict:
    """A limited-API extension, which is the ABI-stability fixture."""
    config = PRODUCT.config_tool(install)
    cflags = _product_run([config, "--cflags"], install, workdir)
    source = workdir / "abi3_ext.c"
    source.write_text(textwrap.dedent("""
        #define Py_LIMITED_API 0x030C0000
        #include <Python.h>

        static PyObject *triple(PyObject *self, PyObject *arg) {
            long value = PyLong_AsLong(arg);
            return PyLong_FromLong(value * 3);
        }
        static PyMethodDef methods[] = {
            {"triple", triple, METH_O, "triple a number"},
            {NULL, NULL, 0, NULL}
        };
        static struct PyModuleDef module = {
            PyModuleDef_HEAD_INIT, "abi3_ext", NULL, -1, methods
        };
        PyMODINIT_FUNC PyInit_abi3_ext(void) {
            return PyModule_Create(&module);
        }
    """))
    extension = workdir / "abi3_ext.so"
    build_command = [
        _external_compiler(target), "-O2", "-fPIC", "-shared",
        *shlex.split(cflags.stdout), str(source), "-o", str(extension),
    ]
    if target.is_macos:
        build_command.extend(["-undefined", "dynamic_lookup"])
    build = _compiler_run(build_command, install, workdir, target)
    if build.returncode != 0:
        return {"ok": False, "error": f"abi3 build failed: {build.stderr.strip()[:400]}"}
    probe = _product_run([python, "-I", "-B", "-c", textwrap.dedent(f"""
        import importlib.util, json
        spec = importlib.util.spec_from_file_location("abi3_ext", {str(extension)!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print(json.dumps({{"triple": mod.triple(14)}}))
    """)], install, workdir)
    if probe.returncode != 0:
        return {"ok": False, "error": f"abi3 import failed: {probe.stderr.strip()[:400]}"}
    data = json.loads(probe.stdout.strip().splitlines()[-1])
    data["ok"] = data["triple"] == 42
    return data


def embedding_checks(python: Path, install: Path, workdir: Path, target) -> dict:
    """Link and run a C embedder against the shipped libpython."""
    config = PRODUCT.config_tool(install)
    if not config.is_file():
        return {"ok": False, "error": f"{config.name} not installed"}
    cflags = _product_run([config, "--cflags"], install, workdir)
    ldflags = _product_run([config, "--ldflags", "--embed"], install, workdir)
    source = workdir / "embed.c"
    source.write_text(textwrap.dedent("""
        #include <Python.h>
        int main(void) {
            Py_Initialize();
            int rc = PyRun_SimpleString(
                "import sys, ssl;"
                "print('embedded', sys.version.split()[0],"
                " ssl.OPENSSL_VERSION.split()[1])");
            Py_Finalize();
            return rc != 0;
        }
    """))
    binary = workdir / "embed"
    libdir = install / "lib"
    build = _compiler_run(
        [_external_compiler(target), "-O2", *shlex.split(cflags.stdout), str(source),
         "-o", str(binary), f"-Wl,-rpath,{libdir}", *shlex.split(ldflags.stdout)],
        install, workdir, target,
    )
    if build.returncode != 0:
        return {"ok": False, "error": f"embed build failed: {build.stderr.strip()[:400]}"}
    # The prefix of a relocatable build is derived from the executable's
    # location, not compiled in, so an embedder sitting outside the tree has
    # to be told where the tree is. Both documented recipes are exercised:
    # PYTHONHOME, and placement inside the install's own bin/.
    run = _product_run(
        [binary],
        install,
        workdir,
        environment_overrides={"PYTHONHOME": str(Path(install).resolve())},
    )
    in_tree = Path(install) / "bin" / "embed-validation"
    shutil.copy2(binary, in_tree)
    try:
        placed = _product_run([in_tree], install, workdir)
    finally:
        in_tree.unlink(missing_ok=True)
    data = {
        "ldflags": ldflags.stdout.strip(),
        "output": run.stdout.strip(),
        "returncode": run.returncode,
        "pythonhome_recipe_ok": run.returncode == 0 and f"embedded {PRODUCT.version_string}" in run.stdout,
        "in_tree_placement_ok": placed.returncode == 0
        and f"embedded {PRODUCT.version_string}" in placed.stdout,
        "requirement": (
            "A relocatable libpython derives its prefix from the running "
            "executable, so an embedder outside the install tree must set "
            "PYTHONHOME (or be placed inside it). An absolute-prefix build "
            "does not have this requirement; this is the cost of the "
            "distribution being movable, and it is stated rather than "
            "discovered."
        ),
    }
    data["ok"] = bool(data["pythonhome_recipe_ok"] and data["in_tree_placement_ok"])
    if not data["ok"]:
        data["stderr"] = (run.stderr or placed.stderr).strip()[:400]
    return data


def relocation_checks(install: Path, workdir: Path, target) -> dict:
    """Run the moved tree, its public config tools, and a new extension build."""
    destination = (workdir / "moved prefix with spaces").resolve()
    if destination.exists():
        shutil.rmtree(destination)
    copy_product_tree(Path(install).resolve(), destination)
    python = PRODUCT.interpreter(destination)
    relocation_probe = textwrap.dedent("""
        import ctypes, json, os, ssl, sys, sysconfig
        libdir = os.path.join(sys.prefix, "lib")
        shared = ctypes.CDLL(os.path.join(libdir,
                                          sysconfig.get_config_var("INSTSONAME")))
        root = os.path.realpath(sys.prefix)
        path_keys = (
            "BINDIR", "BINLIBDEST", "LIBDIR", "LIBPL", "DESTSHARED",
            "INCLUDEPY", "CONFINCLUDEPY", "LIBDEST", "EXENAME", "prefix",
            "exec_prefix", "base", "base_prefix",
        )
        paths = {key: sysconfig.get_config_var(key) for key in path_keys}
        paths = {key: value for key, value in paths.items() if value}
        def is_inside(path):
            return os.path.commonpath((root, os.path.realpath(path))) == root
        sysconfig_paths = sysconfig.get_paths()
        path_rooted = all(is_inside(value) for value in sysconfig_paths.values())
        path_rooted = path_rooted and all(is_inside(value) for value in paths.values())
        print(json.dumps({
            "executable": sys.executable,
            "prefix": sys.prefix,
            "version": sys.version.split()[0],
            "openssl": ssl.OPENSSL_VERSION.split()[1],
            "libpython_loaded": shared is not None,
            "ldflags_leak": "build/prefix" in (sysconfig.get_config_var("LDFLAGS") or ""),
            "sysconfig_paths": paths,
            "sysconfig_paths_rooted": path_rooted,
            "sysconfig_string_values": {
                key: value for key, value in sysconfig.get_config_vars().items()
                if isinstance(value, str)
            },
        }))
    """)
    probe = _product_run(
        [python, "-I", "-B", "-c", relocation_probe], destination, workdir
    )
    if probe.returncode != 0:
        return {"ok": False, "error": probe.stderr.strip()[:400]}
    data = json.loads(probe.stdout.strip().splitlines()[-1])
    data["stale_sysconfig_values"] = stale_sysconfig_values(
        data.pop("sysconfig_string_values")
    )
    data["moved_to"] = str(destination)
    config = PRODUCT.config_tool(destination)
    config_prefix = _product_run([config, "--prefix"], destination, workdir)
    configdir = _product_run([config, "--configdir"], destination, workdir)
    cflags = _product_run([config, "--cflags"], destination, workdir)
    ldflags = _product_run([config, "--ldflags", "--embed"], destination, workdir)
    include_flags = shlex.split(cflags.stdout) if cflags.returncode == 0 else []
    link_flags = shlex.split(ldflags.stdout) if ldflags.returncode == 0 else []
    data["config_script_paths"] = {
        "prefix": config_prefix.stdout.strip(),
        "configdir": configdir.stdout.strip(),
        "cflags": cflags.stdout.strip(),
        "ldflags": ldflags.stdout.strip(),
        "ok": (
            config_prefix.returncode == 0
            and Path(config_prefix.stdout.strip()).resolve() == destination
            and configdir.returncode == 0
            and Path(configdir.stdout.strip()).resolve().is_relative_to(destination)
            and cflags.returncode == 0
            and f"-I{PRODUCT.headers(destination)}" in include_flags
            and ldflags.returncode == 0
            and f"-L{destination / 'lib'}" in link_flags
        ),
    }
    stale_values = data["stale_sysconfig_values"]
    moved_extension_work = workdir / "relocated extension build"
    moved_extension_work.mkdir(parents=True, exist_ok=True)
    moved_extension = extension_checks(python, destination, moved_extension_work, target)
    data["extension_build_after_move"] = moved_extension
    data["ok"] = (
        data["prefix"] == str(destination)
        and data["version"] == PRODUCT.version_string
        and not data["ldflags_leak"]
        and data["sysconfig_paths_rooted"]
        and not stale_values
        and data["config_script_paths"]["ok"]
        and moved_extension.get("ok", False)
    )
    return data


def script_launcher_checks(install: Path, workdir: Path) -> dict:
    """The installed launchers must select *this* interpreter after moving."""
    destination = (workdir / "launcher prefix").resolve()
    if destination.exists():
        shutil.rmtree(destination)
    copy_product_tree(install, destination)
    results = {}
    for name in (f"pydoc{PRODUCT.major_minor}", PRODUCT.config_name):
        script = destination / "bin" / name
        if not script.is_file():
            results[name] = {"ok": False, "error": "not installed"}
            continue
        if name.endswith("-config"):
            run = _product_run([script, "--prefix"], destination, workdir)
        else:
            run = _product_run([script, "-h"], destination, workdir)
        results[name] = {
            "ok": run.returncode == 0,
            "output": (run.stdout or run.stderr).strip()[:120],
        }
    return {"ok": all(v["ok"] for v in results.values()), "launchers": results}


def run_validation(install: Path, workdir: Path, target) -> dict:
    """Run every check against an installed tree; return a report."""
    install = Path(install).resolve()
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    python = PRODUCT.interpreter(install)

    report: dict = {"install": str(install), "checks": {}}
    checks = report["checks"]

    def record(name: str, function, *args) -> None:
        try:
            checks[name] = function(*args)
        except Exception as error:  # noqa: BLE001
            checks[name] = {
                "ok": False,
                "error_type": type(error).__name__,
                "error": f"{type(error).__name__}: {error}",
            }

    if target.is_macos:
        record("macho", macho_checks, install, target)
    record("version_and_abi", lambda: version_and_abi(python, target, cwd=workdir))
    record("mimalloc", mimalloc_allocator_checks, python, install, workdir, target)
    record("module_inventory", lambda: module_inventory(python, cwd=workdir))
    record("capabilities", lambda: capability_checks(python, cwd=workdir))
    record("multiprocessing", lambda: multiprocessing_checks(python, cwd=workdir))
    record("terminal", lambda: terminal_checks(python, install, cwd=workdir))
    record("ctypes", ctypes_checks, python, install, workdir, target)
    record("tls", tls_checks, python, workdir)
    record("extension", extension_checks, python, install, workdir, target)
    record("abi3", abi3_checks, python, install, workdir, target)
    record("embedding", embedding_checks, python, install, workdir, target)
    record("relocation", relocation_checks, install, workdir, target)
    record("launchers", script_launcher_checks, install, workdir)
    def exclusion_checks() -> dict:
        present = verify_exclusions(install)
        importable = probe_in_process(python, cwd=workdir)
        return {
            "present": present,
            "importable": importable,
            "ok": not present and not any(importable.values()),
        }

    record("exclusions", exclusion_checks)

    required_checks = {
        "version_and_abi", "mimalloc", "module_inventory", "capabilities",
        "multiprocessing", "terminal", "ctypes", "tls", "extension",
        "abi3", "embedding", "relocation", "launchers", "exclusions",
    }
    if target.is_macos:
        required_checks.add("macho")
    failed = sorted(
        name for name in required_checks
        if not isinstance(checks.get(name), dict) or checks[name].get("ok") is not True
    )
    failed.extend(sorted(name for name in checks if name not in required_checks))
    failed = sorted(set(failed))
    aggregate = {
        "check_count": len(checks),
        "required_check_count": len(required_checks),
        "failed": failed,
        "ok": len(checks) == len(required_checks) and not failed,
    }
    report["aggregate"] = aggregate
    report["failed"] = failed
    report["ok"] = aggregate["ok"]
    return report


def write_report(report: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
