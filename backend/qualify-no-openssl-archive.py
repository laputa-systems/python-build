from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_layout import ARCHIVE_NAME, MANIFEST_NAME, REPORT_NAME


class QualificationDriverError(Exception):
    pass


def require_work_path(path: Path, name: str) -> Path:
    resolved = Path(path).resolve()
    work_root = (REPO / ".work").resolve()
    if not resolved.is_relative_to(work_root) or resolved == work_root:
        raise QualificationDriverError(f"{name} must be inside {work_root}: {resolved}")
    return resolved


def require_product_work_path(path: Path, name: str) -> Path:
    resolved = Path(path).resolve()
    work_roots = ((REPO / ".work").resolve(), (COMMON_ROOT / ".work").resolve())
    if not any(resolved.is_relative_to(root) and resolved != root for root in work_roots):
        raise QualificationDriverError(
            f"{name} must be inside a repository .work directory: {resolved}"
        )
    return resolved


def common_repository_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    root = Path(result.stdout.strip()).resolve().parent
    for name in ("sources.lock.json", "bootstrap.lock.json"):
        if (REPO / name).read_bytes() != (root / name).read_bytes():
            raise QualificationDriverError(
                f"{name} differs from the shared toolchain and input lock owner"
            )
    return root


COMMON_ROOT = common_repository_root()
sys.path.insert(0, str(COMMON_ROOT))

from buildsys.package import (
    PackageError,
    build_manifest,
    copy_product_tree,
    create_archive,
    payload_size_report,
    qualify,
    verify_archive_extraction,
    write_manifest,
)
from buildsys.product import PRODUCT
from buildsys.product_process import run_product_process
from buildsys.scope import ScopeError, prune_distribution_payload
from buildsys.standalone_compat import (
    run_standalone_compatibility,
    testcapi_modules_pre_prune,
)
from buildsys.targets import native_target
from buildsys.testsuite import _accounting as account_suite_log
from buildsys.testsuite import classify as classify_suite_findings
from buildsys.testsuite import report_payload
from buildsys.validate_product import run_validation, write_report

REQUIRED_CANDIDATE_FILES = (
    "bin/python3.15",
    "lib/python3.15/rtls/_rustls.abi3.so",
    "lib/python3.15/lib-dynload/_hashlib_graviola.abi3.so",
    "lib/python3.15/ssl.py",
    "lib/python3.15/_ssl.py",
    "lib/python3.15/_ssl_metadata.py",
    "lib/python3.15/_hashlib.py",
    "lib/python3.15/python-build-cacert.pem",
)
CERTIFICATE_CASE_COUNT = 82
CERTIFICATE_VERIFY_CODE_COUNT = 12
LINUX_ARCHITECTURES = {
    "aarch64-unknown-linux-musl": {
        "machine": "aarch64",
        "docker_arch": "arm64",
        "platform": "linux/arm64",
        "cpu_baseline": "-march=armv8-a+crypto",
        "cpu_features": {"aes", "pmull", "sha2", "asimd"},
        "rust_cpu": "generic",
        "rust_features": ("+aes", "+sha2", "+neon"),
        "bootstrap_lock": "linux-bootstrap.lock.json",
        "apk_arch": "aarch64",
    },
    "x86_64-unknown-linux-musl": {
        "machine": "x86_64",
        "docker_arch": "amd64",
        "platform": "linux/amd64",
        "cpu_baseline": "-march=broadwell",
        "cpu_features": {"aes", "pclmulqdq", "avx", "avx2", "bmi1", "bmi2", "fma", "movbe"},
        "rust_cpu": "broadwell",
        "rust_features": (),
        "bootstrap_lock": "linux-bootstrap-x86_64.json",
        "apk_arch": "x86_64",
    },
}


def linux_arch(target) -> dict:
    try:
        return LINUX_ARCHITECTURES[target.triple]
    except KeyError:
        raise QualificationDriverError(
            f"Linux candidate has no declared architecture floor: {target.triple}"
        )
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40,64}")
OPENSSL_SYMBOL_PREFIXES = (
    "AES_", "ASN1_", "ASYNC_", "BF_", "BIO_", "BN_", "BUF_", "CAMELLIA_",
    "CAST_", "CMS_", "COMP_", "CONF_", "CRYPTO_", "CT_", "DES_", "DH_",
    "DSA_", "DTLS_", "EC_", "ECDH_", "ECDSA_", "ENGINE_", "ERR_", "EVP_",
    "HMAC_", "IDEA_", "KDF_", "MD4_", "MD5_", "OCSP_", "OPENSSL_", "OSSL_",
    "PEM_", "PKCS", "RAND_", "RC2_", "RC4_", "RSA_", "SHA1_", "SHA224_",
    "SHA256_", "SHA384_", "SHA512_", "SRP_", "SSL_", "TLS_", "TS_", "UI_",
    "X509_", "X509V3_",
)
ADAPTER_EXPORT_IMAGES = frozenset(
    {
        "lib/python3.15/rtls/_rustls.abi3.so",
        "lib/python3.15/lib-dynload/_hashlib_graviola.abi3.so",
    }
)


def shared_library_relative_path(target) -> str:
    suffix = ".dylib" if target.is_macos else ".so.1.0"
    return f"lib/lib{PRODUCT.shared_library_stem}{suffix}"


def candidate_runtime_identity_paths(prefix: Path, target) -> tuple[str, ...]:
    root = Path(prefix).resolve(strict=True)
    package = root / "lib" / "python3.15" / "rtls"
    if not package.is_dir():
        raise QualificationDriverError(f"candidate RTLS package is missing: {package}")
    paths = set(REQUIRED_CANDIDATE_FILES)
    paths.add(shared_library_relative_path(target))
    for path in package.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise QualificationDriverError(f"candidate runtime identity contains a symlink: {relative}")
        if path.is_file():
            paths.add(relative.as_posix())
    missing = sorted(relative for relative in paths if not (root / relative).is_file())
    if missing:
        raise QualificationDriverError("candidate runtime identity is missing: " + ", ".join(missing))
    return tuple(sorted(paths))


def openssl_symbol_family(symbol: str) -> str | None:
    normalized = symbol.removeprefix("_")
    return next(
        (prefix for prefix in OPENSSL_SYMBOL_PREFIXES if normalized.startswith(prefix)),
        None,
    )


def audit_openssl_symbols(prefix: Path, target) -> dict:
    from buildsys import macho
    from buildsys.relocate import find_elfs

    nm = "/usr/bin/nm" if target.is_macos else shutil.which("nm")
    if nm is None:
        return check_result(False, error="native nm symbol tool is missing")
    if target.is_macos:
        images = macho.find_machos(prefix)
        image_format = "Mach-O"
    else:
        images = find_elfs(prefix)
        image_format = "ELF"
    if not images:
        return check_result(False, error=f"extracted archive contains no {image_format} images")
    unresolved = []
    adapter_exports = {}
    for image in images:
        relative = image.relative_to(prefix).as_posix()
        result = subprocess.run(
            [nm, "-g", str(image)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            return check_result(
                False,
                tool=str(nm),
                image=relative,
                error=(result.stdout + result.stderr)[-2000:],
            )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            symbol_type = fields[0] if len(fields) == 2 else fields[-2]
            symbol = fields[-1]
            family = openssl_symbol_family(symbol)
            if family is not None and symbol_type in {"U", "u", "w", "v"}:
                unresolved.append({"image": relative, "symbol": symbol, "family": family})
            if relative in ADAPTER_EXPORT_IMAGES and symbol_type not in {"U", "u", "w", "v"}:
                adapter_exports.setdefault(relative, []).append(symbol)
    return check_result(
        not unresolved,
        tool=nm,
        image_format=image_format,
        image_count=len(images),
        audited_symbol_families=list(OPENSSL_SYMBOL_PREFIXES),
        unresolved_openssl_symbols=unresolved,
        adapter_exports=adapter_exports,
    )


def load_research_driver():
    path = REPO / "backend" / "build-no-openssl.py"
    spec = importlib.util.spec_from_file_location("no_openssl_build_driver", path)
    if spec is None or spec.loader is None:
        raise QualificationDriverError(f"cannot load research build driver: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_regression_policy():
    path = REPO / "backend" / "regression_policy.py"
    spec = importlib.util.spec_from_file_location("modern_tls_regression_policy", path)
    if spec is None or spec.loader is None:
        raise QualificationDriverError(f"cannot load regression policy: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_package_driver():
    path = COMMON_ROOT / "build" / "package.py"
    spec = importlib.util.spec_from_file_location("python_build_package_driver", path)
    if spec is None or spec.loader is None:
        raise QualificationDriverError(f"cannot load package helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_regression_driver():
    path = REPO / "backend" / "qualify-no-openssl-regressions.py"
    spec = importlib.util.spec_from_file_location("no_openssl_regression_driver", path)
    if spec is None or spec.loader is None:
        raise QualificationDriverError(f"cannot load regression driver: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _receipt_commands(value, prefix: tuple[str, ...] = ()):
    if isinstance(value, dict):
        if "argv" in value:
            yield prefix, value
        else:
            for name, item in value.items():
                if isinstance(name, str):
                    yield from _receipt_commands(item, (*prefix, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _receipt_commands(item, (*prefix, str(index)))


def _validate_linux_command_logs(receipt: dict, run_dir: Path) -> dict[str, dict]:
    commands = receipt.get("commands")
    logs = receipt.get("logs")
    if not isinstance(commands, dict) or not isinstance(logs, dict):
        raise QualificationDriverError("Linux build receipt has no command mapping")
    verified_logs = {}
    for key, log in logs.items():
        if not isinstance(key, str) or not isinstance(log, dict):
            raise QualificationDriverError("Linux build receipt has a malformed log inventory")
        log_value = log.get("path")
        digest = log.get("sha256")
        if (
            not isinstance(log_value, str)
            or Path(log_value).is_absolute()
            or ".." in Path(log_value).parts
            or not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
        ):
            raise QualificationDriverError(f"Linux build receipt has an unsafe log record: {key}")
        log_path = (run_dir / log_value).resolve(strict=True)
        if not log_path.is_relative_to(run_dir.resolve(strict=True)) or not log_path.is_file():
            raise QualificationDriverError(f"Linux build log escapes its run directory: {key}")
        if sha256_file(log_path) != digest:
            raise QualificationDriverError(f"Linux build log digest differs: {key}")
        verified_logs[key] = {"path": log_value, "sha256": digest}
    observed = {}
    for path, command in _receipt_commands(commands):
        label = ".".join(path)
        argv = command.get("argv")
        log = command.get("log")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(argument, str) or not argument for argument in argv)
            or not isinstance(log, dict)
        ):
            raise QualificationDriverError(f"Linux build command record is malformed: {label}")
        log_value = log.get("path")
        log_digest = log.get("sha256")
        if (
            not isinstance(log_value, str)
            or not log_value
            or Path(log_value).is_absolute()
            or ".." in Path(log_value).parts
            or not isinstance(log_digest, str)
            or SHA256_PATTERN.fullmatch(log_digest) is None
        ):
            raise QualificationDriverError(f"Linux build command log record is unsafe: {label}")
        log_path = (run_dir / log_value).resolve(strict=True)
        if not log_path.is_relative_to(run_dir.resolve(strict=True)) or not log_path.is_file():
            raise QualificationDriverError(f"Linux build command log escapes its run directory: {label}")
        if sha256_file(log_path) != log_digest:
            raise QualificationDriverError(f"Linux build command log digest differs: {label}")
        if verified_logs.get(log_value) != {"path": log_value, "sha256": log_digest}:
            raise QualificationDriverError(f"Linux command log is absent from the receipt inventory: {label}")
        observed[label.lower()] = {
            "argv": argv,
            "log": {"path": log_value, "sha256": log_digest},
        }
    return observed


def _validate_linux_build_receipt(
    run_dir: Path,
    target,
    bootstrap: dict,
    expected_source_revision: str,
) -> tuple[dict, dict, dict[str, dict]]:
    receipt_path = run_dir / "linux-build.json"
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationDriverError(f"Linux build receipt is missing or invalid: {error}") from error
    if not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1:
        raise QualificationDriverError("Linux build receipt has an unsupported schema")
    if receipt.get("target") != target.triple:
        raise QualificationDriverError("Linux build receipt names a different target")
    arch = linux_arch(target)
    if receipt.get("cpu_baseline") != target.cpu_baseline_cflag:
        raise QualificationDriverError(
            f"Linux research build does not declare the {arch['cpu_baseline']} floor"
        )
    if receipt.get("pgo") is not False:
        raise QualificationDriverError("Linux research build must record PGO disabled")
    cpu_features = receipt.get("cpu_features")
    if not isinstance(cpu_features, list) or not arch["cpu_features"].issubset(cpu_features):
        raise QualificationDriverError(
            f"Linux build receipt does not prove the required native {arch['machine']} CPU features"
        )
    source_revision = receipt.get("source_revision")
    if (
        not isinstance(source_revision, str)
        or REVISION_PATTERN.fullmatch(source_revision) is None
        or source_revision != expected_source_revision
    ):
        raise QualificationDriverError("Linux build receipt has a stale source revision")
    compiler = receipt.get("compiler")
    if not isinstance(compiler, dict):
        raise QualificationDriverError("Linux build receipt has no compiler tool mapping")
    required_tools = {"cc", "cxx", "ar", "ranlib", "ld", "nm", "strip"}
    if set(compiler) != required_tools:
        raise QualificationDriverError("Linux build receipt has an incomplete compiler tool mapping")
    for name, tool in compiler.items():
        if (
            not isinstance(tool, dict)
            or not isinstance(tool.get("path"), str)
            or not Path(tool["path"]).is_absolute()
            or not Path(tool["path"]).is_file()
            or not os.access(tool["path"], os.X_OK)
            or not isinstance(tool.get("version"), str)
            or not tool["version"].strip()
        ):
            raise QualificationDriverError(f"Linux build receipt has an invalid {name} identity")
        resolved_path = Path(tool["path"]).resolve()
        located_path = shutil.which(Path(tool["path"]).name)
        if located_path is None or Path(located_path).resolve() != resolved_path:
            raise QualificationDriverError(f"Linux build receipt {name} path is not the active tool in PATH")
    pinned_clang = bootstrap.get("linux_toolchain", {}).get("llvm", {}).get("version")
    if pinned_clang is None or pinned_clang not in compiler["cc"]["version"]:
        raise QualificationDriverError("Linux build receipt compiler differs from locked LLVM")
    isolation = receipt.get("isolation")
    if (
        not isinstance(isolation, dict)
        or isolation.get("build_mode") != "sealed"
        or isolation.get("mechanism") != "docker-network-none"
        or isolation.get("interfaces") != ["lo"]
        or isolation.get("default_routes") != []
    ):
        raise QualificationDriverError("Linux build receipt lacks network-disabled Docker evidence")
    commands = _validate_linux_command_logs(receipt, run_dir)
    return receipt, compiler, commands


def _validate_linux_docker_receipt(
    run_dir: Path,
    target,
    bootstrap: dict,
    build_receipt_path: Path,
    build_receipt: dict,
) -> dict:
    docker_path = run_dir / "docker-build.json"
    try:
        docker = json.loads(docker_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationDriverError(f"Docker build receipt is missing or invalid: {error}") from error
    if not isinstance(docker, dict) or type(docker.get("schema")) is not int or docker.get("schema") != 1:
        raise QualificationDriverError("Docker build receipt has an unsupported schema")
    arch = linux_arch(target)
    expected_image = bootstrap.get("linux_toolchain", {}).get("images", {}).get(arch["docker_arch"], {}).get("digest")
    if (
        docker.get("target_triple") != target.triple
        or docker.get("platform") != arch["platform"]
        or docker.get("daemon_os") != "linux"
        or docker.get("daemon_platform") != arch["platform"]
        or docker.get("daemon_architecture") not in {arch["machine"], arch["docker_arch"]}
        or docker.get("container_architecture") != arch["machine"]
        or docker.get("image_architecture") not in {arch["machine"], arch["docker_arch"]}
        or not isinstance(docker.get("image_id"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", docker["image_id"]) is None
        or docker.get("locked_image_digest") != expected_image
        or docker.get("network_mode") != "none"
        or docker.get("isolation") != "docker-network-none"
        or docker.get("source_revision") != build_receipt.get("source_revision")
        or docker.get("source_dirty") is not False
        or docker.get("linux_build_sha256") != sha256_file(build_receipt_path)
        or type(docker.get("exit_code")) is not int
        or docker.get("exit_code") != 0
        or not isinstance(docker.get("container_id"), str)
        or not docker["container_id"]
    ):
        raise QualificationDriverError(
            f"Docker build receipt does not prove the locked native {arch['machine']} isolated run"
        )
    argv = docker.get("command")
    if not isinstance(argv, list) or not argv or any(not isinstance(value, str) for value in argv):
        raise QualificationDriverError("Docker build receipt has no inspected container command")
    docker_environment = [
        argv[index + 1]
        for index, value in enumerate(argv[:-1])
        if value in {"--env", "-e"}
    ]
    if "CARGO_BUILD_TARGET" in os.environ or any(
        value == "CARGO_BUILD_TARGET"
        or value.startswith("CARGO_BUILD_TARGET=")
        for value in docker_environment
    ):
        raise QualificationDriverError("Linux Cargo build environment forces a target override")
    if not any(
        argv[index : index + 2] == ["--network", "none"]
        or argv[index] == "--network=none"
        for index in range(len(argv))
    ) or not any(
        argv[index : index + 2] == ["--platform", arch["platform"]]
        or argv[index] == f"--platform={arch['platform']}"
        for index in range(len(argv))
    ):
        raise QualificationDriverError(
            f"Docker command does not request native {arch['machine']} with no network"
        )
    return docker


def _linux_build_evidence(
    build_run_dir: Path,
    base_build_run_dir: Path,
    target,
    research_driver,
) -> dict:
    try:
        bootstrap = json.loads((COMMON_ROOT / "bootstrap.lock.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationDriverError(f"cannot read Linux bootstrap lock: {error}") from error
    rust_bootstrap_path = REPO / "backend" / linux_arch(target)["bootstrap_lock"]
    try:
        rust_bootstrap = json.loads(rust_bootstrap_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationDriverError(f"cannot read Rust bootstrap lock: {error}") from error
    rust_lock = rust_bootstrap.get("rust") if isinstance(rust_bootstrap, dict) else None
    target_lock = rust_bootstrap.get("target") if isinstance(rust_bootstrap, dict) else None
    if (
        not isinstance(rust_lock, dict)
        or not isinstance(target_lock, dict)
        or rust_lock.get("host") != target.triple
        or target_lock.get("triple") != target.triple
        or not isinstance(rust_lock.get("version"), str)
        or rust_lock.get("version") != research_driver.RUST_VERSION
    ):
        raise QualificationDriverError("Rust bootstrap lock does not pin the native Linux target")
    source_revision = _load_source_revision(COMMON_ROOT)
    run_directories = []
    for path in (base_build_run_dir, build_run_dir):
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_relative_to((COMMON_ROOT / ".work").resolve()):
            raise QualificationDriverError(f"Linux build run directory is outside .work: {resolved}")
        if resolved not in run_directories:
            run_directories.append(resolved)

    records = []
    docker_records = []
    commands = {}
    compiler = None
    source_inputs = None
    for run_directory in run_directories:
        receipt_path = run_directory / "linux-build.json"
        receipt, observed_compiler, observed_commands = _validate_linux_build_receipt(
            run_directory, target, bootstrap, source_revision
        )
        docker = _validate_linux_docker_receipt(
            run_directory, target, bootstrap, receipt_path, receipt
        )
        if compiler is None:
            compiler = observed_compiler
        elif compiler != observed_compiler:
            raise QualificationDriverError("base and overlay Linux compiler identities differ")
        receipt_source = receipt.get("source")
        if not isinstance(receipt_source, dict):
            raise QualificationDriverError("Linux build receipt has no source identity mapping")
        if (
            receipt_source.get("sources_lock_sha256")
            != sha256_file(COMMON_ROOT / "sources.lock.json")
            or receipt_source.get("bootstrap_lock_sha256")
            != sha256_file(COMMON_ROOT / "bootstrap.lock.json")
        ):
            raise QualificationDriverError("Linux build receipt is bound to different input locks")
        if source_inputs is None:
            source_inputs = receipt_source
        else:
            for key, value in receipt_source.items():
                if key in source_inputs and source_inputs[key] != value:
                    raise QualificationDriverError(f"Linux build source identity differs for {key}")
                source_inputs[key] = value
        for key, value in observed_commands.items():
            commands[f"{run_directory.name}.{key}"] = value
        records.append(
            {
                "path": str(receipt_path),
                "sha256": sha256_file(receipt_path),
                "receipt": receipt,
            }
        )
        docker_records.append(
            {
                "path": str(run_directory / "docker-build.json"),
                "sha256": sha256_file(run_directory / "docker-build.json"),
                "receipt": docker,
            }
        )

    command_names = set(commands)
    for required in ("configure", "build", "install", "rtls", "hashing"):
        if not any(required in name for name in command_names):
            raise QualificationDriverError(f"Linux build receipts omit the {required} command log")
    if not isinstance(source_inputs, dict):
        raise QualificationDriverError("Linux build receipts have no source inputs")
    rtls_source, hashing_source = research_driver.require_fetched_rust_inputs(build_run_dir)
    required_source_digests = {
        "rtls_revision": research_driver.RTLS_REVISION,
        "rtls_snapshot_sha256": research_driver.rtls_source_digest(rtls_source),
        "hashing_snapshot_sha256": research_driver.hashing_source_digest(hashing_source),
        "rtls_cargo_lock_sha256": sha256_file(rtls_source / "rust" / "Cargo.lock"),
        "hashing_cargo_lock_sha256": sha256_file(hashing_source / "Cargo.lock"),
        "cpython_experimental_patches": [
            {"name": path.relative_to(REPO).as_posix(), "sha256": sha256_file(path)}
            for path in sorted((REPO / "backend" / "cpython-linux-patches").glob("*.patch"))
        ],
    }
    for name, expected in required_source_digests.items():
        if source_inputs.get(name) != expected:
            raise QualificationDriverError(f"Linux build receipt has a stale {name}")
    return {
        "source_revision": source_revision,
        "bootstrap": bootstrap,
        "bootstrap_identity": sha256_file(COMMON_ROOT / "bootstrap.lock.json"),
        "rust_bootstrap": rust_bootstrap,
        "rust_bootstrap_identity": sha256_file(rust_bootstrap_path),
        "compiler": compiler,
        "source": source_inputs,
        "commands": commands,
        "build_receipts": records,
        "docker_receipts": docker_records,
        "rust_sources": (rtls_source, hashing_source),
    }


def check_result(ok: bool, **values) -> dict:
    return {"ok": ok, **values}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_executable(value: str, name: str) -> Path:
    located = shutil.which(value)
    if located is None:
        raise QualificationDriverError(f"{name} is not available in PATH: {value!r}")
    executable = Path(located).resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise QualificationDriverError(f"{name} is not executable: {executable}")
    return executable


def write_json(path: Path, value: object) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _captured_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run_logged_process(
    name: str,
    argv: list[str | Path],
    install: Path,
    cwd: Path,
    logs: Path,
    *,
    timeout_seconds: int,
) -> dict:
    stdout_path = logs / f"{name}.stdout.txt"
    stderr_path = logs / f"{name}.stderr.txt"
    command = [str(argument) for argument in argv]
    try:
        completed = run_product_process(
            argv,
            install,
            cwd,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        stdout = _captured_text(error.stdout)
        stderr = _captured_text(error.stderr)
        stdout_path.write_text(stdout)
        stderr_path.write_text(stderr)
        return {
            "ok": False,
            "command": command,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "stdout_sha256": sha256_file(stdout_path),
            "stderr_sha256": sha256_file(stderr_path),
        }
    except (OSError, ValueError) as error:
        stdout_path.write_text("")
        stderr_path.write_text(f"{type(error).__name__}: {error}\n")
        return {
            "ok": False,
            "command": command,
            "error_type": type(error).__name__,
            "error": str(error),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "stdout_sha256": sha256_file(stdout_path),
            "stderr_sha256": sha256_file(stderr_path),
        }
    stdout_path.write_text(completed.stdout)
    stderr_path.write_text(completed.stderr)
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "returncode": completed.returncode,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
    }


def run_json_contract(
    name: str,
    argv: list[str | Path],
    install: Path,
    cwd: Path,
    logs: Path,
    *,
    timeout_seconds: int,
    report_path: Path,
) -> tuple[dict, dict | None]:
    process = run_logged_process(
        name, argv, install, cwd, logs, timeout_seconds=timeout_seconds
    )
    result = dict(process)
    if process.get("returncode") != 0:
        return result, None
    try:
        payload = json.loads(Path(process["stdout_log"]).read_text())
    except (OSError, json.JSONDecodeError) as error:
        result.update(ok=False, error=f"contract output is not valid JSON: {error}")
        return result, None
    if not isinstance(payload, dict):
        result.update(ok=False, error="contract output must be a JSON object")
        return result, None
    write_json(report_path, payload)
    result["report"] = str(report_path)
    result["report_sha256"] = sha256_file(report_path)
    return result, payload


def modern_compatibility_profile(
    raw: dict,
    candidate: Path,
    extracted: Path,
    rtls_source: Path,
    workdir: Path,
) -> tuple[dict, dict]:
    modern_compatibility = copy.deepcopy(raw)
    checks = modern_compatibility.get("checks")
    aggregate = modern_compatibility.get("aggregate")
    if not isinstance(checks, dict) or not isinstance(aggregate, dict):
        raise QualificationDriverError("standalone compatibility report is incomplete")
    from modern_compat import modern_replacement_for_legacy_check, probe_modern_tls

    replacement_name = modern_replacement_for_legacy_check("ssl_capabilities")
    if replacement_name is None or "ssl_capabilities" not in checks:
        raise QualificationDriverError("modern TLS replacement has no raw SSL capability check")
    expected_count = aggregate.get("required_check_count")
    if (
        aggregate.get("check_count") != len(checks)
        or expected_count != len(checks)
    ):
        raise QualificationDriverError("raw standalone compatibility check accounting is incomplete")
    raw_failures = aggregate.get("failed")
    if not isinstance(raw_failures, list) or len(raw_failures) != len(set(raw_failures)):
        raise QualificationDriverError("raw compatibility failure list is malformed")
    observed_failures = sorted(
        name
        for name, value in checks.items()
        if not isinstance(value, dict)
        or value.get("status") not in {"passed", "skipped", "failed"}
    )
    if observed_failures:
        raise QualificationDriverError(
            "raw compatibility checks have malformed statuses: "
            + ", ".join(observed_failures)
        )
    expected_raw_failures = sorted(
        name for name, value in checks.items() if value.get("status") == "failed"
    )
    if expected_raw_failures != sorted(raw_failures):
        raise QualificationDriverError("raw compatibility failure accounting disagrees with checks")

    source_checks = checks
    del source_checks["ssl_capabilities"]
    capability = probe_modern_tls(candidate, extracted, rtls_source, workdir)
    capability_result = {
        "status": "passed" if capability.get("ok") is True else "failed",
        "evidence": capability.get("evidence"),
    }
    if capability.get("ok") is not True:
        capability_result["error"] = capability.get("error", "modern TLS capability check failed")
    source_checks[replacement_name] = capability_result

    failed = sorted(
        name for name, value in source_checks.items() if value.get("status") == "failed"
    )
    aggregate.update(
        {
            "check_count": len(source_checks),
            "required_check_count": expected_count,
            "failed": failed,
            "ok": len(source_checks) == expected_count and not failed,
        }
    )
    modern_compatibility["failed"] = failed
    modern_compatibility["ok"] = aggregate["ok"]
    return modern_compatibility, capability


def validate_ssl_exact_case_assessment(report: dict, expected_tests_run: int) -> dict:
    modern_policy = report.get("modern_policy")
    if not isinstance(modern_policy, dict):
        raise QualificationDriverError("regression report has no modern_policy mapping")
    policy_path = REPO / "backend" / "MODERN_TLS_POLICY.md"
    expected_policy_digest = sha256_file(policy_path)
    if modern_policy.get("policy_sha256") != expected_policy_digest:
        raise QualificationDriverError("regression report modern policy digest differs from current policy")
    assessment = modern_policy.get("ssl_exact_case_assessment")
    if not isinstance(assessment, dict):
        raise QualificationDriverError("regression report has no SSL exact-case assessment")

    count_fields = ("tests_run", "failures", "errors", "skips")
    for field in count_fields:
        value = assessment.get(field)
        if type(value) is not int or value < 0:
            raise QualificationDriverError(f"SSL exact-case assessment has invalid {field}")
    accepted = assessment.get("accepted")
    unexpected = assessment.get("unexpected")
    if not isinstance(accepted, list) or not isinstance(unexpected, list):
        raise QualificationDriverError("SSL exact-case details are missing")
    details = accepted + unexpected
    if any(
        not isinstance(item, dict)
        or item.get("kind") not in {"FAIL", "ERROR"}
        or not isinstance(item.get("case"), str)
        or not item["case"]
        or not isinstance(item.get("reason"), str)
        or not item["reason"]
        for item in details
    ):
        raise QualificationDriverError("SSL exact-case detail records are malformed")
    if len(details) != assessment["failures"] + assessment["errors"]:
        raise QualificationDriverError("SSL exact-case detail counts do not match the summary")
    accepted_failures = sum(item["kind"] == "FAIL" for item in accepted)
    accepted_errors = sum(item["kind"] == "ERROR" for item in accepted)
    unexpected_failures = sum(item["kind"] == "FAIL" for item in unexpected)
    unexpected_errors = sum(item["kind"] == "ERROR" for item in unexpected)
    if (
        accepted_failures + unexpected_failures != assessment["failures"]
        or accepted_errors + unexpected_errors != assessment["errors"]
    ):
        raise QualificationDriverError("SSL exact-case failure/error categories do not reconcile")
    complete = (
        assessment.get("complete") is True
        and assessment["tests_run"] == expected_tests_run
        and assessment.get("expected_tests_run") == expected_tests_run
        and assessment["tests_run"] > 0
    )
    ok = (
        assessment.get("ok") is True
        and complete
        and not unexpected
        and accepted_failures == assessment["failures"]
        and accepted_errors == assessment["errors"]
    )
    return {
        "ok": ok,
        "complete": complete,
        "policy_sha256": expected_policy_digest,
        "ssl_exact_case_assessment": assessment,
    }


def validate_ssl_log_evidence(report: dict, expected_tests_run: int) -> dict:
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise QualificationDriverError("regression report has no diagnostics mapping")
    records = {}
    contents = {}
    for name in ("full_suite_log", "test_ssl_log"):
        record = diagnostics.get(name)
        if not isinstance(record, dict):
            raise QualificationDriverError(f"regression report has no {name} record")
        value = record.get("path")
        digest = record.get("sha256")
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise QualificationDriverError(f"regression report has an invalid {name} path")
        requested = Path(value)
        log_path = require_product_work_path(requested, name)
        if requested.is_symlink() or str(log_path) != value or not log_path.is_file():
            raise QualificationDriverError(f"regression report {name} is missing, linked, or noncanonical")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise QualificationDriverError(f"regression report has an invalid {name} digest")
        log_bytes = log_path.read_bytes()
        if hashlib.sha256(log_bytes).hexdigest() != digest:
            raise QualificationDriverError(f"regression report {name} digest differs from its file")
        records[name] = {"path": value, "sha256": digest}
        contents[name] = log_bytes

    try:
        full_text = contents["full_suite_log"].decode("utf-8")
    except UnicodeDecodeError as error:
        raise QualificationDriverError("regression logs must be UTF-8") from error
    policy = load_regression_policy()
    test_ssl_bytes = contents["test_ssl_log"]
    test_ssl_block = policy.extract_test_ssl_log(full_text).encode("utf-8")
    if test_ssl_bytes not in (contents["full_suite_log"], test_ssl_block):
        raise QualificationDriverError("test_ssl log is not the exact module block from the full suite log")

    assessment = report.get("modern_policy", {}).get("ssl_exact_case_assessment")
    if not isinstance(assessment, dict):
        raise QualificationDriverError("regression report has no SSL exact-case assessment")
    test_ssl_record = records["test_ssl_log"]
    if (
        assessment.get("source_log_path") != test_ssl_record["path"]
        or assessment.get("source_log_sha256") != test_ssl_record["sha256"]
    ):
        raise QualificationDriverError("SSL assessment is not bound to the full-run test_ssl log")
    derived = policy.assess_ssl_full_run(
        full_text,
        expected_tests_run=expected_tests_run,
    )
    if any(assessment.get(field) != value for field, value in derived.items()):
        raise QualificationDriverError("SSL assessment differs from the full-run test_ssl block")
    return {
        "full_suite_log": records["full_suite_log"],
        "test_ssl_log": test_ssl_record,
        "assessment": derived,
    }


def validate_test_ssl_inventory(
    report: dict,
    source_prefix: Path,
    tested_prefix: Path,
    expected_source: Path,
    scratch: Path,
    reference_python: Path,
) -> dict:
    inventory = report.get("ssl_test_inventory")
    if not isinstance(inventory, dict) or inventory.get("equal") is not True:
        raise QualificationDriverError("regression report has no matching test_ssl source inventory")
    candidate = inventory.get("candidate")
    tested = inventory.get("tested")
    if not isinstance(candidate, dict) or not isinstance(tested, dict):
        raise QualificationDriverError("test_ssl source inventory is malformed")
    expected_test_count = load_regression_policy().EXPECTED_SSL_TEST_COUNT
    if (
        inventory.get("expected_test_count") != expected_test_count
        or inventory.get("candidate_count_matches_expected") is not True
        or inventory.get("tested_count_matches_expected") is not True
    ):
        raise QualificationDriverError(
            f"regression inventory does not match the pinned {expected_test_count}-case policy"
        )
    candidate_source = source_prefix / PRODUCT.stdlib_relative_path / "test" / "test_ssl.py"
    tested_source = tested_prefix / PRODUCT.stdlib_relative_path / "test" / "test_ssl.py"
    if expected_source.is_symlink() or not expected_source.is_file():
        raise QualificationDriverError("pinned CPython test_ssl source is missing or linked")
    expected_source = Path(expected_source).resolve(strict=True)
    for label, record, path, prefix in (
        ("candidate", candidate, candidate_source, source_prefix),
        ("tested", tested, tested_source, tested_prefix),
    ):
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not path.is_file() or not resolved.is_relative_to(prefix.resolve(strict=True)):
            raise QualificationDriverError(f"{label} test_ssl source is missing, linked, or outside its prefix")
        if record.get("source_path") != str(resolved):
            raise QualificationDriverError(f"{label} test_ssl inventory names a different source file")
        digest = sha256_file(path)
        if record.get("source_sha256") != digest or digest != sha256_file(expected_source):
            raise QualificationDriverError(f"{label} test_ssl source differs from pinned CPython source")
    for field in ("test_count", "case_ids_sha256"):
        if candidate.get(field) != tested.get(field):
            raise QualificationDriverError(f"candidate and disposable test_ssl inventories differ in {field}")
    expected_fields = {"source_path", "source_sha256", "test_count", "case_ids_sha256"}
    if set(candidate) != expected_fields or set(tested) != expected_fields:
        raise QualificationDriverError("test_ssl inventory has unexpected or missing fields")
    if (
        type(candidate.get("test_count")) is not int
        or candidate["test_count"] != expected_test_count
        or type(tested.get("test_count")) is not int
        or tested["test_count"] != expected_test_count
        or not isinstance(tested.get("case_ids_sha256"), str)
        or SHA256_PATTERN.fullmatch(tested["case_ids_sha256"]) is None
    ):
        raise QualificationDriverError("test_ssl case inventory differs from the pinned count or digest policy")
    if candidate.get("test_count") != tested["test_count"]:
        raise QualificationDriverError("candidate test_ssl inventory differs from the pinned test count")
    driver = load_regression_driver()
    observed_tested = driver.ssl_test_case_inventory(tested_prefix, scratch)
    if observed_tested != tested:
        raise QualificationDriverError("test_ssl inventory differs from disposable runtime discovery")
    reference_python = Path(reference_python).resolve(strict=True)
    reference_prefix = reference_python.parent.parent.resolve(strict=True)
    if PRODUCT.interpreter(reference_prefix).resolve(strict=True) != reference_python:
        raise QualificationDriverError("OpenSSL reference interpreter is outside its product prefix")
    reference = driver.ssl_test_case_inventory(reference_prefix, scratch)
    if set(reference) != expected_fields:
        raise QualificationDriverError("OpenSSL reference test_ssl inventory is malformed")
    if (
        reference["source_sha256"] != sha256_file(expected_source)
        or reference["test_count"] != expected_test_count
        or reference["case_ids_sha256"] != tested["case_ids_sha256"]
    ):
        raise QualificationDriverError(
            "test_ssl case inventory differs from the pinned OpenSSL reference"
        )
    return {
        "expected_test_count": expected_test_count,
        "pinned_source": {
            "path": str(expected_source),
            "sha256": sha256_file(expected_source),
        },
        "reference": reference,
        "candidate": candidate,
        "tested": tested,
        "equal": True,
    }


def validate_regression_report(
    path: Path | None,
    source_prefix: Path,
    target,
    expected_test_source: Path,
    inventory_scratch: Path,
    reference_python: Path,
) -> dict:
    if path is None:
        return check_result(False, error="pre-prune regression report was not supplied")
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return check_result(False, error=f"cannot read regression report: {error}")
    if not isinstance(report, dict):
        return check_result(False, error="regression report must be a JSON object")
    try:
        tested_value = report.get("tested_prefix")
        if not isinstance(tested_value, str):
            raise QualificationDriverError("regression report has no disposable tested prefix")
        tested_prefix = require_product_work_path(Path(tested_value), "regression tested prefix")
        if not tested_prefix.is_dir():
            raise QualificationDriverError(f"regression tested prefix is missing: {tested_prefix}")
        ssl_inventory = validate_test_ssl_inventory(
            report,
            source_prefix,
            tested_prefix,
            expected_test_source,
            inventory_scratch,
            reference_python,
        )
        expected_tests_run = load_regression_policy().EXPECTED_SSL_TEST_COUNT
        ssl_log_evidence = validate_ssl_log_evidence(report, expected_tests_run)
        full_log_path = Path(ssl_log_evidence["full_suite_log"]["path"])
        full_log_bytes = full_log_path.read_bytes()
        if hashlib.sha256(full_log_bytes).hexdigest() != ssl_log_evidence["full_suite_log"]["sha256"]:
            raise QualificationDriverError("full suite log changed during validation")
        try:
            raw_suite = account_suite_log(full_log_bytes.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise QualificationDriverError("full regression log is not UTF-8") from error
        suite = report_payload(report, target)
        for field, value in raw_suite.items():
            if suite.get(field) != value:
                raise QualificationDriverError(f"full regression report disagrees with raw log: {field}")
        expected_returncode = 0 if raw_suite["result_state"] == "SUCCESS" else 2
        if report.get("returncode") != expected_returncode:
            raise QualificationDriverError("full regression exit status disagrees with raw log")
        solo_findings = report.get("solo_findings")
        if not isinstance(solo_findings, dict):
            raise QualificationDriverError("regression report has no non-SSL diagnostic mapping")
        expected_finding_files = set(raw_suite["failed_files"]) - {"test_ssl"}
        if set(solo_findings) != expected_finding_files:
            raise QualificationDriverError("non-SSL diagnostics do not cover the raw failed-file set")
        classification = classify_suite_findings(solo_findings, target)
        if report.get("classification") != classification:
            raise QualificationDriverError("regression classification differs from registered diagnostics")
        if suite.get("complete") is not True or suite.get("ok") is not True:
            raise QualificationDriverError("full regression suite is incomplete or has unexpected failures")
        if report.get("candidate_prefix") != str(source_prefix.resolve()):
            raise QualificationDriverError("regression report names a different source candidate prefix")
        candidate_identity = report.get("candidate_identity")
        if not isinstance(candidate_identity, dict):
            raise QualificationDriverError("regression report has no candidate identity mapping")
        expected_identity = set(candidate_runtime_identity_paths(source_prefix, target))
        if set(candidate_identity) != expected_identity:
            missing = sorted(expected_identity - set(candidate_identity))
            extra = sorted(set(candidate_identity) - expected_identity)
            raise QualificationDriverError(
                f"candidate identity file set differs from runtime inputs: missing={missing}, extra={extra}"
            )
        identities = {}
        for relative, expected_digest in candidate_identity.items():
            if not isinstance(relative, str) or not isinstance(expected_digest, str) or not SHA256_PATTERN.fullmatch(expected_digest):
                raise QualificationDriverError("candidate identity contains an invalid path or digest")
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise QualificationDriverError("candidate identity contains an unsafe relative path")
            for label, prefix in (("source", source_prefix), ("tested", tested_prefix)):
                root = prefix.resolve(strict=True)
                candidate_file = root / relative_path
                if candidate_file.is_symlink() or not candidate_file.is_file():
                    raise QualificationDriverError(f"candidate identity file is missing or linked: {candidate_file}")
                resolved_file = candidate_file.resolve(strict=True)
                if not resolved_file.is_relative_to(root):
                    raise QualificationDriverError(f"candidate identity escapes its prefix: {relative}")
                found = sha256_file(candidate_file)
                if found != expected_digest:
                    raise QualificationDriverError(f"{label} candidate identity differs: {relative}")
            identities[relative] = expected_digest
        ssl_assessment = validate_ssl_exact_case_assessment(report, expected_tests_run)
        if not ssl_assessment["ok"]:
            raise QualificationDriverError("SSL exact-case modern policy assessment failed")
        return check_result(
            True,
            source_report=str(path.resolve()),
            source_report_sha256=sha256_file(path),
            tested_prefix=str(tested_prefix),
            suite=suite,
            ssl_test_inventory=ssl_inventory,
            candidate_identity=identities,
            modern_policy=ssl_assessment,
            ssl_log_evidence=ssl_log_evidence,
            report=report,
        )
    except Exception as error:  # noqa: BLE001
        return check_result(
            False,
            source_report=str(path.resolve()),
            source_report_sha256=sha256_file(path) if path.is_file() else None,
            error_type=type(error).__name__,
            error=str(error),
            report=report,
        )


def certificate_process_succeeded(process: dict, payload: dict) -> bool:
    return (
        process.get("ok") is True
        and process.get("returncode") == 0
        and (
            "exit_code" not in payload
            or (
                type(payload.get("exit_code")) is int
                and payload["exit_code"] == 0
            )
        )
    )


def run_archive_contracts(
    extracted: Path,
    reference_python: Path,
    certdata: Path,
    run_dir: Path,
    target,
) -> dict[str, dict]:
    python = PRODUCT.interpreter(extracted)
    contracts = run_dir / "contracts"
    logs = contracts / "logs"
    for path in (contracts, logs, contracts / "certificate-work", contracts / "modern-tls-work"):
        path.mkdir(parents=True, exist_ok=True)

    openssl = shutil.which("openssl")
    certificate_argv: list[str | Path] = [
        sys.executable,
        "-I",
        "-B",
        REPO / "backend" / "certificate_contract.py",
        "--reference",
        reference_python,
        "--candidate",
        python,
        "--certdata",
        certdata,
        "--workdir",
        contracts / "certificate-work",
    ]
    if openssl is None:
        certificate_process = check_result(False, error="no OpenSSL-compatible fixture generator found")
        certificate_payload = None
    else:
        certificate_argv.extend(("--openssl", Path(openssl)))
        certificate_process, certificate_payload = run_json_contract(
            "certificate-contract",
            certificate_argv,
            extracted,
            contracts / "certificate-work",
            logs,
            timeout_seconds=1800,
            report_path=contracts / "certificate-contract.json",
        )
    certificate_ok = False
    certificate_summary = {}
    if certificate_payload is not None:
        cases = certificate_payload.get("cases")
        candidate_identity = certificate_payload.get("candidate")
        code_cases = (
            sum(
                isinstance(case, dict) and case.get("verify_code_expected") is not None
                for case in cases.values()
            )
            if isinstance(cases, dict)
            else 0
        )
        certificate_ok = (
            certificate_process_succeeded(certificate_process, certificate_payload)
            and certificate_payload.get("result") == "passed"
            and isinstance(candidate_identity, dict)
            and candidate_identity.get("interpreter") == PRODUCT.version_string
            and candidate_identity.get("executable") == str(python.resolve())
            and isinstance(cases, dict)
            and len(cases) == CERTIFICATE_CASE_COUNT
            and code_cases == CERTIFICATE_VERIFY_CODE_COUNT
            and all(
                isinstance(case, dict) and case.get("contract_passed") is True
                for case in cases.values()
            )
            and not certificate_payload.get("differences")
            and not certificate_payload.get("flag_differences")
            and not certificate_payload.get("default_flags_differ")
            and not certificate_payload.get("harness_errors")
            and not certificate_payload.get("reference_expectation_errors")
        )
        certificate_summary = {
            "cases": len(cases) if isinstance(cases, dict) else 0,
            "verify_code_cases": code_cases,
            "differences": certificate_payload.get("differences"),
            "flag_differences": certificate_payload.get("flag_differences"),
            "harness_errors": certificate_payload.get("harness_errors"),
            "reference_expectation_errors": certificate_payload.get("reference_expectation_errors"),
        }

    modern_process, modern_payload = run_json_contract(
        "modern-tls-contract",
        [
            python,
            "-I",
            "-B",
            REPO / "backend" / "modern_tls_contract.py",
            "--reference",
            reference_python,
            "--certdata",
            certdata,
            "--timeout",
            "4",
        ],
        extracted,
        contracts / "modern-tls-work",
        logs,
        timeout_seconds=600,
        report_path=contracts / "modern-tls-contract.json",
    )
    modern_matrix = modern_payload.get("matrix") if isinstance(modern_payload, dict) else None
    modern_tls_ok = (
        modern_process.get("returncode") == 0
        and isinstance(modern_payload, dict)
        and modern_payload.get("status") == "passed"
        and modern_payload.get("exit_code") == 0
        and isinstance(modern_matrix, list)
        and len(modern_matrix) == 9
        and not modern_payload.get("failures")
        and not modern_payload.get("unavailable_cases")
        and all(isinstance(row, dict) and row.get("status") not in {"failed", "reference_unavailable"} for row in modern_matrix)
    )

    from buildsys.inputs import Cache, load_lock, safe_extract

    ca_pin = next(pin for pin in load_lock(COMMON_ROOT / "sources.lock.json") if pin.name == "certifi-ca")
    ca_source = safe_extract(
        Cache(COMMON_ROOT / ".cache").require(ca_pin), contracts / "pinned CA source",
    )
    ca_bundles = list(ca_source.glob("*/certifi/cacert.pem"))
    if len(ca_bundles) != 1:
        raise QualificationDriverError("locked CA source does not contain one certificate bundle")
    trust_process = run_logged_process(
        "trust-source-contract",
        [
            python, "-I", "-B",
            REPO / "backend" / "trust_policy_contract.py",
            certdata, ca_bundles[0], reference_python,
        ],
        extracted,
        contracts,
        logs,
        timeout_seconds=120,
    )
    trust_output = Path(trust_process["stderr_log"]).read_text()
    trust_runs = re.findall(r"^Ran ([0-9]+) tests in ", trust_output, re.MULTILINE)
    trust_ok = (
        trust_process.get("returncode") == 0
        and trust_runs == ["16"]
        and re.search(r"^OK$", trust_output, re.MULTILINE) is not None
    )
    hashing_process = run_logged_process(
        "hashing-reference-comparison",
        [
            sys.executable,
            "-I",
            "-B",
            REPO / "backend" / "hashing" / "compare-reference.py",
            reference_python,
            python,
        ],
        extracted,
        contracts,
        logs,
        timeout_seconds=600,
    )
    hash_output = Path(hashing_process["stdout_log"]).read_text()
    hash_match = re.fullmatch(
        r"matched \d+ name and error cases, \d+ HMAC/PBKDF2 algorithms, and the scrypt boundary "
        r"\(\d+ buffer cases, \d+ scrypt parameter bounds, and scrypt __index__ conversion\)\s*",
        hash_output,
    )
    hashing_ok = hashing_process.get("returncode") == 0 and hash_match is not None
    cleanup_process, cleanup_payload = run_json_contract(
        "handshake-error-cleanup",
        [
            python, "-I", "-B",
            REPO / "backend" / "test_ssl_handshake_error_cleanup.py",
            "--certdata", certdata,
        ],
        extracted,
        contracts,
        logs,
        timeout_seconds=60,
        report_path=contracts / "handshake-error-cleanup.json",
    )
    cleanup_ok = (
        cleanup_process.get("returncode") == 0
        and isinstance(cleanup_payload, dict)
        and cleanup_payload.get("ok") is True
        and cleanup_payload.get("tests_run") == 6
        and cleanup_payload.get("skipped") == 0
    )
    symbol_audit = audit_openssl_symbols(extracted, target)
    from buildsys.notices import verify_notices

    notice_report = verify_notices(extracted, target, COMMON_ROOT / "sources.lock.json")

    return {
        "no_openssl_symbols": symbol_audit,
        "license_notices": check_result(notice_report.pop("ok"), **notice_report),
        "trust_source_contract": check_result(
            trust_ok, process=trust_process, tests_run=trust_runs,
            pinned_bundle_sha256=sha256_file(ca_bundles[0]),
        ),
        "handshake_error_cleanup": check_result(
            cleanup_ok, process=cleanup_process, summary=cleanup_payload,
        ),
        "certificate_contract": check_result(
            certificate_ok,
            process=certificate_process,
            summary=certificate_summary,
        ),
        "modern_tls_contract": check_result(
            modern_tls_ok,
            process=modern_process,
            summary={
                "matrix_cases": len(modern_matrix) if isinstance(modern_matrix, list) else 0,
                "failures": modern_payload.get("failures") if isinstance(modern_payload, dict) else None,
                "unavailable_cases": modern_payload.get("unavailable_cases") if isinstance(modern_payload, dict) else None,
            },
        ),
        "hashing_reference_comparison": check_result(
            hashing_ok,
            process=hashing_process,
            matched_summary=hash_match.group(0).strip() if hash_match else None,
        ),
    }


def _parse_make_variables(makefile: Path, names: set[str]) -> dict[str, str]:
    values = {}
    for line in makefile.read_text(errors="replace").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() in names:
            values[name.strip()] = value.strip()
    return values


def _load_source_revision(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return result.stdout.strip()


def _rust_dependency_graph(build_run_dir: Path, run_dir: Path, research_driver, target) -> list[dict]:
    license_helper = importlib.util.spec_from_file_location(
        "no_openssl_rust_license_helper",
        REPO / "backend" / "rust_licenses.py",
    )
    if license_helper is None or license_helper.loader is None:
        raise QualificationDriverError("cannot load Cargo dependency closure helper")
    module = importlib.util.module_from_spec(license_helper)
    sys.modules[license_helper.name] = module
    license_helper.loader.exec_module(module)

    cargo, rustc, kache = research_driver.rust_toolchain()
    cargo_home = build_run_dir / "cargo-home"
    registry_root = (cargo_home / "registry" / "src").resolve(strict=True)
    temporary_root = run_dir / "cargo metadata work"
    temporary_root.mkdir()
    source_roots = {
        "rtls": build_run_dir / "rtls-build",
        "hashing": build_run_dir / "hashing-build",
    }
    manifests = {
        "rtls": source_roots["rtls"] / "rust" / "Cargo.toml",
        "hashing": source_roots["hashing"] / "Cargo.toml",
    }
    tool_path = f"{kache.parent}:" if kache is not None else ""
    records: dict[str, dict] = {}
    for name, manifest in manifests.items():
        environment = {
            "CARGO_HOME": str(cargo_home),
            "CARGO_NET_OFFLINE": "true",
            "CARGO_TARGET_DIR": str(temporary_root / "cargo-target"),
            "CARGO_TERM_COLOR": "never",
            "HOME": str(build_run_dir / "home"),
            "PATH": f"{tool_path}{rustc.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(temporary_root),
        }
        result = subprocess.run(
            [
                str(cargo),
                "metadata",
                "--locked",
                "--offline",
                "--format-version",
                "1",
                "--filter-platform",
                target.triple,
                "--manifest-path",
                str(manifest),
            ],
            cwd=manifest.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            raise QualificationDriverError(
                f"offline Cargo metadata failed for {name}: "
                f"{(result.stdout + result.stderr)[-3000:]}"
            )
        try:
            metadata = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise QualificationDriverError(f"Cargo metadata for {name} is malformed") from error
        for package in module._resolved_packages(metadata):
            package_name = package.get("name")
            version = package.get("version")
            if not isinstance(package_name, str) or not isinstance(version, str):
                raise QualificationDriverError("resolved Cargo package identity is incomplete")
            normalized = package_name.lower().replace("_", "-")
            if "openssl" in normalized or normalized in {"libssl", "libcrypto"}:
                raise QualificationDriverError(f"OpenSSL package is active in Rust graph: {package_name}")
            _package_root, source_identity = module._package_location(
                package,
                {key: path.resolve(strict=True) for key, path in source_roots.items()},
                registry_root,
            )
            package_id = package["id"]
            record = records.setdefault(
                package_id,
                {
                    "name": package_name,
                    "version": version,
                    "source": source_identity,
                    "license": package.get("license"),
                    "license_file": package.get("license_file"),
                    "used_by": [],
                },
            )
            if record["source"] != source_identity or record["license"] != package.get("license"):
                raise QualificationDriverError(f"Cargo package identity conflicts: {package_name}")
            if name not in record["used_by"]:
                record["used_by"].append(name)
    return sorted(records.values(), key=lambda item: (item["name"], item["version"], item["source"]))


def build_experimental_manifest(
    *,
    archive_facts,
    archive_path: Path,
    source_prefix: Path,
    extracted_prefix: Path,
    build_run_dir: Path,
    base_build_run_dir: Path,
    run_dir: Path,
    target,
    qualification: dict,
    regression_check: dict,
    raw_compatibility: dict,
    effective_compatibility: dict,
    research_driver,
) -> dict:
    from buildsys.deporder import dependency_order
    from buildsys.inputs import Cache, canonical_json, load_lock

    source_dir = base_build_run_dir / "cpython-source" / f"Python-{PRODUCT.version_string}"
    build_directory = base_build_run_dir / "cpython-build"
    makefile = build_directory / "Makefile"
    config_log = build_directory / "config.log"
    make_variables = _parse_make_variables(
        makefile,
        {
            "CONFIG_ARGS",
            "CONFIGURE_CFLAGS",
            "CONFIGURE_CFLAGS_NODIST",
            "CONFIGURE_CPPFLAGS",
            "CONFIGURE_LDFLAGS",
            "PY_CFLAGS",
            "PY_CFLAGS_NODIST",
            "PY_CPPFLAGS",
            "PY_LDFLAGS",
            "PROFILE_TASK",
        },
    )
    required_make_variables = {"CONFIG_ARGS", "CONFIGURE_CFLAGS"}
    if target.is_macos:
        required_make_variables.add("PROFILE_TASK")
    if not required_make_variables.issubset(make_variables):
        raise QualificationDriverError("CPython Makefile omits its configuration or effective flags")
    configure_arguments = shlex.split(make_variables["CONFIG_ARGS"])
    if "--with-lto=thin" not in configure_arguments:
        raise QualificationDriverError("CPython Makefile does not show ThinLTO configuration")
    if any(argument.startswith("--with-openssl") for argument in configure_arguments):
        raise QualificationDriverError("CPython configure arguments unexpectedly enable OpenSSL")
    configure_values = {
        key: value
        for argument in configure_arguments
        for key, separator, value in (argument.partition("="),)
        if separator
    }
    cflags = configure_values.get("CFLAGS", "")
    if target.is_macos:
        from buildsys.bootstrap import load_macos_toolchain

        locked_toolchain = load_macos_toolchain(COMMON_ROOT / "bootstrap.lock.json")
        target_toolchain = locked_toolchain.toolchain(jobs=2)
        if target.pgo:
            profile_path = build_directory / "code.profclangd"
            pgo_stdout = StringIO()
            pgo_environment = target_toolchain.env(
                {"LLVM_PROFDATA": str(locked_toolchain.llvm_profdata)}
            )
            with contextlib.redirect_stdout(pgo_stdout):
                research_driver.verify_pgo_build(
                    source_dir,
                    build_directory,
                    target_toolchain,
                    pgo_environment,
                )
            pgo_summary = pgo_stdout.getvalue()
            pgo_match = re.search(
                r"PGO profile valid: bytes=(\d+), sha256=([0-9a-f]{64}), "
                r"functions=(\d+), max_count=(\d+)",
                pgo_summary,
            )
            if pgo_match is None or not profile_path.is_file():
                raise QualificationDriverError("locked PGO verifier did not return profile evidence")
            profile_size, profile_digest, functions, maximum_count = pgo_match.groups()
            if sha256_file(profile_path) != profile_digest or profile_path.stat().st_size != int(profile_size):
                raise QualificationDriverError("verified PGO profile changed after inspection")
        else:
            pgo_summary = "CPython macOS PGO is deferred by product policy."
            profile_path = None
            profile_digest = None
            profile_size = 0
            functions = 0
            maximum_count = 0
        locked_clang = (locked_toolchain.llvm_prefix / "bin" / "clang").resolve()
        llvm_version = locked_toolchain.llvm_version
        bootstrap_identity = hashlib.sha256(
            canonical_json(locked_toolchain.identity()).encode()
        ).hexdigest()
        source_revision = _load_source_revision(COMMON_ROOT)
        rust_target_cpu = "apple-m1"
        rust_target_features = None
        if target.pgo:
            pgo_receipt = {
                "verified": True,
                "summary": pgo_summary.strip().splitlines(),
                "profile_path": str(profile_path),
                "profile_sha256": profile_digest,
                "profile_size_bytes": int(profile_size),
            }
            if "--enable-optimizations" not in configure_arguments:
                raise QualificationDriverError("CPython Makefile does not show PGO optimization")
        else:
            pgo_receipt = {"verified": False, "enabled": False, "policy": "PGO is deferred"}
            if "--enable-optimizations" in configure_arguments:
                raise QualificationDriverError("macOS CPython build unexpectedly enables PGO")
        if Path(configure_values.get("CC", "")).resolve() != locked_clang:
            raise QualificationDriverError("CPython configure arguments do not select locked clang")
        ldflags = configure_values.get("LDFLAGS", "")
        if "-O3" not in shlex.split(cflags) or target.cpu_baseline_cflag not in shlex.split(cflags):
            raise QualificationDriverError("CPython configure arguments omit the optimized CPU baseline")
        deployment_flag = f"-mmacosx-version-min={target.deployment_target}"
        if deployment_flag not in shlex.split(cflags) or deployment_flag not in shlex.split(ldflags):
            raise QualificationDriverError("CPython configure arguments omit the macOS deployment floor")
    else:
        linux_evidence = _linux_build_evidence(
            build_run_dir,
            base_build_run_dir,
            target,
            research_driver,
        )
        locked_toolchain = None
        profile_path = None
        profile_digest = None
        profile_size = 0
        functions = 0
        maximum_count = 0
        pgo_summary = "CPython Linux PGO is disabled by product policy."
        pgo_receipt = {"verified": False, "enabled": False, "policy": "Linux PGO is disabled"}
        locked_clang = Path(linux_evidence["compiler"]["cc"]["path"]).resolve()
        llvm_version = linux_evidence["bootstrap"]["linux_toolchain"]["llvm"]["version"]
        bootstrap_identity = linux_evidence["bootstrap_identity"]
        source_revision = linux_evidence["source_revision"]
        arch = linux_arch(target)
        rust_target_cpu = arch["rust_cpu"]
        rust_target_features = ",".join(arch["rust_features"])
        if "--enable-optimizations" in configure_arguments:
            raise QualificationDriverError("Linux CPython build unexpectedly enables PGO")
        if any(
            argument in {"profile-gen", "profile-use", "profile-opt"}
            for command in linux_evidence["commands"].values()
            for argument in command["argv"]
        ):
            raise QualificationDriverError("Linux CPython build command unexpectedly enables PGO")
        configured_clang = resolve_executable(configure_values.get("CC", ""), "CPython clang")
        if configured_clang != locked_clang:
            raise QualificationDriverError("Linux CPython configure arguments do not use receipt clang")
        cflag_tokens = shlex.split(cflags)
        if "-O3" not in cflag_tokens or target.cpu_baseline_cflag not in cflag_tokens:
            raise QualificationDriverError(
                f"Linux CPython configure flags omit the candidate {arch['cpu_baseline']} floor"
            )

    pins = {entry.name: entry for entry in load_lock(COMMON_ROOT / "sources.lock.json")}
    c_names = tuple(
        name for name in dependency_order(target) if name not in {"pkgconf", "openssl"}
    )
    c_inputs = []
    cache = Cache(COMMON_ROOT / ".cache")
    for name in ("cpython", *c_names, "certifi-ca"):
        pin = pins.get(name)
        if pin is None:
            raise QualificationDriverError(f"source lock omits active input {name}")
        object_path = cache.require(pin)
        c_inputs.append(
            {
                "name": pin.name,
                "version": pin.version,
                "role": pin.role,
                "sha256": pin.sha256,
                "size": pin.size,
                "license": pin.license,
                "purpose": pin.purpose,
                "pin_identity": pin.identity(),
                "cached_bytes_sha256": sha256_file(object_path),
            }
        )

    rtls_source, hashing_source = research_driver.require_fetched_rust_inputs(build_run_dir)
    rust_graph = _rust_dependency_graph(build_run_dir, run_dir, research_driver, target)
    rust_locks = {
        "rtls": {
            "path": str((rtls_source / "rust" / "Cargo.lock").relative_to(build_run_dir)),
            "sha256": sha256_file(rtls_source / "rust" / "Cargo.lock"),
            "source_snapshot_sha256": research_driver.rtls_source_digest(rtls_source),
        },
        "hashing": {
            "path": str((hashing_source / "Cargo.lock").relative_to(build_run_dir)),
            "sha256": sha256_file(hashing_source / "Cargo.lock"),
            "source_snapshot_sha256": research_driver.hashing_source_digest(hashing_source),
        },
    }
    rust_log_paths = {
        "rtls": build_run_dir / "logs" / "rtls-cargo.log",
        "hashing": build_run_dir / "logs" / "hashing-cargo.log",
    }
    rust_logs = {}
    for name, log_path in rust_log_paths.items():
        log_text = log_path.read_text()
        linker_paths = sorted(set(re.findall(r"-C linker=([^\s]+)", log_text)))
        resolved_linkers = [
            Path(path).resolve()
            if target.is_macos
            else resolve_executable(path, f"{name} Cargo linker")
            for path in linker_paths
        ]
        if not linker_paths or any(path != locked_clang for path in resolved_linkers):
            raise QualificationDriverError(f"{name} Cargo log does not prove the locked clang linker")
        if f"-C target-cpu={rust_target_cpu}" not in log_text:
            raise QualificationDriverError(f"{name} Cargo log omits the target CPU policy")
        if not target.is_macos and (
            not all(
                feature in log_text for feature in arch["rust_features"]
            )
            or "-crt-static" not in log_text
        ):
                raise QualificationDriverError(f"{name} Cargo log omits the {arch['machine']} feature policy")
        extension_name = "_rustls" if name == "rtls" else "_hashlib_graviola"
        extension_commands = [
            line for line in log_text.splitlines()
            if "--crate-name " + extension_name in line
        ]
        if len(extension_commands) != 1:
            raise QualificationDriverError(f"{name} Cargo log does not identify one extension link command")
        extension_command = extension_commands[0]
        required_flags = (
            "-C opt-level=3",
            "-C lto=thin",
            "-C codegen-units=1",
            f"-C target-cpu={rust_target_cpu}",
        )
        for required_flag in required_flags:
            if required_flag not in extension_command:
                raise QualificationDriverError(f"{name} extension command omits {required_flag}")
        if not target.is_macos and not re.search(
            rf"(?:^|\s)-L native=\S*/rust-static-unwinder/{re.escape(target.triple)}(?:\s|$)",
            extension_command,
        ):
            raise QualificationDriverError(f"{name} extension command omits the static LLVM unwinder")
        if name == "rtls" and target.is_macos and "-C link-arg=dynamic_lookup" not in extension_command:
            raise QualificationDriverError("RTLS extension command omits its dynamic lookup link policy")
        rust_logs[name] = {
            "path": str(log_path),
            "sha256": sha256_file(log_path),
            "linker_paths": (
                linker_paths
                if target.is_macos
                else sorted({str(path) for path in resolved_linkers})
            ),
            "target_cpu": rust_target_cpu,
            "target_features": rust_target_features,
            "deployment_target": target.deployment_target if target.is_macos else None,
            "release_profile": "opt-level=3, thin LTO, one codegen unit, no debug info, unwind",
            "extension_command": extension_command,
            "extension_dynamic_lookup": "-C link-arg=dynamic_lookup" in extension_command,
        }
        if not target.is_macos:
            rust_logs[name]["linker_tokens"] = linker_paths

    make_log = base_build_run_dir / "logs" / "cpython-build.log"
    overlay_log = build_run_dir / "driver-overlay.log"
    if not config_log.is_file():
        raise QualificationDriverError("CPython configure record is missing")
    if target.is_macos and (not make_log.is_file() or not overlay_log.is_file()):
        raise QualificationDriverError("macOS build records are missing CPython or overlay logs")
    cargo, rustc, kache = research_driver.rust_toolchain()
    rustc_result = subprocess.run(
        [str(rustc), "-vV"], capture_output=True, text=True, timeout=30, check=True
    )
    cargo_result = subprocess.run(
        [str(cargo), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    if not target.is_macos:
        rust_lock = linux_evidence["rust_bootstrap"]["rust"]
        rust_version = rust_lock["version"]
        rust_host = rust_lock["host"]
        rustc_lines = rustc_result.stdout.splitlines()
        cargo_lines = cargo_result.stdout.splitlines()
        observed_host = [
            line.partition(":")[2].strip()
            for line in rustc_lines
            if line.startswith("host:")
        ]
        if (
            not rustc_lines
            or rustc_lines[0].split()[:2] != ["rustc", rust_version]
            or observed_host != [rust_host]
            or not cargo_lines
            or cargo_lines[0].split()[:2] != ["cargo", rust_version]
        ):
            raise QualificationDriverError("observed Rust tools differ from the locked native Rust toolchain")
        for name, source in zip(("rtls", "hashing"), linux_evidence["rust_sources"]):
            expected_log = f"logs/{name}-cargo.log"
            commands = [
                command
                for command in linux_evidence["commands"].values()
                if command["log"]["path"] == expected_log
            ]
            if len(commands) != 1:
                raise QualificationDriverError(f"{name} build receipt has no unique Cargo command")
            argv = commands[0]["argv"]
            manifest = source / ("rust/Cargo.toml" if name == "rtls" else "Cargo.toml")
            manifest_index = argv.index("--manifest-path") if "--manifest-path" in argv else -1
            if (
                len(argv) < 2
                or Path(argv[0]).resolve() != cargo.resolve()
                or argv[1] != "build"
                or not {"--release", "--verbose", "--offline", "--locked",
                        *research_driver.LINUX_RUST_BUILD_STD}.issubset(argv)
                or [argv[index + 1:index + 2] for index, value in enumerate(argv) if value == "--target"]
                != [[target.triple]]
                or manifest_index < 0
                or manifest_index + 1 >= len(argv)
                or Path(argv[manifest_index + 1]).resolve() != manifest.resolve()
            ):
                raise QualificationDriverError(f"{name} Cargo command does not prove a native locked release build")
            extension_command = rust_logs[name]["extension_command"]
            if (
                str(rustc) not in extension_command
                or not re.search(rf"(?:^|\s)--target[= ]{re.escape(target.triple)}(?:\s|$)", extension_command)
                or (kache is not None and str(kache) not in extension_command)
            ):
                raise QualificationDriverError(f"{name} compile log does not prove a native locked rustc build")
            rust_logs[name]["cargo_command"] = argv
            rust_logs[name]["host_target"] = observed_host[0]
    if target.is_macos:
        clang = locked_toolchain.llvm_prefix / "bin" / "clang"
        clang_result = subprocess.run(
            [str(clang), "--version"], capture_output=True, text=True, timeout=30, check=True
        )
        if locked_toolchain.llvm_version not in clang_result.stdout.splitlines()[0]:
            raise QualificationDriverError("observed clang version differs from the locked LLVM version")
        toolchain_observed = locked_toolchain.observed_facts()
        toolchain_observed["clang"] = {
            "path": str(clang),
            "version": clang_result.stdout.splitlines()[0].strip(),
        }
    else:
        clang = locked_clang
        toolchain_observed = {
            "family": target.family,
            "cpu_baseline": target.cpu_baseline_cflag,
            "commands": linux_evidence["compiler"],
        }
    rust_observed = {
        "rustc_path": str(rustc),
        "rustc_verbose_version": rustc_result.stdout.strip(),
        "cargo_path": str(cargo),
        "cargo_version": cargo_result.stdout.strip(),
        "linker_paths": sorted({path for entry in rust_logs.values() for path in entry["linker_paths"]}),
    }
    if kache is not None:
        rust_observed["kache_path"] = str(kache)
    if not target.is_macos:
        rust_observed["native_host_target"] = rust_logs["rtls"]["host_target"]
        rust_observed["bootstrap_lock_sha256"] = linux_evidence["rust_bootstrap_identity"]
    toolchain_observed["rust"] = rust_observed

    facade_paths = (
        "lib/python3.15/ssl.py",
        "lib/python3.15/_ssl.py",
        "lib/python3.15/_ssl_metadata.py",
        "lib/python3.15/_hashlib.py",
    )
    native_paths = candidate_runtime_identity_paths(extracted_prefix, target)
    runtime_files = {
        relative: sha256_file(extracted_prefix / relative)
        for relative in native_paths
        if (extracted_prefix / relative).is_file()
    }
    if set(runtime_files) != set(native_paths):
        raise QualificationDriverError("extracted archive is missing a required runtime identity file")
    facade_hashes = {relative: runtime_files[relative] for relative in facade_paths}

    if not target.is_macos:
        recorded_c_inputs = linux_evidence["source"].get("c_inputs")
        expected_c_inputs = [
            {
                key: item[key]
                for key in (
                    "name", "version", "sha256", "size", "role",
                    "license", "purpose", "cached_bytes_sha256",
                )
            }
            for item in c_inputs
        ]
        if recorded_c_inputs != expected_c_inputs:
            raise QualificationDriverError("Linux build receipt inputs differ from the locked dependency closure")

    patch_paths = sorted((COMMON_ROOT / "patches" / "cpython").glob("*.patch"))
    cpython_patches = [
        {"name": path.name, "sha256": sha256_file(path)} for path in patch_paths
    ]
    if not target.is_macos:
        cpython_patches.extend(linux_evidence["source"]["cpython_experimental_patches"])
    rtls_patch_names = (
        *research_driver.RTLS_PATCHES,
        "webpki-policy.patch",
        "webpki-crl-policy.patch",
        "webpki-strict-policy.patch",
        "rustls-session-api.patch",
    )
    rtls_patches = []
    for name in rtls_patch_names:
        path = REPO / "backend" / name
        if not path.is_file():
            raise QualificationDriverError(f"RTLS source patch is missing: {name}")
        rtls_patches.append({"name": name, "sha256": sha256_file(path)})
    helper_paths = (
        REPO / "backend" / "artifact_layout.py",
        REPO / "backend" / "build-no-openssl.py",
        REPO / "backend" / "prepare_webpki.py",
        REPO / "backend" / "prepare_rustls.py",
        REPO / "backend" / "ssl_facade.py",
        REPO / "backend" / "ssl_native_compat.py",
        REPO / "backend" / "ssl_metadata.py",
        REPO / "backend" / "modern_compat.py",
        REPO / "backend" / "MODERN_TLS_POLICY.md",
        REPO / "backend" / "certificate_contract.py",
        REPO / "backend" / "modern_tls_contract.py",
        REPO / "backend" / "test_ssl_handshake_error_cleanup.py",
        REPO / "backend" / "trust_policy_contract.py",
        REPO / "backend" / "qualify-no-openssl-archive.py",
        REPO / "backend" / "qualify-no-openssl-regressions.py",
        REPO / "backend" / "regression_policy.py",
        REPO / "backend" / "rust_licenses.py",
        REPO / "backend" / "hashing" / "compare-reference.py",
    )
    experimental_sources = [
        {"name": path.name, "sha256": sha256_file(path)} for path in helper_paths
    ]
    common_helper_paths = (
        COMMON_ROOT / "build" / "package.py",
        COMMON_ROOT / "buildsys" / "package.py",
        COMMON_ROOT / "buildsys" / "elf.py",
        COMMON_ROOT / "buildsys" / "product_process.py",
        COMMON_ROOT / "buildsys" / "scope.py",
        COMMON_ROOT / "buildsys" / "standalone_compat.py",
        COMMON_ROOT / "buildsys" / "testsuite.py",
        COMMON_ROOT / "buildsys" / "validate_product.py",
        COMMON_ROOT / "buildsys" / "relocate.py",
        COMMON_ROOT / "buildsys" / "targets.py",
    )
    common_helper_hashes = {
        path.relative_to(COMMON_ROOT).as_posix(): sha256_file(path)
        for path in common_helper_paths
    }

    build_record = {
        "source": {
            "revision": source_revision,
            "cpython": next(item for item in c_inputs if item["name"] == "cpython"),
            "target": target.triple,
            "dependency_graph": [item["name"] for item in c_inputs],
            "excluded_components": ["openssl"],
        },
        "patches": {
            "cpython": cpython_patches,
            "rust": rtls_patches,
            "research_sources": experimental_sources,
        },
        "dependencies": {
            "cpython_native": c_inputs,
            "rust_active_closure": rust_graph,
        },
        "toolchain_observed": toolchain_observed,
        "optimization": {
            "cpython": {
                "pgo": target.pgo,
                "profile_sha256": profile_digest,
                "profile_bytes": int(profile_size),
                "profile_functions": int(functions),
                "profile_maximum_count": int(maximum_count),
                "profile_task": make_variables.get("PROFILE_TASK", ""),
            },
            "rust_extensions": rust_logs,
        },
        "effective_flags": {
            "cpython_make_variables": make_variables,
            "target_cpu_baseline": target.cpu_baseline_cflag,
            "macos_deployment_minimum": target.deployment_target if target.is_macos else None,
            "rust": {
                "target_cpu": rust_target_cpu,
                "target_features": rust_target_features,
                "deployment_target": target.deployment_target if target.is_macos else None,
                "release_profile": "opt-level=3, thin LTO, one codegen unit, no debug info, unwind",
            },
        },
        "commands": (
            {
                "cpython_build_log": {"path": str(make_log), "sha256": sha256_file(make_log)},
                "cpython_config_log": {"path": str(config_log), "sha256": sha256_file(config_log)},
                "rust_build_logs": rust_logs,
                "overlay_log": {"path": str(overlay_log), "sha256": sha256_file(overlay_log)},
                "pgo_verification": pgo_summary.strip().splitlines(),
            }
            if target.is_macos
            else {
                "linux_build_receipts": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in linux_evidence["build_receipts"]
                ],
                "docker_build_receipts": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in linux_evidence["docker_receipts"]
                ],
                "commands": linux_evidence["commands"],
            }
        ),
        "build_mode": (
            "sealed macOS sandbox, network access denied during adapter builds"
            if target.is_macos
            else f"sealed native Linux {target.machine} Docker network-none build"
        ),
        "isolation": (
            {
                "buildsys_sealed_run": True,
                "network_boundary_selftest_before_and_after_overlay": True,
                "build_output_root": str(build_run_dir),
                "candidate_build_log": str(overlay_log),
            }
            if target.is_macos
            else {
                "build_receipts": [
                    item["receipt"]["isolation"]
                    for item in linux_evidence["build_receipts"]
                ],
                "docker_receipts": [
                    item["receipt"] for item in linux_evidence["docker_receipts"]
                ],
            }
        ),
    }
    recipe_material = {
        "experimental": "openssl-free-modern-rtls-graviola",
        "target": target.triple,
        "source_graph": build_record["source"]["dependency_graph"],
        "patches": build_record["patches"],
        "effective_flags": build_record["effective_flags"],
        "rust_locks": rust_locks,
        "pgo_profile_sha256": profile_digest,
        "qualification_helper_sha256": common_helper_hashes,
    }
    recipe_identity = hashlib.sha256(canonical_json(recipe_material).encode()).hexdigest()
    manifest = build_manifest(
        product=PRODUCT,
        target=target,
        source_revision=build_record["source"]["revision"],
        recipe_identity=recipe_identity,
        bootstrap_identity=bootstrap_identity,
        llvm_version=llvm_version,
        archive_filename=archive_path.name,
        archive=archive_facts,
        qualification=qualification,
        lock_path=COMMON_ROOT / "sources.lock.json",
        build_record=build_record,
        package_patches_directory=COMMON_ROOT / "patches" / "cpython",
    )
    manifest["components"]["rust_backends"] = [
        item for item in rust_graph if item["name"] in {"rustls", "rustls-webpki", "rustls-graviola", "graviola"}
    ]
    if any(item["name"] == "openssl" or item["name"].startswith(("openssl-", "libssl", "libcrypto")) for item in rust_graph):
        raise QualificationDriverError("experimental artifact manifest found an OpenSSL Rust component")
    native_check_name = "validation.macho" if target.is_macos else "linux_elf_validation"
    native_check = qualification.get("checks", {}).get(native_check_name)
    if not isinstance(native_check, dict) or native_check.get("status") != "passed":
        raise QualificationDriverError("experimental artifact has no passing native image report")
    symbol_check = qualification.get("checks", {}).get("no_openssl_symbols")
    if not isinstance(symbol_check, dict) or symbol_check.get("status") != "passed":
        raise QualificationDriverError("experimental artifact has no passing OpenSSL symbol audit")

    manifest["experimental"] = {
        "status": "research artifact; not a production qualification",
        "tls_profile": "modern TLS 1.2/1.3 only",
        "production_default_changed": False,
        "source_prefix": str(source_prefix),
        "tested_prefix": regression_check.get("tested_prefix"),
        "runtime_identity_sha256": runtime_files,
        "facade_sha256": facade_hashes,
        "native_c_inputs": c_inputs,
        "rust_active_dependency_closure": rust_graph,
        "rust_source": {
            "rtls_revision": research_driver.RTLS_REVISION,
            "rtls_vendor_sha256": research_driver.rtls_source_digest(
                research_driver.RTLS_VENDOR
            ),
            "snapshots_and_locks": rust_locks,
            "patches": rtls_patches,
        },
        "hashing_source": rust_locks["hashing"],
        "pgo_receipt": {
            **pgo_receipt,
            "summary": pgo_summary.strip().splitlines(),
        },
        "runtime_floor_claim": (
            f"macOS {target.deployment_target} is the deployment minimum; "
            f"runtime tested on macOS {platform.mac_ver()[0]}"
            if target.is_macos
            else f"native Linux {target.machine} musl runtime tested in the locked Docker image"
        ),
        "native_images": native_check.get("checks", native_check),
        "native_symbol_audit": symbol_check,
        "research_checkout_revision": _load_source_revision(REPO),
        "qualification_helper_sha256": common_helper_hashes,
        "regression_evidence": {
            "report": regression_check["source_report"],
            "report_sha256": regression_check["source_report_sha256"],
            "suite": regression_check["suite"],
            "candidate_identity": regression_check["candidate_identity"],
            "ssl_log_evidence": regression_check["ssl_log_evidence"],
            "diagnostics": regression_check["report"].get("diagnostics"),
            "modern_policy": regression_check["report"].get("modern_policy"),
            "ssl_test_inventory": regression_check["ssl_test_inventory"],
        },
        "compatibility": {
            "raw_openssl_profile": {
                "ok": raw_compatibility.get("ok"),
                "failed": raw_compatibility.get("aggregate", {}).get("failed"),
                "ssl_capabilities": raw_compatibility.get("checks", {}).get("ssl_capabilities"),
                "aggregate": raw_compatibility.get("aggregate"),
            },
            "effective_modern_profile": {
                "ok": effective_compatibility.get("ok"),
                "failed": effective_compatibility.get("aggregate", {}).get("failed"),
                "modern_tls_capabilities": effective_compatibility.get("checks", {}).get("modern_tls_capabilities"),
                "aggregate": effective_compatibility.get("aggregate"),
            },
            "modern_policy_exclusions": regression_check["report"].get("modern_policy", {}).get("exclusions"),
        },
    }
    if target.is_macos:
        manifest["experimental"]["host_macos_version"] = platform.mac_ver()[0]
    else:
        manifest["experimental"]["docker_build_receipts"] = [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in linux_evidence["docker_receipts"]
        ]
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive and validate an existing no-OpenSSL candidate prefix"
    )
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--build-run-dir", type=Path)
    parser.add_argument("--base-build-run-dir", type=Path)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--certdata", type=Path, required=True)
    parser.add_argument("--regression-report", type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--archive-name", default=None)
    parser.add_argument("--manifest-name", default=None)
    arguments = parser.parse_args(argv)
    archive_name = arguments.archive_name or ARCHIVE_NAME
    manifest_name = arguments.manifest_name or MANIFEST_NAME
    if Path(archive_name).name != archive_name or Path(manifest_name).name != manifest_name:
        raise QualificationDriverError("archive and manifest names must be plain filenames")

    try:
        source_prefix = require_product_work_path(arguments.prefix, "candidate prefix")
        run_dir = require_work_path(arguments.run_dir, "qualification run directory")
        build_run_dir = require_product_work_path(
            arguments.build_run_dir or source_prefix.parent, "candidate build directory"
        )
        base_build_run_dir = require_product_work_path(
            arguments.base_build_run_dir or build_run_dir,
            "candidate base build directory",
        )
        reference_python = Path(arguments.reference_python).resolve()
        certdata = Path(arguments.certdata).resolve()
        regression_report_path = (
            Path(arguments.regression_report).resolve()
            if arguments.regression_report is not None
            else None
        )
        if not source_prefix.is_dir():
            raise QualificationDriverError(f"candidate prefix does not exist: {source_prefix}")
        if not reference_python.is_file() or not os.access(reference_python, os.X_OK):
            raise QualificationDriverError(f"reference interpreter is missing or not executable: {reference_python}")
        if reference_python.name != "python3.15":
            raise QualificationDriverError(f"reference interpreter must be named python3.15: {reference_python}")
        if not certdata.is_dir() or not (certdata / "keycert3.pem").is_file() or not (certdata / "pycacert.pem").is_file():
            raise QualificationDriverError(f"certificate data directory is incomplete: {certdata}")
        host_target = native_target()
        if host_target.triple not in {
            "aarch64-apple-darwin",
            "aarch64-unknown-linux-musl",
            "x86_64-unknown-linux-musl",
        }:
            raise QualificationDriverError(
                f"this experimental qualification supports native macOS arm64 and Linux musl targets only: {host_target.triple}"
            )
        if run_dir.exists():
            raise QualificationDriverError(f"refusing to replace qualification directory: {run_dir}")
        overlapping_paths = any(
            left.is_relative_to(right) or right.is_relative_to(left)
            for left, right in (
                (run_dir, source_prefix),
                (run_dir, build_run_dir),
                (run_dir, base_build_run_dir),
            )
        )
        if overlapping_paths:
            expected_nested_output = (build_run_dir / "qualification" / "archive").resolve()
            protected_build_paths = (
                source_prefix,
                *(build_run_dir / name for name in (
                    "private",
                    "prefix",
                    "relocated prefix",
                    "cpython-source",
                    "cpython-build",
                    "cargo-home",
                    "cargo-target",
                    "rtls-build",
                    "hashing-build",
                    "inputs",
                    "logs",
                )),
            )
            protected_overlap = any(
                run_dir.is_relative_to(path) or path.is_relative_to(run_dir)
                for path in protected_build_paths
            )
            distinct_base_overlap = base_build_run_dir != build_run_dir and (
                run_dir.is_relative_to(base_build_run_dir)
                or base_build_run_dir.is_relative_to(run_dir)
            )
            if (
                host_target.triple not in LINUX_ARCHITECTURES
                or run_dir != expected_nested_output
                or protected_overlap
                or distinct_base_overlap
            ):
                raise QualificationDriverError(
                    "candidate, build, and qualification paths must not overlap"
                )
        research_driver = load_research_driver()
        target = research_driver.experimental_target()
        if not PRODUCT.interpreter(source_prefix).is_file():
            raise QualificationDriverError(
                f"candidate interpreter is missing: {PRODUCT.interpreter(source_prefix)}"
            )
        run_dir.mkdir(parents=True)
        package_work = run_dir / "prepared package tree"
        extracted_parent = run_dir / "archive validation with spaces"
        validation_work = run_dir / "native product validation"
        compatibility_work = run_dir / "standalone compatibility scratch"
        host_tmp = run_dir / "host temporary files"
        regression_inventory_work = run_dir / "test_ssl inventory validation"
        for path in (validation_work, compatibility_work, host_tmp, regression_inventory_work):
            path.mkdir()
        tempfile.tempdir = str(host_tmp)
        scope = {
            "result_is_production_qualification": False,
            "full_cpython_regression_suite": "required from supplied pre-prune regression report",
        }
        if target.is_macos:
            scope.update(
                production_pgo="deferred by product policy",
                minimum_macos_runtime=f"macOS {target.deployment_target} host",
                host_macos_version=platform.mac_ver()[0],
            )
        else:
            scope.update(
                production_pgo="disabled by Linux product policy",
                linux_runtime=f"native Linux {target.machine} musl Docker runtime",
            )

        report: dict = {
            "candidate_prefix": str(source_prefix),
            "candidate_build_directory": str(build_run_dir),
            "candidate_base_build_directory": str(base_build_run_dir),
            "reference_python": str(reference_python),
            "certdata": str(certdata),
            "pre_prune_regression_report": (
                str(regression_report_path) if regression_report_path is not None else None
            ),
            "target": target.triple,
            "shared_lock_root": str(COMMON_ROOT),
            "scope": scope,
            "checks": {},
        }

        copy_product_tree(source_prefix, package_work)
        report["checks"]["private_package_copy"] = check_result(
            True, path=str(package_work), source_preserved=True
        )

        try:
            pre_prune_testcapi = testcapi_modules_pre_prune(
                PRODUCT.interpreter(package_work), package_work
            )
        except Exception as error:  # noqa: BLE001
            pre_prune_testcapi = {
                "status": "failed",
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            }
        report["checks"]["pre_prune_testcapi"] = check_result(
            pre_prune_testcapi.get("ok") is True,
            evidence=pre_prune_testcapi.get("evidence"),
            error=pre_prune_testcapi.get("error"),
        )

        try:
            research_driver.verify_overlay_prefix(package_work, build_run_dir, target)
            report["checks"]["no_openssl_overlay"] = check_result(True)
        except Exception as error:  # noqa: BLE001
            report["checks"]["no_openssl_overlay"] = check_result(
                False, error_type=type(error).__name__, error=str(error)
            )

        linux_evidence = None
        if not target.is_macos:
            linux_evidence = _linux_build_evidence(
                build_run_dir,
                base_build_run_dir,
                target,
                research_driver,
            )
            report["linux_build_evidence"] = {
                "build_receipts": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in linux_evidence["build_receipts"]
                ],
                "docker_receipts": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in linux_evidence["docker_receipts"]
                ],
                "source_revision": linux_evidence["source_revision"],
                "cpu_baseline": target.cpu_baseline_cflag,
                "pgo": False,
            }

        package_driver = load_package_driver()
        package_driver.REPO = COMMON_ROOT

        try:
            if target.is_macos:
                stripped = package_driver.strip_tree_macos(package_work)
                report["checks"]["strip"] = check_result(
                    True, mach_o_images_stripped=len(stripped)
                )
            else:
                strip = linux_evidence["compiler"]["strip"]["path"]
                stripped = package_driver.strip_tree(package_work, strip=strip)
                report["checks"]["strip"] = check_result(
                    True, elf_images_stripped=len(stripped), tool=strip
                )
        except Exception as error:  # noqa: BLE001
            report["checks"]["strip"] = check_result(
                False, error_type=type(error).__name__, error=str(error)
            )

        try:
            report["checks"]["distribution_prune"] = prune_distribution_payload(package_work)
        except (ScopeError, OSError) as error:
            report["checks"]["distribution_prune"] = check_result(
                False, error_type=type(error).__name__, error=str(error)
            )

        archive_path = run_dir / archive_name
        try:
            archive_facts = create_archive(package_work, archive_path)
            extracted = verify_archive_extraction(package_work, archive_path, extracted_parent)
            report["checks"]["archive_roundtrip"] = check_result(
                True,
                archive=str(archive_path),
                archive_sha256=archive_facts.sha256,
                archive_size_bytes=archive_facts.size_bytes,
                payload_sha256=archive_facts.payload_sha256,
                payload_size_bytes=archive_facts.payload_size_bytes,
                extracted_prefix=str(extracted),
            )
            report["archive_artifact"] = {
                "path": str(archive_path),
                "sha256": archive_facts.sha256,
                "payload_sha256": archive_facts.payload_sha256,
                "size_bytes": archive_facts.size_bytes,
                "payload_size_bytes": archive_facts.payload_size_bytes,
            }
            size_report = payload_size_report(extracted)
            write_json(run_dir / "size-report.json", size_report)
            report["checks"]["size_inventory"] = check_result(
                True,
                total_files=size_report["total_files"],
                total_bytes=size_report["total_bytes"],
                largest_files=size_report["largest_files"][:5],
            )
        except (PackageError, OSError) as error:
            report["checks"]["archive_roundtrip"] = check_result(
                False, error_type=type(error).__name__, error=str(error)
            )
            report["ok"] = False
            report_path = run_dir / REPORT_NAME
            write_report(report, report_path)
            print(f"REPORT {report_path}")
            return 1

        validation = run_validation(extracted, validation_work, target)
        report["validation"] = validation
        write_json(run_dir / "product validation.json", validation)
        if not target.is_macos:
            try:
                linux_validation = package_driver._linux_validation(extracted)
            except Exception as error:  # noqa: BLE001
                linux_validation = {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            if isinstance(linux_validation.get("checks"), dict):
                validation["checks"].update(linux_validation["checks"])
                validation["failed"] = sorted(
                    name for name, result in validation["checks"].items()
                    if result.get("ok") is not True
                )
                validation["ok"] = not validation["failed"]
                write_json(run_dir / "product validation.json", validation)
            report["linux_elf_validation"] = linux_validation
            report["checks"]["linux_elf_validation"] = linux_validation
            write_json(run_dir / "Linux ELF validation.json", linux_validation)
        try:
            from buildsys import standalone_compat

            standalone_compat.SCRATCH_ROOT = compatibility_work
            compatibility = run_standalone_compatibility(
                PRODUCT.interpreter(extracted), extracted, target,
                skip_testcapi=True,
            )
        except Exception as error:  # noqa: BLE001
            compatibility = {
                "ok": False,
                "checks": {
                    "aggregate": {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                },
            }
        report["compatibility"] = compatibility
        write_json(run_dir / "raw compatibility report.json", compatibility)

        try:
            rtls_source, _ = research_driver.require_fetched_rust_inputs(build_run_dir)
            effective_compatibility, modern_capability = modern_compatibility_profile(
                compatibility,
                PRODUCT.interpreter(extracted),
                extracted,
                rtls_source,
                compatibility_work,
            )
        except Exception as error:  # noqa: BLE001
            modern_capability = {
                "ok": False,
                "evidence": {},
                "error": f"{type(error).__name__}: {error}",
            }
            effective_compatibility = {
                "ok": False,
                "checks": {
                    "modern_tls_capabilities": {
                        "status": "failed",
                        "error": modern_capability["error"],
                    }
                },
                "aggregate": {
                    "check_count": 1,
                    "required_check_count": 1,
                    "failed": ["modern_tls_capabilities"],
                    "ok": False,
                },
            }
        report["effective_compatibility"] = effective_compatibility
        write_json(run_dir / "effective compatibility report.json", effective_compatibility)
        report["checks"]["modern_tls_capabilities"] = check_result(
            modern_capability.get("ok") is True,
            evidence=modern_capability.get("evidence"),
            error=modern_capability.get("error"),
        )

        try:
            contracts = run_archive_contracts(
                extracted,
                reference_python,
                certdata,
                run_dir,
                target,
            )
            report["checks"].update(contracts)
        except Exception as error:  # noqa: BLE001
            report["checks"]["post_extract_contracts"] = check_result(
                False,
                error_type=type(error).__name__,
                error=str(error),
            )

        regression_check = validate_regression_report(
            regression_report_path,
            source_prefix,
            target,
            base_build_run_dir
            / "cpython-source"
            / f"Python-{PRODUCT.version_string}"
            / "Lib"
            / "test"
            / "test_ssl.py",
            regression_inventory_work,
            reference_python,
        )
        report["regression"] = {
            key: value for key, value in regression_check.items() if key != "report"
        }
        if "report" in regression_check:
            report["regression_report"] = regression_check["report"]
        report["checks"]["pre_prune_regression"] = {
            key: value for key, value in regression_check.items() if key != "report"
        }

        report["qualification"] = qualify(
            validation,
            effective_compatibility,
            additional_checks={
                name: result
                for name, result in report["checks"].items()
            },
        )
        report["ok"] = report["qualification"]["ok"]

        manifest_path = run_dir / manifest_name
        if report["ok"]:
            report["qualification"]["checks"]["experimental_artifact_manifest"] = {
                "status": "passed",
                "path": str(manifest_path),
            }
            try:
                manifest = build_experimental_manifest(
                    archive_facts=archive_facts,
                    archive_path=archive_path,
                    source_prefix=source_prefix,
                    extracted_prefix=extracted,
                    build_run_dir=build_run_dir,
                    base_build_run_dir=base_build_run_dir,
                    run_dir=run_dir,
                    target=target,
                    qualification=report["qualification"],
                    regression_check=regression_check,
                    raw_compatibility=compatibility,
                    effective_compatibility=effective_compatibility,
                    research_driver=research_driver,
                )
                staged_manifest = run_dir / f".{manifest_name}.pending"
                write_manifest(staged_manifest, manifest)
                os.replace(staged_manifest, manifest_path)
                report["artifact_manifest"] = {
                    "path": str(manifest_path),
                    "sha256": sha256_file(manifest_path),
                }
            except Exception as error:  # noqa: BLE001
                report["qualification"]["checks"]["experimental_artifact_manifest"] = {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
                report["qualification"]["ok"] = False
                report["ok"] = False

        report_path = run_dir / REPORT_NAME
        write_report(report, report_path)
        failures = sorted(
            name
            for name, value in report["qualification"]["checks"].items()
            if value.get("status") == "failed"
        )
        print(f"REPORT {report_path}")
        print(f"ARCHIVE {archive_path}")
        print(f"EXPERIMENTAL ARCHIVE CHECKS {'PASS' if report['ok'] else 'FAIL'}")
        for failure in failures:
            detail = report["qualification"]["checks"][failure]
            print(f"FAIL {failure}: {detail.get('error', detail)}")
        return 0 if report["ok"] else 1
    except Exception as error:  # noqa: BLE001
        print(f"FAIL qualification driver: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
