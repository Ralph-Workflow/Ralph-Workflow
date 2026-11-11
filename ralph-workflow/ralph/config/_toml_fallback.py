"""Single fallback seam for optional TOML configuration files.

Provides resilient loading and validation of optional configuration files,
falling back to built-in defaults on syntax errors, decoding issues,
permission errors, or schema validation failures when strict mode is disabled.

This module is also the single RECORDER for configuration-failure
warnings. Both failure paths report through
:func:`emit_config_failure_warning`:

- the soft fallback path (:func:`load_optional_toml`,
  :func:`validate_or_default`), where the built-in default takes over;
- the loud path (a broken ``ralph-workflow.toml``), which has no built-in
  default to fall back to and keeps the strict behaviour.

Both record the failure under one ``(resolved path, sha256 of the file
bytes | error class)`` key, so an unchanged broken file warns exactly once
per process while a file whose content changed warns again.
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
#: Maximum number of remembered (path, content) pairs. The registry is an
#: insertion-ordered mapping, so the oldest pair is evicted first once the
#: cap is reached and a long-lived process cannot grow it without bound.
_MAX_FALLBACK_WARNING_REGISTRY_ENTRIES = 256

_FALLBACK_WARNING_REGISTRY: dict[
    tuple[Path, str | type[BaseException]], None
] = {}  # bounded-accumulator-ok: FIFO-capped at _MAX_FALLBACK_WARNING_REGISTRY_ENTRIES entries


def reset_fallback_warning_registry() -> None:
    """Clear the per-process fallback warning deduplication registry."""
    _FALLBACK_WARNING_REGISTRY.clear()


def _format_error_detail(exc: BaseException) -> str:
    """Format an exception message, appending line/col if available."""
    exc_msg = str(exc)
    lineno: object = getattr(exc, "lineno", None)
    colno: object = getattr(exc, "colno", None)
    if isinstance(lineno, int) and isinstance(colno, int):
        has_line = "line" in exc_msg.lower()
        has_col = "column" in exc_msg.lower() or "col" in exc_msg.lower()
        if not has_line and not has_col:
            return f"{exc_msg} (line {lineno}, column {colno})"
        if not has_line:
            return f"{exc_msg} (line {lineno})"
        if not has_col:
            return f"{exc_msg} (column {colno})"
    elif isinstance(lineno, int) and "line" not in exc_msg.lower():
        return f"{exc_msg} (line {lineno})"
    return exc_msg


def _is_agents_config(path: Path | str, kind: str) -> bool:
    """Return True if the target is an agents configuration file."""
    if kind == "agents":
        return True
    path_name = Path(path).name.lower()
    return "agents" in path_name


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


def describe_config_failure(exc: BaseException) -> str:
    """Return the underlying parse error of a configuration failure.

    Walks the ``__cause__`` chain and returns the deepest cause that
    carries a message: the raw ``TOMLDecodeError`` -- with its line and
    column -- for a syntax failure, the pydantic ``ValidationError`` for
    a schema failure. A wrapper that only repeats "something went wrong"
    is never returned while a cause with a real message exists.

    Args:
        exc: The exception the configuration failure surfaced.

    Returns:
        The most specific error text available for the failure.
    """
    causes: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None:
        causes.append(current)
        current = current.__cause__
    for cause in reversed(causes):
        detail = _format_error_detail(cause).strip()
        if detail:
            return detail
    return _format_error_detail(exc)


#: Operator-facing recovery command that restores a broken agents file.
_AGENTS_RECOVERY_COMMAND = "ralph --regenerate-config"
#: Operator-facing command that validates the configuration without rewriting it.
_CHECK_CONFIG_COMMAND = "ralph --check-config"


def effective_fix_hint(path: Path, kind: str, fix_hint: str) -> str:
    """Return the operator-facing hint, naming the agents recovery command.

    An unreadable agents file must name ``ralph --regenerate-config`` --
    the command that restores the file -- and not only
    ``ralph --check-config``, which only validates it. Every operator-facing
    sink (the loguru record and the console panel) renders this one value,
    so the operator never reads two different recovery paths.

    Args:
        path: The configuration file the operator has to fix.
        kind: The configuration layer the file belongs to.
        fix_hint: The raw operator-facing fix instruction.

    Returns:
        The fix hint the operator must see.
    """
    path_obj = Path(path)
    if not _is_agents_config(path_obj, kind):
        return fix_hint
    if _AGENTS_RECOVERY_COMMAND in fix_hint:
        return fix_hint
    check_config = f"`{_CHECK_CONFIG_COMMAND}`"
    if check_config in fix_hint:
        return fix_hint.replace(
            check_config,
            f"`{_AGENTS_RECOVERY_COMMAND}` or {check_config}",
        )
    return f"{fix_hint} Fix: run `{_AGENTS_RECOVERY_COMMAND}`."


def emit_config_failure_warning(
    path: Path | str,
    *,
    kind: str,
    error_detail: str,
    behaviour: str,
    fix_hint: str,
    exc: BaseException,
    raw_bytes: bytes | None = None,
) -> bool:
    """Emit ONE deduplicated WARN for a configuration file that failed to load.

    The single recorder behind both configuration failure paths:

    - the soft fallback path (:func:`load_optional_toml`,
      :func:`validate_or_default`), where the built-in default takes over;
    - the loud path (a broken ``ralph-workflow.toml``), which has no
      built-in default and keeps the strict behaviour.

    Both record the failure under one ``(resolved path, sha256 of the file
    bytes | error class)`` key, so an unchanged broken file warns exactly
    once per process and a file whose content changed warns again.

    Args:
        path: The configuration file the operator has to fix.
        kind: What the file configures (``agents``, ``policy``,
            ``ralph-workflow``, ...).
        error_detail: The underlying parse error, with line and column
            when the underlying error carries them.
        behaviour: The behaviour in effect -- ``using built-in default``
            for the soft path, the strict behaviour for the loud path.
        fix_hint: The operator-facing fix instruction.
        exc: The exception the failure carries; its class is the
            fingerprint fallback when the file bytes cannot be read.
        raw_bytes: The file bytes, when the caller already read them.

    Returns:
        ``True`` when the warning was emitted, ``False`` when it was
        suppressed because the same (file, content) pair already warned.
    """
    path_obj = Path(path)
    resolved = path_obj.resolve()
    effective_hint = effective_fix_hint(path_obj, kind, fix_hint)

    fingerprint = _compute_file_fingerprint(resolved, exc, raw_bytes=raw_bytes)
    key = (resolved, fingerprint)
    if key in _FALLBACK_WARNING_REGISTRY:
        return False
    _FALLBACK_WARNING_REGISTRY[key] = None
    while len(_FALLBACK_WARNING_REGISTRY) > _MAX_FALLBACK_WARNING_REGISTRY_ENTRIES:
        oldest = next(iter(_FALLBACK_WARNING_REGISTRY))
        del _FALLBACK_WARNING_REGISTRY[oldest]

    logger.warning(
        f"Failed to load {kind} configuration from {path_obj}: {error_detail}; "
        f"{behaviour}. {effective_hint}"
    )
    return True


def _emit_fallback_warning(
    path: Path,
    exc: BaseException,
    kind: str,
    raw_bytes: bytes | None = None,
) -> None:
    """Emit a deduped loguru warning for a fallback to built-in default."""
    if _is_agents_config(path, kind):
        fix_hint = "Fix: run `ralph --regenerate-config` or `ralph --check-config`."
    else:
        fix_hint = "Fix: run `ralph --check-config`."
    emit_config_failure_warning(
        path,
        kind=kind,
        error_detail=_format_error_detail(exc),
        behaviour="using built-in default",
        fix_hint=fix_hint,
        exc=exc,
        raw_bytes=raw_bytes,
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
    if available), default in use, and fix command (`ralph --regenerate-config`
    for agents, `ralph --check-config`).
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
    "describe_config_failure",
    "emit_config_failure_warning",
    "load_optional_toml",
    "reset_fallback_warning_registry",
    "validate_or_default",
]
