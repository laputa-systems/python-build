"""Comparison reports over a completed package artifact."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .generations import GenerationError, active_generation
from .inputs import Cache, InputError, locked_inputs, safe_extract
from .package import PackageError, extract_package_archive, publish_files
from .targets import Target


class ComparisonError(Exception):
    """A completed artifact could not be compared."""


def _reference_pin(lock_path: Path, target: Target):
    entries = locked_inputs(lock_path)
    qualified = f"reference-pbs-{target.triple}"
    for entry in entries:
        if entry.name == qualified:
            return entry
    for entry in entries:
        if entry.name == "reference-pbs" and entry.target == target.triple:
            return entry
    return None


def _run_probe(python: Path, code: str, target: Target, workdir: Path) -> dict:
    command = [str(python), "-I", "-B", "-c", code]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workdir / "home"),
        "TMPDIR": str(workdir / "tmp"),
        "PYTHONNOUSERSITE": "1",
    }
    workdir.mkdir(parents=True, exist_ok=True)
    Path(environment["HOME"]).mkdir(parents=True, exist_ok=True)
    Path(environment["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    if target.is_macos:
        from .sandbox import SealedRun

        sandbox = SealedRun(write_paths=[workdir], home=Path(environment["HOME"]))
        result = sandbox.run(command, cwd=workdir, env=sandbox.environment())
    else:
        result = subprocess.run(
            command,
            cwd=workdir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        return {
            "ok": False,
            "error": (result.stderr or result.stdout).strip()[-1000:],
        }
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        return {"ok": False, "error": f"probe returned invalid JSON: {error}"}
    return {"ok": True, "value": value}


def _status_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _markdown(report: dict) -> str:
    lines = [
        f"# Artifact comparison: {report['target']['triple']}",
        "",
        f"Reference: {report['reference']['name']}",
        "",
        "| Area | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for row in report["rows"]:
        evidence = str(row.get("evidence", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['area']} | {row['status']} | {evidence} |")
    return "\n".join(lines) + "\n"


def compare_completed_artifact(
    dist: Path,
    target: Target,
    *,
    lock_path: Path,
    cache_root: Path,
    scratch_root: Path,
) -> Path:
    dist = Path(dist)
    try:
        artifact_directory = active_generation(dist)
        manifest_path = artifact_directory / "artifact.json"
        manifest = json.loads(manifest_path.read_text())
    except (GenerationError, OSError, json.JSONDecodeError) as error:
        raise ComparisonError(f"completed artifact manifest is unavailable: {error}") from error
    if manifest.get("target", {}).get("triple") != target.triple:
        raise ComparisonError("completed artifact manifest names a different target")
    archive_name = manifest.get("archive", {}).get("filename")
    if not isinstance(archive_name, str) or Path(archive_name).name != archive_name:
        raise ComparisonError("completed artifact manifest has an unsafe archive filename")
    archive_path = artifact_directory / archive_name
    try:
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    except OSError as error:
        raise ComparisonError(f"completed package archive is unavailable: {error}") from error
    archive_record = manifest.get("archive", {})
    if digest != archive_record.get("sha256") or archive_path.stat().st_size != archive_record.get("size_bytes"):
        raise ComparisonError("completed package archive does not match its artifact manifest")

    scratch_root = Path(scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="comparison-", dir=scratch_root) as temporary:
        workdir = Path(temporary)
        try:
            artifact_root = extract_package_archive(archive_path, workdir / "artifact")
        except PackageError as error:
            raise ComparisonError(f"completed package archive cannot be extracted: {error}") from error
        product = manifest.get("product", {})
        major_minor = ".".join(str(product.get("version", "")).split(".")[:2])
        artifact_python = artifact_root / "python" / "bin" / f"python{major_minor}"
        reference = {
            "name": "unavailable",
            "status": "untested",
            "reason": "no target-specific reference pin is present",
        }
        rows = [{
            "area": "artifact_integrity",
            "status": "match",
            "evidence": f"archive sha256 {digest}",
        }]
        pin = _reference_pin(lock_path, target)
        reference_python = None
        if pin is not None:
            try:
                reference_archive = Cache(cache_root).require(pin)
            except InputError as error:
                reference["name"] = f"{pin.name} {pin.version}"
                reference["reason"] = str(error)
            else:
                extracted = safe_extract(reference_archive, workdir / "reference")
                candidate = extracted / "python" / "bin" / f"python{major_minor}"
                if candidate.is_file():
                    reference_python = candidate
                    reference = {
                        "name": f"{pin.name} {pin.version}",
                        "status": "available",
                    }
                else:
                    reference = {
                        "name": f"{pin.name} {pin.version}",
                        "status": "untested",
                        "reason": f"reference interpreter {candidate.name} is absent",
                    }

        if reference_python is None:
            rows.append({
                "area": "reference_comparison",
                "status": "untested",
                "evidence": reference.get("reason", "reference unavailable"),
            })
        else:
            version_code = (
                "import json,sys; print(json.dumps('.'.join(map(str, sys.version_info[:3]))))"
            )
            for name, code in (
                ("cpython_version", version_code),
                ("openssl_version", "import json,ssl; print(json.dumps(ssl.OPENSSL_VERSION))"),
                ("sqlite_version", "import json,sqlite3; print(json.dumps(sqlite3.sqlite_version))"),
                ("mpdecimal_version", "import decimal,json; print(json.dumps(decimal.__libmpdec_version__))"),
            ):
                ours = _run_probe(artifact_python, code, target, workdir / f"ours-{name}")
                theirs = _run_probe(reference_python, code, target, workdir / f"reference-{name}")
                if not ours["ok"] or not theirs["ok"]:
                    rows.append({
                        "area": name,
                        "status": "untested",
                        "evidence": f"ours={ours.get('error')} reference={theirs.get('error')}",
                    })
                    continue
                ours_value = ours["value"]
                reference_value = theirs["value"]
                rows.append({
                    "area": name,
                    "status": "match" if ours_value == reference_value else "gap",
                    "evidence": f"ours={ours_value!r} reference={reference_value!r}",
                })

        report = {
            "schema_version": 1,
            "target": manifest["target"],
            "artifact": {
                "manifest": manifest_path.name,
                "archive": archive_name,
                "sha256": digest,
            },
            "reference": reference,
            "rows": rows,
            "status_counts": _status_counts(rows),
        }
        report_json = workdir / "parity.json"
        report_md = workdir / "parity.md"
        report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        report_md.write_text(_markdown(report))
        publish_files({
            dist / "parity.json": report_json,
            dist / "parity.md": report_md,
        })
    return dist / "parity.md"
