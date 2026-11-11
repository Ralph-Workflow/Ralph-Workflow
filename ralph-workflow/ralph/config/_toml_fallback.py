
from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, overload

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

_MAX_FALLBACK_WARNING_REGISTRY_ENTRIES = 256

_FALLBACK_WARNING_REGISTRY: dict[
    tuple[Path, str | type[BaseException]], None
] = {}


def reset_fallback_warning_registry() -> None:
    _FALLBACK_WARNING_REGISTRY.clear()


def _format_error_detail(exc: BaseException) -> str:
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
    if kind == "agents":
        return True
    path_name = Path(path).name.lower()
    return "agents" in path_name


def _compute_file_fingerprint(
    path: Path,
    exc: BaseException,
    raw_bytes: bytes | None = None,
) -> str | type[BaseException]:
    if raw_bytes is not None:
        return hashlib.sha256(raw_bytes).hexdigest()
    try:
        data_bytes = path.read_bytes()
        return hashlib.sha256(data_bytes).hexdigest()
    except OSError:
        return type(exc)


def describe_config_failure(exc: BaseException) -> str:
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


_AGENTS_RECOVERY_COMMAND = "ralph --regenerate-config"
_CHECK_CONFIG_COMMAND = "ralph --check-config"


def effective_fix_hint(path: Path, kind: str, fix_hint: str) -> str:
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
    path_obj = Path(path)
    raw_bytes: bytes | None = None
    try:
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
