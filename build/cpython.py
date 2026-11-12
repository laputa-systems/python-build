"""Build the locked CPython release against the private prefix.

The staged installation lands in build/stage; the relocation decision
(destining a neutral prefix rather than the checkout) belongs to packaging.

The relocation step is per-format: Linux rewrites ELF rpaths with patchelf,
and macOS rewrites Mach-O install names and adds `@loader_path` search paths.
Both run immediately after `make install`, so later phases see a relocatable
tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from buildsys import macho
from buildsys.bootstrap import (
    BootstrapError,
    load_macos_toolchain,
    toolchain_for,
)
from buildsys.cpython import (
    configuration,
    resolve_build_jobs,
    resolve_pgo_jobs,
)
from buildsys.deporder import dependency_order
from buildsys.inputs import Cache, InputError, locked_inputs, pin_identity, safe_extract
from buildsys.patches import PatchError, apply_patch_set
from buildsys.product import PRODUCT
from buildsys.product_process import run_product_process
from buildsys.recipes import BuildError, run
from buildsys.relocate import RelocationError, macho_relocate, relocate
from buildsys.scope import (
    ScopeError,
    enforce_exclusions,
    enforced_module_names,
    probe_in_process,
    verify_exclusions,
)
from buildsys.targets import Target, UnsupportedTargetError, native_target

CONFIGURE_PREFIX = "/install"
JOBS = str(resolve_build_jobs())


def _tool_version(command: str, env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True,
            env=env, timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BuildError(f"cannot identify build tool {command}: {error}") from error
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not output:
        raise BuildError(f"cannot identify build tool {command}: {output}")
    return output.splitlines()[0]


def verify_deployment_floor(install: Path, target: Target) -> dict:
    """Every shipped image must declare the target's macOS floor.

    An unset `-mmacosx-version-min` silently inherits the SDK's version, which
    is newer than the declared floor and would make the artifact refuse to run
    on macOS releases the manifest claims. This is checked rather than
    assumed because the failure is invisible until someone runs it.
    """
    report: dict[str, object] = {
        "expected_minos": target.deployment_target,
        "images": {},
        "mismatches": [],
    }
    images = report["images"]
    mismatches = report["mismatches"]
    for image in macho.find_machos(install):
        version = macho.build_version(image)
        name = str(image.relative_to(install))
        if version is None:
            mismatches.append(f"{name}: no LC_BUILD_VERSION")
            continue
        images[name] = {"minos": version.minos, "sdk": version.sdk}
        if version.minos != target.deployment_target:
            mismatches.append(f"{name}: minos {version.minos} != {target.deployment_target}")
    return report


def build(
    work: Path,
    prefix: Path,
    stage: Path,
    logs: Path,
    cache: Cache,
    *,
    pgo_jobs: int | None = None,
) -> Path:
    target = native_target()
    recipe_path = logs / "build-record.json"
    recipe_path.unlink(missing_ok=True)
    if pgo_jobs is not None and not target.pgo:
        raise BuildError("--pgo-jobs requires a target with PGO enabled")
    resolved_pgo_jobs = resolve_pgo_jobs(pgo_jobs) if target.pgo else None
    staged = stage / "cpython-staged"
    if staged.exists():
        shutil.rmtree(staged)
    pins = locked_inputs(REPO / "sources.lock.json")
    pin = next(entry for entry in pins if entry.name == "cpython")
    if pin.version != PRODUCT.version_string:
        raise BuildError("the CPython source lock and product definition disagree")
    blob = cache.require(pin)
    # safe_extract refuses an existing destination, so clear our own scratch
    # rather than making the caller do it before every rebuild.
    if (work / "cpython").exists():
        shutil.rmtree(work / "cpython")
    source = safe_extract(blob, work / "cpython") / f"Python-{pin.version}"
    applied_patches = apply_patch_set(source, REPO / "patches" / "cpython")
    build_directory = work / "cpython-build"
    if build_directory.exists():
        shutil.rmtree(build_directory)
    build_directory.mkdir(parents=True)
    toolchain = toolchain_for(target, REPO / "bootstrap.lock.json")
    profile_toolchain = (
        load_macos_toolchain(REPO / "bootstrap.lock.json")
        if target.is_macos else None
    )
    args, env_overrides = configuration(
        prefix, target, pgo_jobs=resolved_pgo_jobs
    )
    if target.pgo:
        # CPython's configure probe can find Apple's generic llvm-profdata,
        # which may not match the locked clang bitcode. Name the profiler from
        # the same digest-pinned official LLVM prefix as CC.
        assert profile_toolchain is not None
        env_overrides["LLVM_PROFDATA"] = str(profile_toolchain.llvm_profdata)
    env = toolchain.env(
        {
            **env_overrides,
            "SOURCE_DATE_EPOCH": "1704067200",
            "PKG_CONFIG": "pkg-config",
        }
    )
    source_revision = env.get("PYTHON_BUILD_SOURCE_REVISION") or env.get("GITHUB_SHA")
    if not source_revision:
        source_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True,
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    if env.get("GITHUB_SHA") and env["GITHUB_SHA"] != source_revision:
        raise BuildError(
            f"checkout revision {source_revision} does not match GITHUB_SHA "
            f"{env['GITHUB_SHA']}"
        )
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_revision):
        raise BuildError(f"invalid source revision {source_revision!r}")
    env["CFLAGS"] = env.get("CFLAGS", "") + f" -DPYB_SRC_REV='\"{source_revision[:12]}\"'"
    build_mode = env.get("PYTHON_BUILD_BUILD_MODE", "development")
    isolation_mechanism = env.get("PYTHON_BUILD_ISOLATION", "host-process")
    if build_mode not in {"development", "sealed"}:
        raise BuildError(f"unsupported build mode {build_mode!r}")
    if build_mode == "sealed" and isolation_mechanism == "host-process":
        raise BuildError("sealed builds must identify their enforced isolation boundary")
    configure = source / "configure"
    run(
        [str(configure), f"--prefix={CONFIGURE_PREFIX}", *args],
        cwd=build_directory,
        env=env,
        log=logs / "cpython-configure.log",
    )
    run(
        [toolchain.make, "-j", JOBS],
        cwd=build_directory,
        env=env,
        log=logs / "cpython-make.log",
    )
    staged.parent.mkdir(parents=True, exist_ok=True)
    run(
        [toolchain.make, "install", f"DESTDIR={staged}"],
        cwd=build_directory,
        env=env,
        log=logs / "cpython-install.log",
    )
    install = staged / CONFIGURE_PREFIX.lstrip("/")
    builder_paths = [prefix, source, build_directory]
    command_paths = [
        Path(path)
        for path in (
            toolchain.cc, toolchain.cxx, toolchain.ar, toolchain.ranlib,
            toolchain.ld, toolchain.nm, toolchain.strip, toolchain.make,
        )
        if path
    ]
    if target.is_macos:
        assert profile_toolchain is not None
        builder_paths.extend((profile_toolchain.llvm_prefix, profile_toolchain.sdkroot))
        command_paths.extend((profile_toolchain.llvm_profdata, profile_toolchain.pkgconf))
        macho_relocate(
            install,
            prefix,
            configure_prefix=CONFIGURE_PREFIX,
            builder_paths=builder_paths,
            command_paths=command_paths,
        )
        floor = verify_deployment_floor(install, target)
        (logs / "minos.json").write_text(
            json.dumps(floor, indent=2, sort_keys=True) + "\n"
        )
        if floor["mismatches"]:
            raise RelocationError(
                "deployment floor mismatch: " + "; ".join(floor["mismatches"][:5])
            )
    else:
        relocate(
            install,
            prefix,
            configure_prefix=CONFIGURE_PREFIX,
            builder_paths=builder_paths,
            command_paths=command_paths,
        )
    # Scope enforcement runs after relocation so the shebang rewrite cannot
    # resurrect a launcher that was meant to be removed, and before anything
    # downstream treats the tree as shippable.
    report = enforce_exclusions(install)
    (logs / "scope.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    remaining = verify_exclusions(install)
    if remaining:
        raise ScopeError("excluded components still present: " + ", ".join(remaining))
    importable = probe_in_process(
        PRODUCT.interpreter(install), modules=enforced_module_names()
    )
    leaked = sorted(name for name, present in importable.items() if present)
    if leaked:
        raise ScopeError(f"excluded modules are still importable: {', '.join(leaked)}")
    profile_data = None
    optimization = {
        "pgo": target.pgo,
        "pgo_jobs": resolved_pgo_jobs,
        "profile_task": env.get("PROFILE_TASK"),
        "lto": "thin",
        "configure_args": args,
    }
    if target.pgo:
        profile_path = build_directory / "code.profclangd"
        try:
            profile_bytes = profile_path.read_bytes()
        except OSError as error:
            raise BuildError(
                f"CPython did not leave merged PGO data at {profile_path}: {error}"
            ) from error
        profile_data = {
            "path": profile_path.name,
            "sha256": hashlib.sha256(profile_bytes).hexdigest(),
            "size_bytes": len(profile_bytes),
        }
        optimization["profile_data"] = profile_data
        optimization["llvm_profdata"] = env["LLVM_PROFDATA"]

    effective_flags = run_product_process(
        [str(PRODUCT.interpreter(install)), "-I", "-B", "-c",
         ("import json,sysconfig; print(json.dumps({key: sysconfig.get_config_var(key) "
         "for key in ('CFLAGS','CFLAGS_NODIST','PY_CFLAGS','PY_CFLAGS_NODIST',"
         "'LDFLAGS','LTOFLAGS','CONFIG_ARGS')}))")],
        install,
        build_directory,
        timeout_seconds=30,
    )
    if effective_flags.returncode != 0:
        raise BuildError(
            "installed interpreter could not report effective compiler flags: "
            + effective_flags.stderr.strip()
        )
    config_flags = json.loads(effective_flags.stdout.strip().splitlines()[-1])
    patch_identities = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in applied_patches
    ]
    pin_by_name = {entry.name: entry for entry in pins}
    dependency_names = tuple(
        name for name in dependency_order(target) if name != "pkgconf"
    )
    missing_dependencies = sorted(set(dependency_names) - pin_by_name.keys())
    if missing_dependencies:
        raise BuildError(
            "missing locked dependency inputs: " + ", ".join(missing_dependencies)
        )
    dependency_inputs = [
        {
            "name": pin_by_name[name].name,
            "version": pin_by_name[name].version,
            "sha256": pin_by_name[name].sha256,
            "identity": pin_identity(pin_by_name[name]),
        }
        for name in dependency_names
    ]
    command_facts = {
        name: {"path": command, "version": _tool_version(command, env)}
        for name, command in (
            ("cc", toolchain.cc), ("cxx", toolchain.cxx),
            ("ar", toolchain.ar), ("ranlib", toolchain.ranlib),
            ("ld", toolchain.ld), ("nm", toolchain.nm),
            ("strip", toolchain.strip), ("make", toolchain.make),
        ) if command
    }
    bootstrap_bytes = (REPO / "bootstrap.lock.json").read_bytes()
    bootstrap_document = json.loads(bootstrap_bytes)
    bootstrap_identity = hashlib.sha256(bootstrap_bytes).hexdigest()
    if profile_toolchain is not None:
        toolchain_identity = profile_toolchain.identity()
        toolchain_observed = {
            **profile_toolchain.observed_facts(),
            "commands": command_facts,
        }
    else:
        linux_packages = bootstrap_document["linux_toolchain"]["direct_packages"]
        clang_versions = [
            package["version"].split("-r", 1)[0]
            for package in linux_packages
            if package["name"] == "clang23"
        ]
        if len(clang_versions) != 1:
            raise BuildError("bootstrap lock must identify one Linux clang23 version")
        toolchain_identity = {
            "family": target.family,
            "cpu_baseline": target.cpu_baseline_cflag,
            "llvm_version": clang_versions[0],
            "commands": {
                name: {"version": value["version"]}
                for name, value in command_facts.items()
            },
        }
        toolchain_observed = {"commands": command_facts}
    recipe_environment = {
        key: env[key] for key in (
            "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "PROFILE_TASK",
            "MACOSX_DEPLOYMENT_TARGET", "PYTHONHASHSEED",
        ) if key in env
    }
    recipe_material = {
        "target": target.triple,
        "product": PRODUCT.version_string,
        "source": {"name": pin.name, "version": pin.version, "sha256": pin.sha256},
        "patches": patch_identities,
        "dependencies": dependency_inputs,
        "configure_args": args,
        "environment": recipe_environment,
        "effective_flags": config_flags,
        "toolchain": toolchain_identity,
        "bootstrap_identity": bootstrap_identity,
    }
    recipe_identity = hashlib.sha256(
        json.dumps(recipe_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    source_dirty_value = env.get("PYTHON_BUILD_SOURCE_DIRTY")
    if source_dirty_value is None:
        source_dirty_value = "1" if subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO, check=True,
            capture_output=True, text=True, timeout=15,
        ).stdout.strip() else "0"
    if source_dirty_value not in {"0", "1"}:
        raise BuildError("PYTHON_BUILD_SOURCE_DIRTY must be 0 or 1")
    source_dirty = source_dirty_value == "1"
    build_record = {
        "schema_version": 1,
        "target": target.triple,
        "source_revision": source_revision,
        "source_dirty": source_dirty,
        "product": {"version": PRODUCT.version_string, "abi_tag": PRODUCT.abi_tag},
        "build_mode": build_mode,
        "isolation": {
            "mechanism": isolation_mechanism,
            "network": "disabled" if build_mode == "sealed" else "not-enforced",
        },
        "source": {"name": pin.name, "version": pin.version, "sha256": pin.sha256},
        "patches": patch_identities,
        "dependencies": dependency_inputs,
        "recipe_identity": recipe_identity,
        "bootstrap_identity": bootstrap_identity,
        "toolchain": toolchain_identity,
        "optimization": optimization,
        "environment": recipe_environment,
        "effective_flags": config_flags,
        "toolchain_observed": toolchain_observed,
        "commands": {
            "configure": [str(configure), f"--prefix={CONFIGURE_PREFIX}", *args],
            "build": [toolchain.make, "-j", JOBS],
            "install": [toolchain.make, "install", f"DESTDIR={staged}"],
        },
    }
    staged_record = staged / "build-record.json"
    staged_record.write_text(json.dumps(build_record, indent=2, sort_keys=True) + "\n")
    recipe_path.write_text(json.dumps(build_record, indent=2, sort_keys=True) + "\n")
    print(f"      removed {len(report['removed'])} excluded path(s)", flush=True)
    return staged


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the locked CPython source")
    parser.add_argument(
        "--pgo-jobs", type=_positive_int,
        help="number of workers for the macOS CPython --pgo training task "
             "(default: CPython compile jobs, one fewer than host CPUs)",
    )
    args = parser.parse_args(argv)
    work = REPO / "build" / "work"
    prefix = (REPO / "build" / "prefix").resolve()
    stage = REPO / "build" / "stage"
    logs = REPO / "build" / "logs"
    work.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    cache = Cache(REPO / ".cache")
    print(f"BUILD cpython {PRODUCT.version_string}", flush=True)
    try:
        staged = build(
            work, prefix, stage, logs, cache, pgo_jobs=args.pgo_jobs
        )
    except (BuildError, InputError, PatchError, BootstrapError,
            UnsupportedTargetError, RelocationError, ScopeError) as error:
        print(f"FAIL cpython: {error}")
        return 1
    print(f"OK    cpython -> {staged}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
