from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

from artifact_layout import ARCHIVE_NAME, MANIFEST_NAME, REPORT_NAME
from buildsys.inputs import input_sources

ARCH_CONFIGS = {
    "aarch64-unknown-linux-musl": {
        "triple": "aarch64-unknown-linux-musl",
        "platform": "linux/arm64",
        "machine": "aarch64",
        "docker_arch": "arm64",
        "apk_arch": "arm64",
        "bootstrap_lock": "linux-bootstrap.lock.json",
    },
    "x86_64-unknown-linux-musl": {
        "triple": "x86_64-unknown-linux-musl",
        "platform": "linux/amd64",
        "machine": "x86_64",
        "docker_arch": "amd64",
        "apk_arch": "amd64",
        "bootstrap_lock": "linux-bootstrap-x86_64.json",
    },
}
TARGET = "aarch64-unknown-linux-musl"
DOCKER_PLATFORM = "linux/arm64"
MACHINE = "aarch64"
DOCKER_ARCH = "arm64"
APK_ARCH = "arm64"
LOCK_PATH = REPO / "backend" / "linux-bootstrap.lock.json"
DOCKERFILE = REPO / "backend" / "Dockerfile.linux"


def configure(triple: str) -> None:
    global TARGET, DOCKER_PLATFORM, MACHINE, DOCKER_ARCH, APK_ARCH, LOCK_PATH, DOCKERFILE
    try:
        selected = ARCH_CONFIGS[triple]
    except KeyError:
        raise DockerBuildError(f"unsupported Linux OpenSSL-free target: {triple}")
    TARGET = selected["triple"]
    DOCKER_PLATFORM = selected["platform"]
    MACHINE = selected["machine"]
    DOCKER_ARCH = selected["docker_arch"]
    APK_ARCH = selected["apk_arch"]
    LOCK_PATH = REPO / "backend" / selected["bootstrap_lock"]
    DOCKERFILE = REPO / "backend" / "Dockerfile.linux"
REFERENCE_DRIVER = REPO / "backend" / "build-linux-reference.py"
REGRESSION_DRIVER = REPO / "backend" / "qualify-no-openssl-regressions.py"
ARCHIVE_DRIVER = REPO / "backend" / "qualify-no-openssl-archive.py"
BUILD_DRIVER = REPO / "backend" / "build-no-openssl.py"
QUALIFICATION_VOLUME_TARGET = "/work/.work/tmp/q"
CHUNK_SIZE = 1 << 20


class DockerBuildError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def checked_run(argv: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise DockerBuildError(
            f"command failed ({result.returncode}): {argv!r}\n{result.stdout}{result.stderr}"
        )
    return result.stdout.strip()


def run_logged(
    argv: list[str],
    log_path: Path,
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")
    output: list[str] = []
    with log_path.open("a") as log:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            if process.stdout is None:
                raise DockerBuildError("failed to capture command output")
            for line in process.stdout:
                output.append(line)
                log.write(line)
                log.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            status = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if status:
        raise DockerBuildError(
            f"command failed ({status}); see {log_path}: {argv!r}"
        )
    return "".join(output)


def git_output(*arguments: str) -> str:
    return checked_run(["git", *arguments], cwd=REPO)


def common_root() -> Path:
    git_dir = Path(git_output("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return git_dir.resolve().parent


def source_identity(requested_revision: str | None) -> tuple[str, bool]:
    revision = git_output("rev-parse", "HEAD")
    dirty = bool(git_output("status", "--porcelain=v1", "--untracked-files=all"))
    if requested_revision is not None:
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", requested_revision):
            raise DockerBuildError("--source-revision must be a full hexadecimal commit hash")
        if revision != requested_revision:
            raise DockerBuildError(
                f"checkout revision {revision} differs from requested {requested_revision}"
            )
        if dirty:
            raise DockerBuildError("an explicit --source-revision requires a clean checkout")
    return revision, dirty


def require_work_dir(path: Path, *, fresh: bool) -> Path:
    work_root = REPO / ".work" / "tmp"
    work_parent = REPO / ".work"
    if work_parent.is_symlink() or work_root.is_symlink():
        raise DockerBuildError("working scratch directories cannot be symlinks")
    exclude = Path(git_output("rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
    try:
        excluded_patterns = exclude.read_text().splitlines()
    except OSError as error:
        raise DockerBuildError(f"cannot read Git's local exclude file: {exclude}") from error
    if not any(pattern.strip() in {".work/", "/.work/"} for pattern in excluded_patterns):
        raise DockerBuildError(".work/ must be ignored through .git/info/exclude before use")
    if subprocess.run(["git", "check-ignore", "-q", ".work/tmp"], cwd=REPO, check=False).returncode:
        raise DockerBuildError(".work/tmp must be ignored through .git/info/exclude before use")
    work_root.mkdir(parents=True, exist_ok=True)
    lexical = Path(os.path.abspath(path if path.is_absolute() else REPO / path))
    if not lexical.is_relative_to(work_root):
        raise DockerBuildError(f"run directory must stay under {work_root}: {lexical}")
    current = work_root
    for part in lexical.relative_to(work_root).parts:
        current = current / part
        if current.is_symlink():
            raise DockerBuildError(f"run directory cannot contain symlinks: {current}")
    if fresh and lexical.exists() and any(lexical.iterdir()):
        raise DockerBuildError(f"--phase all requires an empty run directory: {lexical}")
    lexical.mkdir(parents=True, exist_ok=True)
    return lexical.resolve(strict=True)


def load_locks() -> tuple[dict, dict, str]:
    try:
        bootstrap = json.loads((REPO / "bootstrap.lock.json").read_text())
        experimental = json.loads(LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise DockerBuildError(f"cannot read the locked Linux toolchain inputs: {error}") from error
    try:
        image = bootstrap["linux_toolchain"]["images"][DOCKER_ARCH]
        digest = image["digest"]
        if image["platform"] != DOCKER_PLATFORM:
            raise KeyError("unexpected platform")
        if experimental["target"] != {
            "triple": TARGET,
            "platform": DOCKER_PLATFORM,
            "architecture": MACHINE,
        }:
            raise KeyError("unexpected experimental target")
    except (KeyError, TypeError) as error:
        raise DockerBuildError(f"Linux bootstrap locks do not describe native {MACHINE}") from error
    return bootstrap, experimental, digest


def docker_daemon() -> dict:
    raw = checked_run(["docker", "info", "--format", "{{.OSType}}|{{.Architecture}}"])
    try:
        operating_system, architecture = raw.split("|", 1)
    except ValueError as error:
        raise DockerBuildError(f"cannot parse Docker daemon identity: {raw!r}") from error
    normalized = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(architecture)
    if operating_system != "linux" or normalized != DOCKER_ARCH:
        raise DockerBuildError(
            f"a native Linux {MACHINE} Docker daemon is required, found {operating_system}/{architecture}"
        )
    return {
        "daemon_os": operating_system,
        "daemon_architecture": architecture,
        "daemon_platform": DOCKER_PLATFORM,
    }


def verify_file(path: Path, expected_hash: str, expected_size: int | None = None) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    if sha256_file(path) != expected_hash:
        return False
    return expected_size is None or path.stat().st_size == expected_size


def require_no_symlink_below(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise DockerBuildError(f"path escaped its cache root: {path}") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DockerBuildError(f"cache path cannot contain symlinks: {current}")


def download_asset(item: dict, path: Path) -> None:
    expected_hash = item["sha256"]
    expected_size = item.get("size")
    if verify_file(path, expected_hash, expected_size):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    failures = []
    try:
        for url in input_sources(expected_hash, item["url"]):
            digest = hashlib.sha256()
            size = 0
            try:
                with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                    while chunk := response.read(CHUNK_SIZE):
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                failures.append(f"{url}: {error}")
                continue
            if digest.hexdigest() != expected_hash or (expected_size is not None and size != expected_size):
                raise DockerBuildError(f"downloaded bytes do not match the lock: {url}")
            os.replace(temporary, path)
            return
        raise DockerBuildError("no source served the locked bytes: " + "; ".join(failures))
    finally:
        temporary.unlink(missing_ok=True)


def acquire_experimental_inputs(lock: dict) -> Path:
    common = common_root()
    root = common / ".cache" / "bootstrap" / f"rtls-linux-{DOCKER_ARCH}"
    require_no_symlink_below(common, root)
    require_no_symlink_below(common, root / "apk")
    require_no_symlink_below(common, root / "rust")
    root.parent.mkdir(parents=True, exist_ok=True)
    for item in lock["apk_closure"]:
        if item["architecture"] != MACHINE:
            raise DockerBuildError(f"experimental APK is not {MACHINE}: {item['name']}")
        download_asset(item, root / "apk" / f"{item['sha256']}.apk")
    for item in lock["rust"]["components"]:
        download_asset(item, root / "rust" / item["file"])
    return root


def verify_experimental_inputs(root: Path, lock: dict) -> None:
    require_no_symlink_below(common_root(), root)
    require_no_symlink_below(common_root(), root / "apk")
    require_no_symlink_below(common_root(), root / "rust")
    expected_apks = {item["sha256"] + ".apk" for item in lock["apk_closure"]}
    actual_apks = {path.name for path in (root / "apk").glob("*")}
    if expected_apks - actual_apks:
        raise DockerBuildError(f"experimental APK cache is missing locked files: {expected_apks - actual_apks}")
    expected_rust = {item["file"] for item in lock["rust"]["components"]}
    actual_rust = {path.name for path in (root / "rust").glob("*")}
    if expected_rust - actual_rust:
        raise DockerBuildError(f"Rust cache is missing locked files: {expected_rust - actual_rust}")
    for item in lock["apk_closure"]:
        if not verify_file(root / "apk" / f"{item['sha256']}.apk", item["sha256"], item["size"]):
            raise DockerBuildError(f"experimental APK input failed verification: {item['name']}")
    for item in lock["rust"]["components"]:
        if not verify_file(root / "rust" / item["file"], item["sha256"]):
            raise DockerBuildError(f"Rust component failed verification: {item['name']}")


def base_package_paths(common: Path, bootstrap: dict) -> list[tuple[dict, Path]]:
    entries = bootstrap["linux_toolchain"]["apk_closure"][MACHINE]
    cache = common / ".cache" / "bootstrap" / "apk" / DOCKER_ARCH
    require_no_symlink_below(common, cache)
    result = []
    for entry in entries:
        path = cache / f"{entry['sha256']}.apk"
        if not verify_file(path, entry["sha256"], entry["size"]):
            raise DockerBuildError(
                f"locked base APK is missing or invalid at {path}; run build.py fetch --target {TARGET}"
            )
        result.append((entry, path))
    return result


def toolchain_input_paths(common: Path, bootstrap: dict) -> list[tuple[str, Path]]:
    section = bootstrap["linux_toolchain"]
    entries = ((section["llvm"]["archives"][MACHINE], ".tar.xz"),)
    cache = common / ".cache" / "bootstrap" / "toolchain" / DOCKER_ARCH
    require_no_symlink_below(common, cache)
    result = []
    for entry, suffix in entries:
        name = f"{entry['sha256']}{suffix}"
        path = cache / name
        if not verify_file(path, entry["sha256"], entry["size"]):
            raise DockerBuildError(
                f"locked toolchain input is missing or invalid at {path}; run build.py fetch --target {TARGET}"
            )
        result.append((name, path))
    return result


def image_info(image: str) -> dict:
    output = checked_run(["docker", "image", "inspect", image, "--format", "{{json .}}"])
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        raise DockerBuildError(f"cannot parse Docker image inspection for {image}") from error
    if value.get("Os") != "linux" or value.get("Architecture") not in {DOCKER_ARCH, MACHINE}:
        raise DockerBuildError(f"image is not native Linux {MACHINE}: {image}")
    return value


def prepare_toolchain_image(
    run_dir: Path, bootstrap: dict, experimental: dict, locked_digest: str
) -> tuple[str, dict, dict]:
    common = common_root()
    package_paths = base_package_paths(common, bootstrap)
    toolchain_paths = toolchain_input_paths(common, bootstrap)
    base_material = {
        "dockerfile": sha256_file(REPO / "Dockerfile"),
        "bootstrap_lock": sha256_file(REPO / "bootstrap.lock.json"),
        "targets": sha256_file(REPO / "buildsys" / "targets.py"),
        "stack_patch": sha256_file(REPO / "build" / "patch_llvm_stack.py"),
        "apk_closure": sorted(item[0]["sha256"] for item in package_paths),
        "toolchain_inputs": sorted(name for name, _ in toolchain_paths),
    }
    base_hash = canonical_digest(base_material)
    base_tag = f"python-build-rtls-{DOCKER_ARCH}-base:{base_hash[:16]}"
    try:
        base_image = image_info(base_tag)
    except DockerBuildError:
        with tempfile.TemporaryDirectory(prefix="base-context-", dir=run_dir) as temporary:
            context = Path(temporary)
            shutil.copy2(REPO / "Dockerfile", context / "Dockerfile")
            shutil.copy2(REPO / "bootstrap.lock.json", context / "bootstrap.lock.json")
            (context / "buildsys").mkdir()
            shutil.copy2(REPO / "buildsys" / "targets.py", context / "buildsys" / "targets.py")
            (context / "build").mkdir()
            shutil.copy2(REPO / "build" / "patch_llvm_stack.py", context / "build" / "patch_llvm_stack.py")
            apk_dir = context / ".cache" / "bootstrap" / "apk" / APK_ARCH
            apk_dir.mkdir(parents=True)
            for entry, path in package_paths:
                shutil.copy2(path, apk_dir / f"{entry['sha256']}.apk")
            toolchain_dir = context / ".cache" / "bootstrap" / "toolchain" / APK_ARCH
            toolchain_dir.mkdir(parents=True)
            for name, path in toolchain_paths:
                shutil.copy2(path, toolchain_dir / name)
            (context / ".dockerignore").write_text("")
            subprocess.run(
                ["docker", "pull", "--platform", DOCKER_PLATFORM, f"alpine@{locked_digest}"],
                check=True,
            )
            run_logged(
                [
                    "docker", "build", "--platform", DOCKER_PLATFORM, "--network", "none",
                    "--target", "toolchain", "--tag", base_tag, str(context),
                ],
                run_dir / "logs" / "toolchain-base-image.log",
            )
            base_image = image_info(base_tag)
    experimental_root = common_root() / ".cache" / "bootstrap" / f"rtls-linux-{DOCKER_ARCH}"
    verify_experimental_inputs(experimental_root, experimental)
    input_items = [
        item["sha256"] for item in experimental["apk_closure"]
    ] + [item["sha256"] for item in experimental["rust"]["components"]]
    input_identity = canonical_digest(
        {"lock": sha256_file(LOCK_PATH), "inputs": sorted(input_items)}
    )
    overlay_material = {
        "dockerfile": sha256_file(DOCKERFILE),
        "bootstrap_lock": sha256_file(REPO / "bootstrap.lock.json"),
        "experimental_lock": sha256_file(LOCK_PATH),
        "base_image_id": base_image["Id"],
        "input_identity": input_identity,
    }
    overlay_hash = canonical_digest(overlay_material)
    overlay_tag = f"python-build-rtls-linux-{DOCKER_ARCH}:bootstrap-{overlay_hash[:20]}"
    try:
        overlay_image = image_info(overlay_tag)
    except DockerBuildError:
        with tempfile.TemporaryDirectory(prefix="overlay-context-", dir=run_dir) as temporary:
            context = Path(temporary)
            shutil.copy2(DOCKERFILE, context / "Dockerfile.linux")
            shutil.copy2(REPO / "bootstrap.lock.json", context / "bootstrap.lock.json")
            lock_destination = context / "backend" / "linux-bootstrap.lock.json"
            lock_destination.parent.mkdir(parents=True)
            shutil.copy2(LOCK_PATH, lock_destination)
            cached_inputs = context / ".cache" / "bootstrap" / "rtls-linux"
            (cached_inputs / "apk").mkdir(parents=True)
            (cached_inputs / "rust").mkdir()
            for item in experimental["apk_closure"]:
                shutil.copy2(
                    experimental_root / "apk" / f"{item['sha256']}.apk",
                    cached_inputs / "apk" / f"{item['sha256']}.apk",
                )
            for item in experimental["rust"]["components"]:
                shutil.copy2(
                    experimental_root / "rust" / item["file"],
                    cached_inputs / "rust" / item["file"],
                )
            (context / ".dockerignore").write_text("")
            run_logged(
                [
                    "docker", "build", "--platform", DOCKER_PLATFORM, "--network", "none",
                    "--target", "experimental-toolchain",
                    "--build-arg", f"TOOLCHAIN_IMAGE={base_tag}",
                    "--build-arg", f"RUNTIME_BASE_IMAGE=alpine@{locked_digest}",
                    "--build-arg", f"LOCKED_IMAGE_DIGEST={locked_digest}",
                    "--build-arg", f"BOOTSTRAP_INPUTS_SHA256={input_identity}",
                    "--build-arg", f"EXPECTED_TRIPLE={TARGET}",
                    "--build-arg", f"EXPECTED_PLATFORM={DOCKER_PLATFORM}",
                    "--build-arg", f"EXPECTED_MACHINE={MACHINE}",
                    "--tag", overlay_tag, "-f", str(context / "Dockerfile.linux"), str(context),
                ],
                run_dir / "logs" / "experimental-toolchain-image.log",
            )
            overlay_image = image_info(overlay_tag)
    image_labels = overlay_image.get("Config", {}).get("Labels", {})
    if (
        image_labels.get("org.python-build.rtls.locked-image-digest") != locked_digest
        or image_labels.get("org.python-build.rtls.bootstrap-inputs-sha256") != input_identity
    ):
        raise DockerBuildError("experimental toolchain image identity differs from its locked inputs")
    return overlay_tag, base_image, overlay_image


def host_to_container(path: Path, workspace: Path) -> str:
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(workspace):
        return "/work/" + resolved.relative_to(workspace).as_posix()
    common = common_root()
    if resolved.is_relative_to(common):
        return (common / resolved.relative_to(common)).as_posix()
    raise DockerBuildError(f"container input must be under the checkout or Git common root: {resolved}")


def docker_mounts(workspace: Path) -> list[str]:
    if "," in str(workspace):
        raise DockerBuildError("checkout path cannot contain commas for Docker bind mounts")
    mounts = ["--mount", f"type=bind,source={workspace},target=/work"]
    common = common_root()
    if common != workspace:
        if "," in str(common):
            raise DockerBuildError("Git common root path cannot contain commas for Docker bind mounts")
        mounts.extend(("--mount", f"type=bind,source={common},target={common},readonly"))
    return mounts


def create_container(
    image: str,
    command: list[str],
    *,
    name: str,
    run_dir: Path,
    workspace: Path | None,
    environment: dict[str, str] | None = None,
    workdir: str = "/work",
    qualification_volume: str | None = None,
    user: str | None = None,
    account_files: tuple[Path, Path] | None = None,
    capabilities: tuple[str, ...] = (),
) -> tuple[str, list[str]]:
    argv = ["docker", "create", "--name", name, "--platform", DOCKER_PLATFORM, "--network", "none"]
    execution_user = user or f"{os.getuid()}:{os.getgid()}"
    if re.fullmatch(r"[0-9]+:[0-9]+", execution_user) is None:
        raise DockerBuildError(f"invalid container uid:gid: {execution_user}")
    argv.extend(("--user", execution_user))
    argv.extend(("--cap-drop", "ALL"))
    if any(re.fullmatch(r"[A-Z][A-Z0-9_]*", capability) is None for capability in capabilities):
        raise DockerBuildError(f"invalid container capability set: {capabilities!r}")
    for capability in capabilities:
        argv.extend(("--cap-add", capability))
    if workspace is not None:
        argv.extend(docker_mounts(workspace))
    if qualification_volume is not None:
        if re.fullmatch(r"rtls-q-[0-9a-f]{12}", qualification_volume) is None:
            raise DockerBuildError(f"invalid qualification volume name: {qualification_volume}")
        argv.extend(
            (
                "--mount",
                f"type=volume,source={qualification_volume},target={QUALIFICATION_VOLUME_TARGET},volume-nocopy",
            )
        )
    if account_files is not None:
        if len(account_files) != 2:
            raise DockerBuildError("qualification requires both passwd and group files")
        for source, target in zip(account_files, ("/etc/passwd", "/etc/group")):
            if source.is_symlink() or not source.is_file():
                raise DockerBuildError(f"qualification account file is not a regular file: {source}")
            if "," in str(source):
                raise DockerBuildError(f"qualification account path cannot contain commas: {source}")
            argv.extend(("--mount", f"type=bind,source={source.resolve(strict=True)},target={target},readonly"))
    argv.extend(("--workdir", workdir))
    for key, value in (environment or {}).items():
        argv.extend(("--env", f"{key}={value}"))
    argv.append(image)
    argv.extend(command)
    container_id = checked_run(argv, cwd=REPO)
    return container_id, argv


def inspect_container(container_id: str) -> dict:
    output = checked_run(["docker", "container", "inspect", container_id, "--format", "{{json .}}"])
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise DockerBuildError(f"cannot parse Docker container inspection for {container_id}") from error


def start_container(container_id: str, log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output: list[str] = []
    with log_path.open("w") as log:
        process = subprocess.Popen(
            ["docker", "start", "--attach", container_id],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            if process.stdout is None:
                raise DockerBuildError("failed to capture container output")
            for line in process.stdout:
                output.append(line)
                log.write(line)
                log.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            process_status = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    inspection = inspect_container(container_id)
    status = inspection.get("State", {}).get("ExitCode")
    if process_status and status in (0, None):
        status = process_status
    return int(status if status is not None else process_status), "".join(output)


def run_container(
    image: str,
    command: list[str],
    *,
    name: str,
    run_dir: Path,
    log_path: Path,
    workspace: Path | None,
    environment: dict[str, str] | None = None,
    workdir: str = "/work",
    qualification_volume: str | None = None,
    user: str | None = None,
    account_files: tuple[Path, Path] | None = None,
    capabilities: tuple[str, ...] = (),
) -> tuple[dict, dict, list[str], str]:
    image_record = image_info(image)
    container_id, argv = create_container(
        image,
        command,
        name=name,
        run_dir=run_dir,
        workspace=workspace,
        environment=environment,
        workdir=workdir,
        qualification_volume=qualification_volume,
        user=user,
        account_files=account_files,
        capabilities=capabilities,
    )
    try:
        before = inspect_container(container_id)
        network_mode = before.get("HostConfig", {}).get("NetworkMode")
        platform = before.get("Platform")
        if network_mode != "none" or platform not in {None, "linux", DOCKER_PLATFORM}:
            raise DockerBuildError("created container failed the native network-none inspection")
        status, output = start_container(container_id, log_path)
        after = inspect_container(container_id)
        if after.get("HostConfig", {}).get("NetworkMode") != "none":
            raise DockerBuildError("container network mode changed during execution")
        lines = output.splitlines()
        if not lines or lines[0] != MACHINE:
            raise DockerBuildError(f"container did not prove native {MACHINE} execution")
        record = {
            "container_id": after["Id"],
            "network_mode": network_mode,
            "exit_code": status,
            "platform": after.get("Platform", DOCKER_PLATFORM),
            "container_architecture": lines[0],
            "container_user": before.get("Config", {}).get("User"),
            "cap_drop": before.get("HostConfig", {}).get("CapDrop") or [],
            "cap_add": before.get("HostConfig", {}).get("CapAdd") or [],
            "effective_capabilities": next(
                (line.partition("=")[2] for line in lines if line.startswith("container_cap_eff=")),
                None,
            ),
            "mounts": [
                {
                    "type": mount.get("Type"),
                    "destination": mount.get("Destination"),
                    "read_only": not mount.get("RW", True),
                }
                for mount in after.get("Mounts", [])
            ],
            "log_path": str(log_path.relative_to(REPO)),
        }
        if status:
            raise DockerBuildError(f"container exited {status}; see {log_path}")
    except BaseException:
        subprocess.run(["docker", "stop", "--time", "5", container_id], capture_output=True, check=False)
        subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, check=False)
        raise
    cleanup = subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, text=True, check=False)
    if cleanup.returncode:
        raise DockerBuildError(f"could not remove completed container {container_id}: {cleanup.stderr.strip()}")
    return record, image_record, argv, output


def run_build(
    run_dir: Path,
    image: str,
    revision: str,
    dirty: bool,
    jobs: int,
    hashing_source: Path,
    daemon: dict,
    locked_digest: str,
) -> dict:
    relative_run = run_dir.relative_to(REPO).as_posix()
    container_run = f"/work/{relative_run}"
    hashing_path = host_to_container(hashing_source, REPO)
    home = run_dir / "home"
    cargo_home = run_dir / "cargo-home"
    home.mkdir(exist_ok=True)
    cargo_home.mkdir(exist_ok=True)
    environment = {
        "HOME": f"{container_run}/home",
        "XDG_CACHE_HOME": f"{container_run}/home/.cache",
        "TMPDIR": "/work/.work/tmp",
        "CARGO_HOME": f"{container_run}/cargo-home",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_BUILD_BUILD_MODE": "sealed",
        "PYTHON_BUILD_ISOLATION": "docker-network-none",
        "PYTHON_BUILD_SOURCE_REVISION": revision,
        "PYTHON_BUILD_SOURCE_DIRTY": "1" if dirty else "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    driver_command = [
        "python3", "backend/build-no-openssl.py",
        "--phase", "all", "--run-dir", container_run, "--jobs", str(jobs),
        "--hashing-source", hashing_path,
    ]
    command = [
        "sh", "-ec",
        (f'test "$(uname -m)" = {MACHINE}; printf "{MACHINE}\\n"; '
        'cap_eff=$(awk \'/^CapEff:/ {print $2}\' /proc/self/status); '
        'test "$cap_eff" = 0000000000000000; printf "container_cap_eff=%s\\n" "$cap_eff"; exec "$@"'),
        f"native-{MACHINE}-guard", *driver_command,
    ]
    name = "rtls-linux-build-" + uuid.uuid4().hex[:12]
    record, image_record, argv, output = run_container(
        image,
        command,
        name=name,
        run_dir=run_dir,
        log_path=run_dir / "logs" / "candidate-build.log",
        workspace=REPO,
        environment=environment,
    )
    build_receipt = run_dir / "linux-build.json"
    if not build_receipt.is_file():
        raise DockerBuildError("native build completed without linux-build.json")
    receipt = json.loads(build_receipt.read_text())
    if receipt.get("target") != TARGET or receipt.get("source_revision") != revision:
        raise DockerBuildError("native build receipt does not match the requested target and revision")
    docker_receipt = {
        "schema": 1,
        "target_triple": TARGET,
        "platform": DOCKER_PLATFORM,
        "source_revision": revision,
        "source_dirty": dirty,
        "locked_image_digest": locked_digest,
        "image_id": image_record["Id"],
        "image_architecture": image_record["Architecture"],
        "container_user": record["container_user"],
        "cap_drop": record["cap_drop"],
        "cap_add": record["cap_add"],
        "effective_capabilities": record["effective_capabilities"],
        "daemon_os": daemon["daemon_os"],
        "daemon_architecture": daemon["daemon_architecture"],
        "daemon_platform": daemon["daemon_platform"],
        "container_architecture": record["container_architecture"],
        "container_id": record["container_id"],
        "network_mode": record["network_mode"],
        "isolation": "docker-network-none",
        "command": argv,
        "linux_build_sha256": sha256_file(build_receipt),
        "toolchain_bootstrap_sha256": image_record.get("Config", {}).get("Labels", {}).get(
            "org.python-build.rtls.bootstrap-inputs-sha256"
        ),
        "fixture_generators": {
            "openssl_cli": next((line for line in output.splitlines() if line.startswith("OpenSSL ")), None)
        },
        "exit_code": record["exit_code"],
    }
    if docker_receipt["fixture_generators"]["openssl_cli"] is None:
        probe = toolchain_probe(image)
        docker_receipt["fixture_generators"]["openssl_cli"] = probe["openssl_cli"]
    write_json(run_dir / "docker-build.json", docker_receipt)
    return docker_receipt


def toolchain_probe(image: str) -> dict:
    output = checked_run(
        [
            "docker", "run", "--rm", "--platform", DOCKER_PLATFORM, "--network", "none",
            "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "sh", image, "-ec",
            f'test "$(uname -m)" = {MACHINE}; openssl version; git --version; rustc --version; cargo --version',
        ]
    )
    lines = output.splitlines()
    openssl = next((line for line in lines if line.startswith("OpenSSL ")), None)
    if openssl is None:
        raise DockerBuildError("native toolchain probe did not report the fixture OpenSSL version")
    return {"openssl_cli": openssl, "probe": lines}


def networked_fetch(
    run_dir: Path,
    image: str,
    jobs: int,
    hashing_source: Path,
) -> None:
    relative_run = run_dir.relative_to(REPO).as_posix()
    container_run = f"/work/{relative_run}"
    environment = {
        "HOME": f"{container_run}/home",
        "XDG_CACHE_HOME": f"{container_run}/home/.cache",
        "TMPDIR": "/work/.work/tmp",
        "CARGO_HOME": f"{container_run}/cargo-home",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    (run_dir / "home").mkdir(exist_ok=True)
    (run_dir / "cargo-home").mkdir(exist_ok=True)
    command = [
        "python3", "backend/build-no-openssl.py",
        "--phase", "fetch", "--run-dir", container_run, "--jobs", str(jobs),
        "--hashing-source", host_to_container(hashing_source, REPO),
    ]
    argv = [
        "docker", "run", "--rm", "--platform", DOCKER_PLATFORM, "--network", "bridge",
        "--user", f"{os.getuid()}:{os.getgid()}", *docker_mounts(REPO), "--workdir", "/work",
    ]
    for key, value in environment.items():
        argv.extend(("--env", f"{key}={value}"))
    argv.extend((image, *command))
    run_logged(argv, run_dir / "logs" / "rust-input-fetch.log", cwd=REPO)


def build_runtime_image(
    run_dir: Path, locked_digest: str, candidate_archive: Path
) -> tuple[str, dict, str]:
    if candidate_archive.is_symlink() or not candidate_archive.is_file():
        raise DockerBuildError("qualified candidate archive must be a regular file")
    archive_hash = sha256_file(candidate_archive)
    image_hash = canonical_digest(
        {
            "dockerfile": sha256_file(DOCKERFILE),
            "locked_digest": locked_digest,
            "archive_sha256": archive_hash,
        }
    )
    tag = f"python-build-rtls-linux-{DOCKER_ARCH}-runtime:{image_hash[:20]}"
    try:
        image = image_info(tag)
    except DockerBuildError:
        with tempfile.TemporaryDirectory(prefix="runtime-context-", dir=run_dir) as temporary:
            context = Path(temporary)
            shutil.copy2(DOCKERFILE, context / "Dockerfile.linux")
            shutil.copy2(REPO / "bootstrap.lock.json", context / "bootstrap.lock.json")
            lock_destination = context / "backend" / "linux-bootstrap.lock.json"
            lock_destination.parent.mkdir(parents=True)
            shutil.copy2(LOCK_PATH, lock_destination)
            archive = context / "qualification" / "candidate.tar.gz"
            archive.parent.mkdir()
            shutil.copy2(candidate_archive, archive)
            (context / ".dockerignore").write_text("")
            run_logged(
                [
                    "docker", "build", "--platform", DOCKER_PLATFORM, "--network", "none",
                    "--target", "archive-runtime",
                    "--build-arg", f"TOOLCHAIN_IMAGE=alpine@{locked_digest}",
                    "--build-arg", f"RUNTIME_BASE_IMAGE=alpine@{locked_digest}",
                    "--build-arg", f"LOCKED_IMAGE_DIGEST={locked_digest}",
                    "--build-arg", f"ARCHIVE_SHA256={archive_hash}",
                    "--tag", tag, "-f", str(context / "Dockerfile.linux"), str(context),
                ],
                run_dir / "logs" / "archive-runtime-image.log",
            )
            image = image_info(tag)
    labels = image.get("Config", {}).get("Labels", {})
    if (
        labels.get("org.python-build.rtls.locked-image-digest") != locked_digest
        or labels.get("org.python-build.rtls.archive-sha256") != archive_hash
    ):
        raise DockerBuildError("runtime image labels do not bind the locked base and candidate archive")
    return tag, image, archive_hash


def runtime_command() -> list[str]:
    code = r"""
import hashlib
import hmac
import importlib
import json
import ssl
import sys

names = ('ssl', '_ssl', 'hashlib', '_hashlib', 'rtls._rustls', 'sqlite3',
         'compression.zstd', 'multiprocessing', 'ctypes')
modules = {name: importlib.import_module(name) for name in names}
assert sys.prefix == '/opt/python', sys.prefix
assert ssl.OPENSSL_VERSION.startswith('Rustls '), ssl.OPENSSL_VERSION
assert hashlib.sha256(b'abc').hexdigest() == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
assert hmac.digest(b'key', b'The quick brown fox jumps over the lazy dog', 'sha256').hex() == 'f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8'
assert hashlib.pbkdf2_hmac('sha256', b'password', b'salt', 1).hex() == '120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b'
assert hashlib.scrypt(b'', salt=b'', n=16, r=1, p=1, dklen=64).hex() == '77d6576238657b203b19ca42c18a0497f16b4844e3074ae8dfdffa3fede21442fcd0069ded0948f8326a753a0fc81f17e8d3e0fb2e0d3628cf35e20c38d18906'
incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
connection = ssl.create_default_context().wrap_bio(incoming, outgoing, server_hostname='localhost')
try:
    connection.do_handshake()
except ssl.SSLWantReadError:
    pass
else:
    raise AssertionError('TLS client completed without a server')
assert outgoing.pending > 0
print(json.dumps({
    'ok': True,
    'sys_prefix': sys.prefix,
    'openssl_version': ssl.OPENSSL_VERSION,
    'sha256_abc': hashlib.sha256(b'abc').hexdigest(),
    'crypto_operations': ['sha256', 'hmac-sha256', 'pbkdf2-hmac-sha256', 'scrypt', 'tls-client-hello'],
    'module_origins': {name: getattr(module, '__file__', None) for name, module in modules.items()},
}, sort_keys=True))
"""
    return [
        "sh", "-ec",
        (f'test "$(uname -m)" = {MACHINE}; printf "{MACHINE}\\n"; '
        'cap_eff=$(awk \'/^CapEff:/ {print $2}\' /proc/self/status); '
        'test "$cap_eff" = 0000000000000000; printf "container_cap_eff=%s\\n" "$cap_eff"; exec "$@"'),
        f"native-{MACHINE}-runtime-guard", "/opt/python/bin/python3.15", "-I", "-S", "-B", "-c", code,
    ]


def run_runtime_probe(
    run_dir: Path,
    image: str,
    image_record: dict,
    archive_hash: str,
    revision: str,
    dirty: bool,
    daemon: dict,
    locked_digest: str,
    archive_path: Path | None = None,
) -> dict:
    name = "rtls-linux-runtime-" + uuid.uuid4().hex[:12]
    record, inspected_image, argv, output = run_container(
        image,
        runtime_command(),
        name=name,
        run_dir=run_dir,
        log_path=run_dir / "logs" / "archive-runtime-probe.log",
        workspace=None,
        workdir="/",
    )
    if inspected_image["Id"] != image_record["Id"]:
        raise DockerBuildError("runtime container used a different image than the inspected runtime image")
    lines = output.splitlines()
    probe_lines = [line for line in lines if line.startswith("{")]
    if len(probe_lines) != 1:
        raise DockerBuildError("runtime probe did not emit exactly one JSON result")
    probe = json.loads(probe_lines[0])
    if probe.get("ok") is not True or probe.get("sys_prefix") != "/opt/python":
        raise DockerBuildError("runtime probe result did not prove the extracted interpreter identity")
    receipt = {
        "schema": 1,
        "target_triple": TARGET,
        "platform": DOCKER_PLATFORM,
        "source_revision": revision,
        "source_dirty": dirty,
        "locked_image_digest": locked_digest,
        "image_id": inspected_image["Id"],
        "image_architecture": inspected_image["Architecture"],
        "container_user": record["container_user"],
        "cap_drop": record["cap_drop"],
        "cap_add": record["cap_add"],
        "effective_capabilities": record["effective_capabilities"],
        "daemon_os": daemon["daemon_os"],
        "daemon_architecture": daemon["daemon_architecture"],
        "daemon_platform": daemon["daemon_platform"],
        "container_architecture": record["container_architecture"],
        "container_id": record["container_id"],
        "network_mode": record["network_mode"],
        "isolation": "docker-network-none",
        "command": argv,
        "archive_path": f"qualification/archive/{archive_path.name}",
        "archive_sha256": archive_hash,
        "runtime_probe": probe,
        "exit_code": record["exit_code"],
    }
    write_json(run_dir / "docker-runtime.json", receipt)
    return receipt


@contextmanager
def qualification_volume():
    identity = uuid.uuid4().hex[:12]
    name = f"rtls-q-{identity}"
    label = "org.python-build.rtls.qualification-volume"
    existing = subprocess.run(
        ["docker", "volume", "inspect", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode == 0:
        raise DockerBuildError(f"refusing to reuse an existing qualification volume: {name}")
    if "no such volume" not in (existing.stdout + existing.stderr).lower():
        raise DockerBuildError(
            f"could not establish that qualification volume {name} is unused: "
            f"{existing.stdout}{existing.stderr}"
        )
    if checked_run(["docker", "volume", "create", "--label", f"{label}={identity}", name]) != name:
        raise DockerBuildError(f"Docker did not create the requested qualification volume: {name}")
    labels = json.loads(
        checked_run(["docker", "volume", "inspect", name, "--format", "{{json .Labels}}"])
    )
    if not isinstance(labels, dict) or labels.get(label) != identity:
        raise DockerBuildError(f"qualification volume identity did not match its creation label: {name}")
    volume = {"name": name, "identity": identity}
    try:
        yield volume
    except BaseException as error:
        cleanup = subprocess.run(
            ["docker", "volume", "rm", name], capture_output=True, text=True,
            check=False,
        )
        if cleanup.returncode:
            raise DockerBuildError(
                f"{error}; failed to remove qualification volume {name}: {cleanup.stderr.strip()}"
            ) from error
        raise
    else:
        checked_run(["docker", "volume", "rm", name])



def _capability_names(capabilities: list[str]) -> list[str]:
    return [name.removeprefix("CAP_") for name in capabilities]


def initialize_qualification_volume(
    run_dir: Path,
    image: str,
    volume: str,
    owner_uid: int,
    owner_gid: int,
    locked_digest: str,
) -> tuple[dict, str, str]:
    command = [
        "sh",
        "-ec",
        (f'test "$(uname -m)" = {MACHINE}; printf "{MACHINE}\\n"; '
        'cap_eff=$(awk \'/^CapEff:/ {print $2}\' /proc/self/status); '
        'test "$cap_eff" = 0000000000000001; printf "container_cap_eff=%s\\n" "$cap_eff"; '
        f"chmod 0700 {QUALIFICATION_VOLUME_TARGET}; chown {owner_uid}:{owner_gid} {QUALIFICATION_VOLUME_TARGET}; "
        "printf 'PASSWD_BEGIN\\n'; cat /etc/passwd; printf 'PASSWD_END\\n'; "
        "printf 'GROUP_BEGIN\\n'; cat /etc/group; printf 'GROUP_END\\n'"),
    ]
    record, image_record, argv, output = run_container(
        image,
        command,
        name="rtls-linux-qualification-volume-" + uuid.uuid4().hex[:12],
        run_dir=run_dir,
        log_path=run_dir / "logs" / "qualification-volume-init.log",
        workspace=REPO,
        environment={"HOME": "/root", "TMPDIR": "/work/.work/tmp"},
        qualification_volume=volume,
        user="0:0",
        capabilities=("CHOWN",),
    )
    def file_section(name: str) -> str:
        begin = f"{name}_BEGIN\n"
        end = f"\n{name}_END\n"
        if output.count(begin) != 1 or output.count(end) != 1:
            raise DockerBuildError(f"qualification initializer did not return one {name.lower()} file")
        _, contents = output.split(begin, 1)
        contents, _ = contents.split(end, 1)
        if not contents or "\r" in contents:
            raise DockerBuildError(f"qualification initializer returned malformed {name.lower()} data")
        return contents + "\n"

    receipt = {
        "container_id": record["container_id"],
        "network_mode": record["network_mode"],
        "exit_code": record["exit_code"],
        "image_id": image_record["Id"],
        "image_architecture": image_record["Architecture"],
        "platform": record["platform"],
        "locked_image_digest": locked_digest,
        "container_architecture": record["container_architecture"],
        "container_user": record["container_user"],
        "cap_drop": record["cap_drop"],
        "cap_add": record["cap_add"],
        "effective_capabilities": record["effective_capabilities"],
        "command": argv,
        "owner_uid": owner_uid,
        "owner_gid": owner_gid,
    }
    if (
        receipt["container_user"] != "0:0"
        or _capability_names(receipt["cap_drop"]) != ["ALL"]
        or _capability_names(receipt["cap_add"]) != ["CHOWN"]
        or receipt["effective_capabilities"] != "0000000000000001"
    ):
        raise DockerBuildError(
            f"qualification volume initializer did not use the restricted chown-only identity: {receipt!r}"
        )
    return receipt, file_section("PASSWD"), file_section("GROUP")


def write_qualification_account(
    run_dir: Path,
    identity: str,
    owner_uid: int,
    owner_gid: int,
    home: str,
    passwd_source: str,
    group_source: str,
) -> tuple[tuple[Path, Path], dict]:
    if owner_uid <= 0 or owner_gid < 0:
        raise DockerBuildError("qualification requires a non-root host UID and a nonnegative GID")
    username = f"rtls-q-{identity}"
    if re.fullmatch(r"rtls-q-[0-9a-f]{12}", username) is None:
        raise DockerBuildError("qualification account identity is malformed")
    passwd_lines = passwd_source.splitlines()
    group_lines = group_source.splitlines()
    for line in passwd_lines:
        fields = line.split(":")
        if len(fields) != 7 or not fields[2].isdigit() or not fields[3].isdigit():
            raise DockerBuildError("toolchain image contains a malformed passwd entry")
    for line in group_lines:
        fields = line.split(":")
        if len(fields) != 4 or not fields[2].isdigit():
            raise DockerBuildError("toolchain image contains a malformed group entry")
    passwd_lines = [
        line for line in passwd_lines
        if line.split(":")[0] != username and int(line.split(":")[2]) != owner_uid
    ]
    passwd_lines.append(f"{username}:x:{owner_uid}:{owner_gid}:RTLS qualification:{home}:/bin/sh")
    group_lines = [line for line in group_lines if line.split(":")[0] != username]
    if not any(int(line.split(":")[2]) == owner_gid for line in group_lines):
        group_lines.append(f"{username}:x:{owner_gid}:")
    account_dir = run_dir / "container-nss"
    if account_dir.is_symlink() or account_dir.exists():
        raise DockerBuildError(f"qualification account directory must be fresh: {account_dir}")
    account_dir.mkdir(mode=0o755)
    passwd_path = account_dir / "passwd"
    group_path = account_dir / "group"
    passwd_path.write_text("\n".join(passwd_lines) + "\n")
    group_path.write_text("\n".join(group_lines) + "\n")
    os.chmod(passwd_path, 0o644)
    os.chmod(group_path, 0o644)
    return (passwd_path, group_path), {
        "username": username,
        "uid": owner_uid,
        "gid": owner_gid,
        "home": home,
        "passwd_sha256": sha256_file(passwd_path),
        "group_sha256": sha256_file(group_path),
    }


def qualify(
    run_dir: Path,
    image: str,
    jobs: int,
    daemon: dict,
    revision: str,
    dirty: bool,
    locked_digest: str,
    archive_name: str | None = None,
    manifest_name: str | None = None,
) -> tuple[Path, dict]:
    candidate_run = run_dir
    reference_run = run_dir / "reference-build"
    qualification_parent = run_dir / "qualification"
    regression_run = run_dir / "qualification" / "regression"
    archive_run = run_dir / "qualification" / "archive"
    for directory in (reference_run, qualification_parent, regression_run):
        if directory.is_symlink():
            raise DockerBuildError(f"qualification output cannot be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        if any(directory.iterdir()):
            raise DockerBuildError(f"qualification output directory must be empty: {directory}")
    if archive_run.is_symlink() or archive_run.exists():
        raise DockerBuildError(f"archive qualifier requires a fresh, absent output directory: {archive_run}")
    if {entry.name for entry in qualification_parent.iterdir()} != {"regression"}:
        raise DockerBuildError(f"unexpected qualification output entries under {qualification_parent}")
    relative_run = candidate_run.relative_to(REPO).as_posix()
    base = f"/work/{relative_run}"
    volume_work_id = uuid.uuid4().hex[:8]
    volume_work_dir = f"{QUALIFICATION_VOLUME_TARGET}/{volume_work_id}"
    commands = [
        [
            "python3", "backend/build-linux-reference.py",
            "--candidate-run-dir", base,
            "--run-dir", f"{base}/reference-build",
            "--jobs", str(jobs),
        ],
        [
            "python3", "backend/qualify-no-openssl-regressions.py",
            "--prefix", f"{base}/relocated prefix",
            "--run-dir", f"{base}/qualification/regression",
            "--work-dir", volume_work_dir,
            "--jobs", str(jobs),
        ],
        [
            "python3", "backend/qualify-no-openssl-archive.py",
            "--prefix", f"{base}/relocated prefix",
            "--build-run-dir", base,
            "--reference-python", f"{base}/reference-build/prefix/bin/python3.15",
            "--certdata", f"{base}/cpython-source/Python-3.15.1/Lib/test/certdata",
            "--regression-report", f"{base}/qualification/regression/report.json",
            "--run-dir", f"{base}/qualification/archive",
            *(["--archive-name", archive_name] if archive_name else []),
            *(["--manifest-name", manifest_name] if manifest_name else []),
        ],
    ]
    environment = {
        "HOME": f"{base}/home",
        "XDG_CACHE_HOME": f"{base}/home/.cache",
        "TMPDIR": QUALIFICATION_VOLUME_TARGET,
        "CARGO_HOME": f"{base}/cargo-home",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "PYTHON_BUILD_BUILD_MODE": "sealed",
        "PYTHON_BUILD_ISOLATION": "docker-network-none",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    if os.getuid() <= 0:
        raise DockerBuildError("qualification must run as a non-root host user")
    sequence = [
        "set -eu",
        f'test "$(uname -m)" = {MACHINE}',
        f'printf "{MACHINE}\\n"',
        f'test "$(id -u):$(id -g)" = "{os.getuid()}:{os.getgid()}"',
        'cap_eff=$(awk \'/^CapEff:/ {print $2}\' /proc/self/status)',
        'test "$cap_eff" = 0000000000000000',
        'printf "container_cap_eff=%s\\n" "$cap_eff"',
    ]
    for command in commands:
        sequence.append(" ".join(shlex_quote(item) for item in command))
    shell_command = ["sh", "-ec", "; ".join(sequence)]
    with qualification_volume() as volume:
        owner_uid = os.getuid()
        owner_gid = os.getgid()
        initialization = initialize_qualification_volume(
            run_dir, image, volume["name"], owner_uid, owner_gid, locked_digest
        )
        initialization_record, passwd_source, group_source = initialization
        if (
            initialization_record["platform"] not in {None, "linux", DOCKER_PLATFORM}
            or initialization_record["container_architecture"] != MACHINE
            or initialization_record["network_mode"] != "none"
            or initialization_record["locked_image_digest"] != locked_digest
        ):
            raise DockerBuildError(f"qualification volume initializer did not use the locked native {MACHINE} image")
        account_files, account_record = write_qualification_account(
            run_dir,
            volume["identity"],
            owner_uid,
            owner_gid,
            environment["HOME"],
            passwd_source,
            group_source,
        )
        environment["USER"] = account_record["username"]
        environment["LOGNAME"] = account_record["username"]
        container, image_record, argv, _ = run_container(
            image,
            shell_command,
            name="rtls-linux-qualification-" + uuid.uuid4().hex[:12],
            run_dir=run_dir,
            log_path=run_dir / "logs" / "reference-regression-archive.log",
            workspace=REPO,
            environment=environment,
            qualification_volume=volume["name"],
            account_files=account_files,
        )
        if initialization_record["image_id"] != image_record["Id"]:
            raise DockerBuildError("qualification volume initializer and runner used different images")
        if container["container_user"] != f"{owner_uid}:{owner_gid}":
            raise DockerBuildError("qualification container did not use the host non-root UID and GID")
        if container["cap_drop"] != ["ALL"] or container["cap_add"]:
            raise DockerBuildError("qualification container did not drop all Linux capabilities")
        mount_readonly = {item["destination"]: item["read_only"] for item in container["mounts"]}
        if mount_readonly.get("/etc/passwd") is not True or mount_readonly.get("/etc/group") is not True:
            raise DockerBuildError("qualification account files were not mounted read-only")
        write_json(
            run_dir / "docker-qualification.json",
            {
                "schema": 1,
                "target_triple": TARGET,
                "platform": DOCKER_PLATFORM,
                "source_revision": revision,
                "source_dirty": dirty,
                "locked_image_digest": locked_digest,
                **daemon,
                "image_id": image_record["Id"],
                "image_architecture": image_record["Architecture"],
                "container_architecture": container["container_architecture"],
                "container_id": container["container_id"],
                "container_user": container["container_user"],
                "cap_drop": container["cap_drop"],
                "cap_add": container["cap_add"],
                "effective_capabilities": container["effective_capabilities"],
                "network_mode": container["network_mode"],
                "isolation": "docker-network-none",
                "command": argv,
                "qualification_work_volume": {
                    "name": volume["name"],
                    "target": QUALIFICATION_VOLUME_TARGET,
                    "work_dir": volume_work_dir,
                    "owner_uid": owner_uid,
                    "owner_gid": owner_gid,
                    "account": account_record,
                    "initialization": initialization_record,
                },
                "exit_code": container["exit_code"],
            },
        )
        archive = archive_run / (archive_name or ARCHIVE_NAME)
        manifest = archive_run / (manifest_name or MANIFEST_NAME)
        report = archive_run / REPORT_NAME
        if not archive.is_file() or archive.is_symlink() or not manifest.is_file() or not report.is_file():
            raise DockerBuildError("archive qualifier did not write the required report, manifest, and archive")
        report_value = json.loads(report.read_text())
        if report_value.get("ok") is not True:
            raise DockerBuildError("archive qualifier did not report success")
        return archive, image_record


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def run_fetch(
    run_dir: Path,
    jobs: int,
    revision: str,
    dirty: bool,
    hashing_source: Path,
    bootstrap: dict,
    experimental: dict,
    locked_digest: str,
) -> tuple[str, dict, dict]:
    cache_log = run_dir / "logs" / "source-and-base-fetch.log"
    run_logged(
        [sys.executable, str(REPO / "build.py"), "fetch", "--target", TARGET],
        cache_log,
        cwd=REPO,
        environment={**os.environ, "TMPDIR": str(REPO / ".work" / "tmp")},
    )
    acquire_experimental_inputs(experimental)
    subprocess.run(
        ["docker", "pull", "--platform", DOCKER_PLATFORM, f"alpine@{locked_digest}"],
        check=True,
    )
    image, base_image, overlay_image = prepare_toolchain_image(
        run_dir, bootstrap, experimental, locked_digest
    )
    probe = toolchain_probe(image)
    write_json(
        run_dir / "docker-toolchain.json",
        {
            "schema": 1,
            "target_triple": TARGET,
            "platform": DOCKER_PLATFORM,
            "source_revision": revision,
            "source_dirty": dirty,
            "locked_image_digest": locked_digest,
            "toolchain_base_image_id": base_image["Id"],
            "image_id": overlay_image["Id"],
            "image_architecture": overlay_image["Architecture"],
            "daemon_os": "linux",
            "daemon_architecture": docker_daemon()["daemon_architecture"],
            "daemon_platform": DOCKER_PLATFORM,
            "openssl_cli": probe["openssl_cli"],
            "probe": probe["probe"],
        },
    )
    networked_fetch(run_dir, image, jobs, hashing_source)
    return image, base_image, overlay_image


def run_build_and_qualification(
    phase: str,
    run_dir: Path,
    jobs: int,
    revision: str,
    dirty: bool,
    hashing_source: Path,
    bootstrap: dict,
    experimental: dict,
    locked_digest: str,
    daemon: dict,
    archive_name: str | None = None,
    manifest_name: str | None = None,
) -> None:
    if phase in {"fetch", "all"}:
        image, _, _ = run_fetch(
            run_dir, jobs, revision, dirty, hashing_source,
            bootstrap, experimental, locked_digest,
        )
    else:
        verify_experimental_inputs(
            common_root() / ".cache" / "bootstrap" / f"rtls-linux-{DOCKER_ARCH}", experimental
        )
        image, _, _ = prepare_toolchain_image(run_dir, bootstrap, experimental, locked_digest)
    if phase in {"build", "all"}:
        run_build(
            run_dir, image, revision, dirty, jobs, hashing_source, daemon, locked_digest
        )
    if phase in {"qualify", "all"}:
        if not (run_dir / "docker-build.json").is_file():
            raise DockerBuildError("qualification requires a completed sealed candidate build")
        archive, _ = qualify(
            run_dir, image, jobs, daemon, revision, dirty, locked_digest,
            archive_name, manifest_name,
        )
        runtime_tag, runtime_image, archive_hash = build_runtime_image(
            run_dir, locked_digest, archive
        )
        run_runtime_probe(
            run_dir, runtime_tag, runtime_image, archive_hash, revision, dirty, daemon, locked_digest,
            archive,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Acquire and qualify a native Linux OpenSSL-free candidate in Docker"
    )
    parser.add_argument("--phase", choices=("fetch", "build", "qualify", "all"), default="all")
    parser.add_argument("--target", default="aarch64-unknown-linux-musl", choices=sorted(ARCH_CONFIGS))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--source-revision")
    parser.add_argument("--hashing-source", type=Path, default=REPO / "backend" / "hashing")
    parser.add_argument("--archive-name", default=None)
    parser.add_argument("--manifest-name", default=None)
    args = parser.parse_args(argv)
    try:
        if args.jobs <= 0:
            raise DockerBuildError("--jobs must be positive")
        configure(args.target)
        daemon = docker_daemon()
        run_dir = require_work_dir(args.run_dir, fresh=args.phase == "all")
        revision, dirty = source_identity(args.source_revision)
        hashing_source = args.hashing_source.resolve(strict=True)
        bootstrap, experimental, locked_digest = load_locks()
        (run_dir / "logs").mkdir(exist_ok=True)
        run_build_and_qualification(
            args.phase,
            run_dir,
            args.jobs,
            revision,
            dirty,
            hashing_source,
            bootstrap,
            experimental,
            locked_digest,
            daemon,
            args.archive_name,
            args.manifest_name,
        )
    except (DockerBuildError, OSError, subprocess.SubprocessError, KeyError, ValueError) as error:
        print(f"FAIL native Linux {MACHINE} Docker qualification: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
