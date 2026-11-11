
from __future__ import annotations

import re
import shlex

_MODEL_FLAGS: frozenset[str] = frozenset({"--model", "-m"})
_PROVIDER_FLAGS: frozenset[str] = frozenset({"--provider"})

_QUALIFIED_SEPARATOR: str = "/"

_PROVIDER_NORMALISE_PATTERN: re.Pattern[str] = re.compile(r"[^a-z0-9_-]")


def _normalise_provider_slug(raw: str) -> str:
    lowered = raw.lower()
    return _PROVIDER_NORMALISE_PATTERN.sub("-", lowered)


def parse_model_flag(flag: str) -> tuple[str | None, str | None]:
    if not flag or not flag.strip():
        return (None, None)

    try:
        parts = shlex.split(flag)
    except ValueError:
        return (None, None)

    provider_value: str | None = None
    model_value: str | None = None

    index = 0
    while index < len(parts):
        token = parts[index]

        if "=" in token:
            flag_name, _, flag_value = token.partition("=")
            if flag_name in _MODEL_FLAGS and flag_value:
                model_value = flag_value
            elif flag_name in _PROVIDER_FLAGS and flag_value:
                provider_value = flag_value
            index += 1
            continue

        if token in _MODEL_FLAGS and index + 1 < len(parts):
            model_value = parts[index + 1]
            index += 2
            continue

        if token in _PROVIDER_FLAGS and index + 1 < len(parts):
            provider_value = parts[index + 1]
            index += 2
            continue

        if model_value is None and not token.startswith("-"):
            model_value = token
        index += 1

    if model_value is None:
        return (None, None)

    if _QUALIFIED_SEPARATOR in model_value:
        provider_raw, _, model_id = model_value.partition(_QUALIFIED_SEPARATOR)
        provider_value = provider_raw
        if not model_id:
            return (None, None)
        model_value = model_id

    if provider_value is None:
        return (None, model_value)

    return (_normalise_provider_slug(provider_value), model_value)


__all__ = ["parse_model_flag"]
