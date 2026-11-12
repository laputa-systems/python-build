"""Build-system controller for the x86_64 musl CPython distribution."""

from .wheel import ensure as _ensure_buildlib

_ensure_buildlib()

from .inputs import (
    Cache,
    InputError,
    canonical_json,
    identity,
    locked_inputs,
    safe_extract,
)

__all__ = [
    "Cache",
    "InputError",
    "canonical_json",
    "identity",
    "locked_inputs",
    "safe_extract",
]
