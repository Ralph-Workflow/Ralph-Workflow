"""Secret resolution helpers for web-search backends."""

from __future__ import annotations

import os
from collections.abc import Callable

from .backends.base import WebSearchError

type EnvGetter = Callable[[str], str | None]


def resolve_secret(
    api_key: str | None,
    api_key_env: str | None,
    *,
    getenv: EnvGetter = os.getenv,
) -> str:
    """Resolve a backend secret from either an inline value or an env var name."""

    normalized_key = _normalize_optional_string(api_key)
    normalized_env = _normalize_optional_string(api_key_env)
    provided = [value is not None for value in (normalized_key, normalized_env)]

    if sum(provided) != 1:
        raise ValueError("configure exactly one of 'api_key' or 'api_key_env'")
    if normalized_key is not None:
        return normalized_key
    if normalized_env is None:
        raise ValueError("configure exactly one of 'api_key' or 'api_key_env'")

    resolved = getenv(normalized_env)
    if not resolved:
        raise WebSearchError(f"environment variable '{normalized_env}' is not set")
    return resolved


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


__all__ = ["resolve_secret"]
