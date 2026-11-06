"""Qualify and publish an artifact from the staged install tree."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from buildsys import macho
from buildsys.bootstrap import load_macos_toolchain
from buildsys.ca_bundle import CABundleError, install_fallback_ca_bundle
from buildsys.elf import (
    dynamic_relocation_report,
    dynamic_tags,
    forbidden_runtime_libraries,
    full_relro_report,
    gnu_stack_report,
    hardening_report,
    linkage_report,
    machine_report,
    pie_report,
    writable_executable_loads_report,
)
from buildsys.generations import active_generation, publish_generation
from buildsys.inputs import InputError, load_lock
from buildsys.package import (
    PackageError,
    build_manifest,
    create_archive,
    qualify,
    validate_build_record,
    verify_archive_extraction,
    write_manifest,
)
from buildsys.patches import PatchError, apply_patch
from buildsys.product import PRODUCT
from buildsys.product_process import run_product_process
from buildsys.relocate import find_elfs
from buildsys.scope import ScopeError, prune_distribution_payload
from buildsys.standalone_compat import (
    CompatibilityError,
    run_standalone_compatibility,
    testcapi_modules_pre_prune,
)
from buildsys.targets import Target, native_target
from buildsys.testsuite import (
    SuiteError,
    classify,
    report_payload,
    run_suite,
    verify_excluded_failures,
)
from buildsys.validate_product import ValidationError, run_validation

TARGET_DESCRIPTION = native_target()
TARGET = TARGET_DESCRIPTION.triple
REVISION = "r2"
ARCHIVE_NAME = PRODUCT.local_archive_name(TARGET, REVISION)

class PackagingError(Exception):
    """The staged tree could not be qualified and published."""


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, **kwargs, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise PackagingError(f"could not run {command}: {error}") from error
    if result.returncode != 0:
        raise PackagingError(f"{command}: {result.stderr or result.stdout}")
    return result


def strip_tree(install: Path, *, strip: str = "llvm-strip") -> list[Path]:
    stripped = []
    for elf in find_elfs(install):
        _run([strip, "--strip-unneeded", str(elf)])
        stripped.append(elf)
    return stripped


def strip_tree_macos(install: Path) -> list[Path]:
    locked = load_macos_toolchain(REPO / "bootstrap.lock.json")
    strip = locked.llvm_prefix / "bin" / "llvm-strip"
    stripped = []
    for image in macho.find_machos(install):
        _run([str(strip), "--strip-debug", str(image)])
        macho.sign_adhoc(image)
        stripped.append(image)
    return stripped


def require_macos_pgo_recipe() -> dict:
    recipe_path = REPO / "build" / "logs" / "build-record.json"
    try:
        recipe = json.loads(recipe_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PackagingError(f"macOS PGO build recipe is missing or invalid: {error}") from error
    optimization = recipe.get("optimization", {})
    jobs = optimization.get("pgo_jobs")
    if recipe.get("target") != TARGET or not optimization.get("pgo"):
        raise PackagingError("staged macOS build has no matching PGO recipe")
    if type(jobs) is not int or jobs < 1:
        raise PackagingError("macOS PGO recipe has no valid worker count")
    from buildsys.cpython import CPYTHON_PGO_HASH_SEED, pgo_profile_task

    if optimization.get("profile_task") != pgo_profile_task(jobs):
        raise PackagingError("macOS PGO recipe has an unexpected PROFILE_TASK")
    if recipe.get("environment", {}).get("PYTHONHASHSEED") != CPYTHON_PGO_HASH_SEED:
        raise PackagingError("macOS PGO recipe has no fixed Python hash seed")
    profile_data = optimization.get("profile_data", {})
    profile_hash = profile_data.get("sha256")
    profile_size = profile_data.get("size_bytes")
    if (
        profile_data.get("path") != "code.profclangd"
        or not isinstance(profile_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", profile_hash)
        or type(profile_size) is not int
        or profile_size < 1
    ):
        raise PackagingError("macOS PGO recipe has no valid merged profile receipt")
    if not Path(optimization.get("llvm_profdata", "")).is_file():
        raise PackagingError("macOS PGO recipe's llvm-profdata is not present")
    source = recipe.get("source", {})
    cpython = [entry for entry in load_lock(REPO / "sources.lock.json") if entry.name == "cpython"]
    if len(cpython) != 1:
        raise PackagingError("source lock must contain exactly one CPython pin")
    pin = cpython[0]
    if (source.get("name"), source.get("version"), source.get("sha256")) != (
        pin.name, pin.version, pin.sha256
    ):
        raise PackagingError("macOS PGO recipe does not match the locked CPython source")
    if "-fno-omit-frame-pointer" in recipe.get("environment", {}).get("CFLAGS", ""):
        raise PackagingError("macOS PGO recipe unexpectedly retains frame pointers")
    return recipe


def install_ca_fallback(install: Path) -> dict[str, object]:
    ssl_module = PRODUCT.stdlib(install) / "ssl.py"
    if not ssl_module.is_file():
        raise PackagingError(f"installed ssl module is missing: {ssl_module}")
    if "_PYTHON_BUILD_DEFAULT_CA_BUNDLE" not in ssl_module.read_text():
        patch = REPO / "patches" / "package" / "0001-ssl-default-ca-bundle-fallback.patch"
        try:
            apply_patch(ssl_module.parent, patch, strip=2)
        except PatchError as error:
            raise PackagingError(f"cannot enable the locked CA fallback: {error}") from error
    try:
        return install_fallback_ca_bundle(
            install, cache_root=REPO / ".cache", lock_path=REPO / "sources.lock.json"
        )
    except CABundleError as error:
        raise PackagingError(str(error)) from error


def _linux_validation(install: Path) -> dict:
    checks: dict[str, dict] = {}
    python = PRODUCT.interpreter(install)
    imports = run_product_process(
        [str(python), "-I", "-B", "-c", "import ssl, sqlite3, dbm, compression.zstd"],
        install, install, timeout_seconds=30,
    )
    checks["smoke_import"] = {
        "ok": imports.returncode == 0,
        "output": (imports.stdout + imports.stderr).strip(),
    }

    elfs = find_elfs(install)
    if not elfs:
        raise PackagingError("packaged tree contains no ELF files")
    forbidden = {}
    stack_violations = []
    pie_violations = []
    relro_violations = {}
    writable_executable_violations = {}
    textrel_violations = {}
    executable_pie_reports = {}
    elf_report = []
    machine_violations = []
    linkage_violations = []
    loader_violations = []
    interpreter_paths = {}
    system_libraries = {f"libc.musl-{TARGET_DESCRIPTION.machine}.so.1"}
    interpreter = PRODUCT.interpreter(install)
    interpreter_relative = str(interpreter.relative_to(install))
    for elf in elfs:
        relative = str(elf.relative_to(install))
        header = _run(["readelf", "-hW", str(elf)]).stdout
        machine = machine_report(header, TARGET_DESCRIPTION.machine)
        if not machine["ok"]:
            machine_violations.append(relative)
        dynamic = _run(["readelf", "-dW", str(elf)]).stdout
        tags = dynamic_tags(dynamic)
        needed = tags["needed"]
        if rejected := forbidden_runtime_libraries([*needed, *tags["soname"]]):
            forbidden[relative] = rejected
        search_paths = [
            item
            for encoded in (*tags["rpath"], *tags["runpath"])
            for item in encoded.split(":")
        ]
        linkage = linkage_report(
            install, elf, needed=needed, runpaths=search_paths,
            system_libraries=system_libraries,
        )
        if not linkage["ok"]:
            linkage_violations.append(relative)
        program_headers = _run(["readelf", "-lW", str(elf)]).stdout
        stack = gnu_stack_report(program_headers)
        if not stack["ok"]:
            stack_violations.append(relative)
        relro = full_relro_report(program_headers, dynamic)
        if not relro["ok"]:
            relro_violations[relative] = relro
        load_permissions = writable_executable_loads_report(program_headers)
        if not load_permissions["ok"]:
            writable_executable_violations[relative] = load_permissions
        dynamic_relocations = dynamic_relocation_report(dynamic)
        if not dynamic_relocations["ok"]:
            textrel_violations[relative] = dynamic_relocations
        requested = re.search(
            r"Requesting program interpreter:\s*([^\]]+)", program_headers
        )
        pie = pie_report(header)
        executable = (
            requested is not None
            or relative == interpreter_relative
            or bool(elf.stat().st_mode & 0o111)
            or pie["elf_type"] == "ET_EXEC"
        )
        if executable:
            executable_pie_reports[relative] = pie
            if not pie["ok"]:
                pie_violations.append(relative)
        if requested is not None:
            path = requested.group(1).strip()
            interpreter_paths[relative] = path
            if path != TARGET_DESCRIPTION.musl_loader:
                loader_violations.append(relative)
        elf_report.append({
            "path": str(elf.relative_to(install)),
            "machine": machine,
            "needed": needed,
            "linkage": linkage,
            "gnu_stack": stack,
            "full_relro": relro,
            "writable_executable_loads": load_permissions,
            "dynamic_relocations": dynamic_relocations,
        })
    platform_libraries = sorted({
        library
        for report in elf_report
        for library in report["needed"]
        if library in system_libraries
    })
    checks["elf_linkage"] = {
        "ok": not forbidden and not linkage_violations,
        "forbidden_libraries": forbidden,
        "unresolved": linkage_violations,
        "platform_libraries": platform_libraries,
        "elf_report": elf_report,
    }
    checks["elf_machine"] = {
        "ok": not machine_violations,
        "violations": machine_violations,
        "expected": TARGET_DESCRIPTION.machine,
    }
    checks["elf_stack"] = {
        "ok": not stack_violations,
        "violations": stack_violations,
    }
    checks["elf_pie"] = {
        "ok": interpreter_relative in executable_pie_reports and not pie_violations,
        "interpreter": interpreter_relative,
        "reports": executable_pie_reports,
        "violations": pie_violations,
    }
    checks["elf_relro"] = {
        "ok": not relro_violations,
        "violations": relro_violations,
    }
    checks["elf_writable_executable"] = {
        "ok": not writable_executable_violations,
        "violations": writable_executable_violations,
    }
    checks["elf_text_relocations"] = {
        "ok": not textrel_violations,
        "violations": textrel_violations,
    }

    actual_loader = interpreter_paths.get(interpreter_relative)
    checks["elf_interpreters"] = {
        "ok": actual_loader == TARGET_DESCRIPTION.musl_loader and not loader_violations,
        "actual": actual_loader,
        "expected": TARGET_DESCRIPTION.musl_loader,
        "violations": loader_violations,
        "all_interpreter_paths": interpreter_paths,
    }
    checks["musl_loader"] = {
        "ok": actual_loader == TARGET_DESCRIPTION.musl_loader,
        "actual": actual_loader,
        "expected": TARGET_DESCRIPTION.musl_loader,
    }
    sysconfig = run_product_process(
        [str(interpreter), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_config_var('LDFLAGS') or '')"],
        install, install, timeout_seconds=30,
    )
    checks["sysconfig_paths"] = {
        "ok": sysconfig.returncode == 0 and "build/prefix" not in sysconfig.stdout,
        "output": (sysconfig.stdout + sysconfig.stderr).strip(),
    }
    configured = run_product_process([
        str(interpreter), "-I", "-B", "-c",
        ("import json,sysconfig; print(json.dumps({k: sysconfig.get_config_var(k) "
        "for k in ('CFLAGS','CFLAGS_NODIST','PY_CFLAGS','PY_CFLAGS_NODIST','CPPFLAGS')}))"),
    ], install, install, timeout_seconds=30)
    if configured.returncode != 0:
        raise PackagingError(
            "product could not report ELF hardening flags: "
            + (configured.stderr or configured.stdout).strip()
        )
    flag_values = json.loads(configured.stdout.strip().splitlines()[-1])
    installed_library = run_product_process([
        str(interpreter), "-I", "-B", "-c",
        "import sysconfig; print(sysconfig.get_config_var('INSTSONAME') or '')",
    ], install, install, timeout_seconds=30)
    if installed_library.returncode != 0 or not installed_library.stdout.strip():
        raise PackagingError("product did not identify its shared libpython")
    library_name = installed_library.stdout.strip()
    if Path(library_name).name != library_name:
        raise PackagingError(f"product reported an unsafe shared libpython name: {library_name!r}")
    libpython = install / "lib" / library_name
    checks["binary_inventory"] = {
        "ok": interpreter.is_file() and libpython.is_file(),
        "interpreter": str(interpreter.relative_to(install)),
        "shared_library": str(libpython.relative_to(install)),
    }
    symbols = {
        "interpreter": _run(["readelf", "--dyn-syms", "-W", str(interpreter)]).stdout,
        "libpython": _run(["readelf", "--dyn-syms", "-W", str(libpython)]).stdout,
    }
    hardening = hardening_report(
        " ".join(value for value in flag_values.values() if isinstance(value, str)),
        symbols,
    )
    checks["elf_hardening"] = hardening
    failed = sorted(name for name, result in checks.items() if result.get("ok") is not True)
    return {"ok": not failed, "failed": failed, "checks": checks}


def _source_revision(build_record: dict) -> str:
    revision = build_record.get("source_revision")
    if not isinstance(revision, str):
        raise PackagingError("build record has no source revision")
    requested_revision = os.environ.get("GITHUB_SHA")
    if requested_revision and requested_revision != revision:
        raise PackagingError(
            f"build record revision {revision} does not match GITHUB_SHA {requested_revision}"
        )
    if (REPO / ".git").exists():
        current = _run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout.strip()
        if current != revision:
            raise PackagingError(
                f"build record revision {revision} does not match checkout {current}"
            )
    return revision


def _config_prefix_argument(config_args: str) -> tuple[int, int, str] | None:
    first = len(config_args) - len(config_args.lstrip())
    if first == len(config_args):
        return None
    if config_args[first] in {"'", '"'}:
        quote = config_args[first]
        start = first + 1
        end = config_args.find(quote, start)
        if end < 0 or (
            end + 1 < len(config_args)
            and not config_args[end + 1].isspace()
        ):
            return None
    else:
        start = first
        end = next(
            (
                index
                for index in range(start, len(config_args))
                if config_args[index].isspace()
            ),
            len(config_args),
        )
    argument = config_args[start:end]
    if not argument.startswith("--prefix="):
        return None
    return start, end, argument


def _effective_flags_match_record(
    actual: object, expected: object, install: Path
) -> bool:
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    if actual.keys() != expected.keys():
        return False
    actual_config = actual.get("CONFIG_ARGS")
    expected_config = expected.get("CONFIG_ARGS")
    if not isinstance(actual_config, str) or not isinstance(expected_config, str):
        return actual == expected
    actual_prefix = _config_prefix_argument(actual_config)
    expected_prefix = _config_prefix_argument(expected_config)
    if actual_prefix is None or expected_prefix is None:
        return False
    actual_start, actual_end, actual_argument = actual_prefix
    _, _, expected_argument = expected_prefix
    if actual_argument != f"--prefix={install.resolve()}":
        return False
    normalized_config = (
        actual_config[:actual_start]
        + expected_argument
        + actual_config[actual_end:]
    )
    return (
        normalized_config == expected_config
        and {key: value for key, value in actual.items() if key != "CONFIG_ARGS"}
        == {key: value for key, value in expected.items() if key != "CONFIG_ARGS"}
    )


def _llvm_version(record: dict, lock_path: Path, target: Target) -> str:
    try:
        document = json.loads(lock_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PackagingError(f"cannot read bootstrap lock: {error}") from error
    if target.is_macos:
        llvm_version = document["macos_toolchain"]["llvm"]["version"]
        actual = record.get("toolchain", {}).get("llvm_version")
    else:
        direct_packages = document["linux_toolchain"]["direct_packages"]
        clang_versions = [
            package["version"].split("-r", 1)[0]
            for package in direct_packages
            if package.get("name") == "clang23"
        ]
        if len(clang_versions) != 1:
            raise PackagingError("bootstrap lock has no unique Linux clang23 package")
        llvm_version = clang_versions[0]
        version_output = record.get("toolchain", {}).get("commands", {}).get("cc", {}).get("version", "")
        match = re.search(r"clang version ([0-9]+\.[0-9]+\.[0-9]+)", version_output)
        if match is None:
            raise PackagingError("build record has no identifiable Linux LLVM version")
        actual = match.group(1)
    if actual != llvm_version:
        raise PackagingError(
            f"build record LLVM version {actual!r} does not match bootstrap {llvm_version}"
        )
    return llvm_version


def _verify_install_matches_record(install: Path, record: dict) -> None:
    interpreter = PRODUCT.interpreter(install)
    probe = run_product_process([
        str(interpreter), "-I", "-B", "-c",
        ("import json,sysconfig; keys=('CFLAGS','CFLAGS_NODIST','PY_CFLAGS',"
        "'PY_CFLAGS_NODIST','LDFLAGS','LTOFLAGS','CONFIG_ARGS'); "
        "print(json.dumps({key:sysconfig.get_config_var(key) for key in keys}))"),
    ], install, install, timeout_seconds=30)
    if probe.returncode != 0:
        raise PackagingError(
            "staged interpreter could not report effective configuration: "
            + probe.stderr.strip()
        )
    output = [line for line in probe.stdout.splitlines() if line.strip()]
    if not output:
        raise PackagingError("staged interpreter returned no effective configuration")
    try:
        actual = json.loads(output[-1])
    except json.JSONDecodeError as error:
        raise PackagingError(
            f"staged interpreter returned invalid effective configuration: {error}"
        ) from error
    if not _effective_flags_match_record(
        actual, record["effective_flags"], install
    ):
        raise PackagingError("staged install configuration does not match its build record")


def _regression_suite(install: Path, workdir: Path) -> dict:
    suite_tree = workdir / "regression-copy"
    shutil.copytree(install, suite_tree, symlinks=True)
    suite_python = PRODUCT.interpreter(suite_tree)

    def fresh_python() -> Path:
        retry_tree = workdir / "regression-retry"
        if retry_tree.exists():
            shutil.rmtree(retry_tree)
        shutil.copytree(install, retry_tree, symlinks=True)
        return PRODUCT.interpreter(retry_tree)

    suite = run_suite(suite_python)
    solo = verify_excluded_failures(
        suite_python,
        suite["failed_files"],
        fresh_python=fresh_python,
        target=TARGET_DESCRIPTION,
    )
    suite["classification"] = classify(solo, target=TARGET_DESCRIPTION)
    report = report_payload(suite, target=TARGET_DESCRIPTION)
    report["solo_findings"] = {
        name: finding["failures"] for name, finding in solo.items()
    }
    return report


def _qualification(
    install: Path,
    workdir: Path,
    target: Target,
    *,
    trust_store: dict,
    distribution_scope: dict,
    regression_suite: dict,
    pre_prune_testcapi: dict,
) -> dict:
    validation = run_validation(install, workdir / "product-validation", target)
    if not target.is_macos:
        linux = _linux_validation(install)
        validation["checks"].update(linux["checks"])
    validation["checks"]["pre_prune_testcapi"] = pre_prune_testcapi
    validation["checks"].update({
        "trust_store": {
            **trust_store,
            "ok": trust_store.get("ca_certificates", 0) > 0,
        },
        "distribution_scope": distribution_scope,
    })
    validation["checks"]["regression_suite"] = regression_suite
    failed = sorted(
        name for name, check in validation["checks"].items()
        if check.get("ok") is not True
    )
    validation["failed"] = failed
    validation["aggregate"] = {
        "check_count": len(validation["checks"]),
        "required_check_count": len(validation["checks"]),
        "failed": failed,
        "ok": not failed,
    }
    validation["ok"] = not failed
    compatibility = run_standalone_compatibility(
        PRODUCT.interpreter(install), install, target, skip_testcapi=True
    )
    return qualify(validation, compatibility)


def _package() -> Path:
    stage = REPO / "build" / "stage" / "cpython-staged" / "install"
    if not stage.is_dir():
        raise PackagingError("no staged install; run `build` first")
    build_record = validate_build_record(
        stage.parent / "build-record.json",
        product=PRODUCT,
        target=TARGET_DESCRIPTION,
        lock_path=REPO / "sources.lock.json",
        bootstrap_lock_path=REPO / "bootstrap.lock.json",
        patches_directory=REPO / "patches" / "cpython",
    )
    if TARGET_DESCRIPTION.pgo:
        require_macos_pgo_recipe()
    source_revision = _source_revision(build_record)
    archive_name = ARCHIVE_NAME

    scratch_root = REPO / ".work" / "tmp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    output = REPO / "dist" / TARGET
    with tempfile.TemporaryDirectory(prefix="package-", dir=scratch_root) as temporary:
        workdir = Path(temporary)
        install = workdir / "install"
        shutil.copytree(stage, install, symlinks=True)
        _verify_install_matches_record(install, build_record)
        trust_store = install_ca_fallback(install)
        regression_suite = _regression_suite(install, workdir)
        try:
            pre_prune_testcapi = testcapi_modules_pre_prune(
                PRODUCT.interpreter(install), install
            )
        except (CompatibilityError, OSError, subprocess.SubprocessError) as error:
            pre_prune_testcapi = {
                "status": "failed",
                "ok": False,
                "error": str(error),
            }
        if TARGET_DESCRIPTION.is_macos:
            strip_tree_macos(install)
        else:
            strip_tree(install)
        try:
            distribution_scope = prune_distribution_payload(install)
        except ScopeError as error:
            raise PackagingError(str(error)) from error
        archive_temp = workdir / "artifact.tar.gz"
        facts = create_archive(install, archive_temp)
        extracted = verify_archive_extraction(
            install, archive_temp, workdir / "archive-check"
        )
        qualification = _qualification(
            extracted,
            workdir,
            TARGET_DESCRIPTION,
            trust_store=trust_store,
            distribution_scope=distribution_scope,
            regression_suite=regression_suite,
            pre_prune_testcapi=pre_prune_testcapi,
        )
        if not qualification["ok"]:
            failures = sorted(
                name for name, check in qualification["checks"].items()
                if check.get("status") == "failed"
            )
            raise PackagingError("qualification failed: " + ", ".join(failures))
        bootstrap_identity = build_record["bootstrap_identity"]
        llvm_version = _llvm_version(
            build_record, REPO / "bootstrap.lock.json", TARGET_DESCRIPTION
        )
        recipe_identity = build_record["recipe_identity"]
        manifest = build_manifest(
            product=PRODUCT,
            target=TARGET_DESCRIPTION,
            source_revision=source_revision,
            recipe_identity=recipe_identity,
            bootstrap_identity=bootstrap_identity,
            llvm_version=llvm_version,
            archive_filename=archive_name,
            archive=facts,
            qualification=qualification,
            lock_path=REPO / "sources.lock.json",
            build_record=build_record,
            package_patches_directory=REPO / "patches" / "package",
        )
        manifest_temp = workdir / "artifact.json"
        write_manifest(manifest_temp, manifest)
        publish_generation(output, {
            archive_name: archive_temp,
            "artifact.json": manifest_temp,
        })
    return active_generation(output) / archive_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify and package the staged interpreter")
    parser.add_argument("--target", default=TARGET)
    arguments = parser.parse_args(argv)
    try:
        if arguments.target != TARGET:
            raise PackagingError(
                f"--target {arguments.target} does not match this runtime target {TARGET}"
            )
        archive = _package()
    except (
        PackagingError, PackageError, InputError, ScopeError, CompatibilityError,
        ValidationError, SuiteError,
    ) as error:
        print(f"FAIL package: {error}")
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"FAIL package: {type(error).__name__}: {error}")
        return 1
    print(f"OK    package -> {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
