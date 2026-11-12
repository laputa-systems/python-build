"""Product identity derived from the locked CPython source input."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from buildlib.release import archive_name, manifest_name

from .inputs import InputError, locked_inputs

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = REPOSITORY_ROOT / "sources.lock.json"
PRODUCT_NAME = "cpython"


class ProductError(Exception):
    """The locked CPython source does not describe a stable product version."""


@dataclass(frozen=True)
class Product:
    version: tuple[int, int, int]

    def __post_init__(self) -> None:
        if type(self.version) is not tuple or len(self.version) != 3 or any(
            type(part) is not int or part < 0 for part in self.version
        ):
            raise ProductError("product version must contain three nonnegative integers")
        if self.version[0] != 3:
            raise ProductError("product must use CPython major version 3")

    @property
    def version_string(self) -> str:
        return ".".join(str(part) for part in self.version)

    @property
    def major_minor(self) -> str:
        return ".".join(str(part) for part in self.version[:2])

    @property
    def abi_tag(self) -> str:
        return f"cp{self.version[0]}{self.version[1]}"

    @property
    def interpreter_name(self) -> str:
        return f"python{self.major_minor}"

    @property
    def config_name(self) -> str:
        return f"{self.interpreter_name}-config"

    @property
    def stdlib_relative_path(self) -> Path:
        return Path("lib") / self.interpreter_name

    @property
    def include_relative_path(self) -> Path:
        return Path("include") / self.interpreter_name

    @property
    def shared_library_stem(self) -> str:
        return self.interpreter_name

    def interpreter(self, root: Path) -> Path:
        return Path(root) / "bin" / self.interpreter_name

    def config_tool(self, root: Path) -> Path:
        return Path(root) / "bin" / self.config_name

    def stdlib(self, root: Path) -> Path:
        return Path(root) / self.stdlib_relative_path

    def headers(self, root: Path) -> Path:
        return Path(root) / self.include_relative_path

    def local_archive_name(self, target: str, revision: str) -> str:
        return f"cpython-{self.version_string}-{target}-{revision}.tar.gz"

    def mirror_archive_name(self, build: str, target: str) -> str:
        return f"cpython-{self.version_string}+{build}-{target}-install_only_stripped.tar.gz"

    def release_archive_name(self, target: str) -> str:
        return archive_name(PRODUCT_NAME, self.version_string, target)

    def release_manifest_name(self, target: str) -> str:
        return manifest_name(PRODUCT_NAME, self.version_string, target)


def load_product(lock_path: Path = SOURCE_LOCK) -> Product:
    try:
        entries = locked_inputs(Path(lock_path))
    except InputError as error:
        raise ProductError(str(error)) from error
    pins = [entry for entry in entries if entry.name == "cpython"]
    if len(pins) != 1:
        raise ProductError(f"{lock_path}: expected exactly one cpython source pin")
    pin = pins[0]
    if pin.role != "source":
        raise ProductError(f"{lock_path}: cpython must be a production source input")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", pin.version)
    if match is None:
        raise ProductError(f"{lock_path}: unsupported CPython release {pin.version!r}")
    return Product(tuple(int(value) for value in match.groups()))


PRODUCT = load_product()
