"""Single fallback seam for optional TOML configuration files.

Provides resilient loading and validation of optional configuration files,
falling back to built-in defaults on syntax errors, decoding issues,
permission errors, or schema validation failures when strict mode is disabled.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, overload

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

# Process-level registry for deduplicating fallback warnings.
# Keyed by (resolved Path, sha256_of_bytes | error_class).
_FALLBACK_WARNING_REGISTRY: set[tuple[Path, str | type[BaseException]]] = set()


def reset_fallback_warning_registry() -> None:
    """Clear the per-process fallback warning deduplication registry."""
    _FALLBACK_WARNING_REGISTRY.clear()


def _format_error_detail(exc: BaseException) -> str:
    """Format an exception message, appending line/col if available."""
    exc_msg = str(exc)
    lineno: object = getattr(exc, "lineno", None)
    colno: object = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        if "line" not in exc_msg.lower():
            return f"{exc_msg} (line {lineno}, column {colno})"
    elif isinstance(lineno, int) and "line" not in exc_msg.lower():
        return f"{exc_msg} (line {lineno})"
    return exc_msg


def _compute_file_fingerprint(
    path: Path,
    exc: BaseException,
    raw_bytes: bytes | None = None,
) -> str | type[BaseException]:
    """Compute dedup fingerprint: sha256 of file bytes or error class."""
    if raw_bytes is not None:
        return hashlib.sha256(raw_bytes).hexdigest()
    try:
        # filesystem-read-ok: read file bytes for fallback dedup fingerprint
        data_bytes = path.read_bytes()
        return hashlib.sha256(data_bytes).hexdigest()
    except OSError:
        return type(exc)


def _emit_fallback_warning(
    path: Path,
    exc: BaseException,
    kind: str,
    raw_bytes: bytes | None = None,
) -> None:
    """Emit a deduped loguru warning for a fallback to built-in default."""
    resolved = path.resolve()
    fingerprint = _compute_file_fingerprint(resolved, exc, raw_bytes=raw_bytes)
    key = (resolved, fingerprint)
    if key in _FALLBACK_WARNING_REGISTRY:
        return
    _FALLBACK_WARNING_REGISTRY.add(key)

    error_detail = _format_error_detail(exc)
    logger.warning(
        f"Failed to load {kind} configuration from {path}: {error_detail}; "
        f"using built-in default. Fix: run `ralph --check-config`."
    )


@overload
def load_optional_toml(
    path: Path | str,
    *,
    kind: str,
    strict: bool = False,
    default_factory: None = None,
) -> dict[str, object]: ...


@overload
def load_optional_toml[T](
    path: Path | str,
    *,
    kind: str,
    strict: bool = False,
    default_factory: Callable[[], T],
) -> dict[str, object] | T: ...


def load_optional_toml[T](
    path: Path | str,
    *,
    kind: str,
    strict: bool = False,
    default_factory: Callable[[], T] | None = None,
) -> dict[str, object] | T:
    """Read an optional TOML file; return default on missing or fallback.

    Never writes, deletes, or modifies the file.
    When strict=True, errors (TOMLDecodeError, UnicodeDecodeError, OSError) are re-raised.
    When strict=False, errors return the default (e.g. {} or default_factory())
    and emit a loguru warning mentioning the path, parse error (with line/col
    if available), default in use, and fix command (`ralph --check-config`).
    Warnings are deduplicated per process by (resolved path, sha256 | error_class).
    """
    path_obj = Path(path)
    raw_bytes: bytes | None = None
    try:
        # filesystem-read-ok: load optional toml file content for parsing and dedup hashing
        raw_bytes = path_obj.read_bytes()
    except FileNotFoundError:
        return default_factory() if default_factory is not None else {}
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            raise
        _emit_fallback_warning(path_obj, exc, kind=kind, raw_bytes=raw_bytes)
        return default_factory() if default_factory is not None else {}

    try:
        text = raw_bytes.decode("utf-8")
        data: dict[str, object] = tomllib.loads(text)
        return data
    except (ValueError, UnicodeDecodeError) as exc:
        if strict:
            raise
        _emit_fallback_warning(path_obj, exc, kind=kind, raw_bytes=raw_bytes)
        return default_factory() if default_factory is not None else {}


def validate_or_default[DataT, T](
    path: Path | str,
    data: DataT,
    validator: Callable[[DataT], T],
    default: T,
    kind: str,
    *,
    strict: bool = False,
) -> T:
    """Validate data with validator; return default and warn on validation error.

    When strict=True, validation errors (Exception or SystemExit) are re-raised.
    When strict=False, replaces data with default, emits a loguru warning with
    the same format and deduplication as load_optional_toml.
    """
    path_obj = Path(path)
    try:
        return validator(data)
    except (Exception, SystemExit) as exc:
        if strict:
            raise
        _emit_fallback_warning(path_obj, exc, kind=kind)
        return default


__all__ = [
    "load_optional_toml",
    "reset_fallback_warning_registry",
    "validate_or_default",
]
