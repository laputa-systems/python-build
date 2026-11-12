"""Install the locked Mozilla CA bundle as a relocatable trust fallback."""

from __future__ import annotations

import shutil
import ssl
import tempfile
from pathlib import Path

from .inputs import Cache, InputError, locked_inputs, safe_extract
from .product import PRODUCT

INPUT_NAME = "certifi-ca"
BUNDLE_RELATIVE_PATH = PRODUCT.stdlib_relative_path / "python-build-cacert.pem"
LICENSE_DIRECTORY = Path("share/licenses/python-build")


class CABundleError(Exception):
    """The locked CA trust bundle could not be installed safely."""


def install_fallback_ca_bundle(
    install: Path,
    *,
    cache_root: Path,
    lock_path: Path,
) -> dict[str, object]:
    """Verify, validate, and install only certifi's PEM and license files."""
    entries = [entry for entry in locked_inputs(lock_path) if entry.name == INPUT_NAME]
    if len(entries) != 1:
        raise CABundleError(f"expected one {INPUT_NAME!r} entry in {lock_path}")
    entry = entries[0]
    if entry.role != "source" or entry.license != "MPL-2.0":
        raise CABundleError(
            f"{INPUT_NAME}: expected a source input licensed MPL-2.0"
        )

    try:
        blob = Cache(cache_root).require(entry)
    except InputError as error:
        raise CABundleError(f"{INPUT_NAME}: locked source is unavailable: {error}") from error

    install = Path(install)
    bundle = install / BUNDLE_RELATIVE_PATH
    license_relative_path = LICENSE_DIRECTORY / f"certifi-{entry.version}-LICENSE"
    license_file = install / license_relative_path
    if bundle.exists() or bundle.is_symlink() or license_file.exists() or license_file.is_symlink():
        raise CABundleError("refusing to overwrite existing fallback CA bundle or license")

    with tempfile.TemporaryDirectory(prefix="certifi-ca-") as temporary:
        extracted = safe_extract(blob, Path(temporary) / "source")
        source_bundles = sorted(extracted.glob("*/certifi/cacert.pem"))
        if len(source_bundles) != 1:
            raise CABundleError(
                f"{INPUT_NAME}: expected one versioned certifi/cacert.pem in source archive"
            )
        source_bundle = source_bundles[0]
        source_root = source_bundle.parents[1]
        source_license = source_root / "LICENSE"
        if not source_license.is_file():
            raise CABundleError(
                f"{INPUT_NAME}: source archive must contain certifi/cacert.pem and LICENSE"
            )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            context.load_verify_locations(cafile=str(source_bundle))
        except (OSError, ssl.SSLError) as error:
            raise CABundleError(f"{INPUT_NAME}: invalid CA bundle: {error}") from error
        ca_count = context.cert_store_stats()["x509_ca"]
        if ca_count < 1:
            raise CABundleError(f"{INPUT_NAME}: CA bundle contains no trusted roots")

        bundle.parent.mkdir(parents=True, exist_ok=True)
        license_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_bundle, bundle)
        shutil.copyfile(source_license, license_file)
        bundle.chmod(0o644)
        license_file.chmod(0o644)

    return {
        "name": INPUT_NAME,
        "version": entry.version,
        "sha256": entry.sha256,
        "license": entry.license,
        "ca_certificates": ca_count,
        "bundle_path": str(BUNDLE_RELATIVE_PATH),
        "license_path": str(license_relative_path),
        "policy": "fallback only when OpenSSL default paths load no CAs",
    }
