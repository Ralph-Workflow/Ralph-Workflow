from __future__ import annotations

import re
from dataclasses import dataclass

_PROVIDER_SLUG_NORMALISE_PATTERN: re.Pattern[str] = re.compile(r"[^a-z0-9_-]")


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("Model entry string fields must be strings")


def _normalise_provider_slug(raw: str) -> str:
    lowered = raw.lower()
    return _PROVIDER_SLUG_NORMALISE_PATTERN.sub("-", lowered)


def _normalise_modalities_input(raw: object | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        msg = "Model entry 'modalities' field must be an object"
        raise ValueError(msg)
    input_field = raw.get("input")
    if input_field is None:
        return ()
    if not isinstance(input_field, list):
        msg = "Model entry 'modalities.input' field must be a list"
        raise ValueError(msg)
    modalities: list[str] = []
    for item in input_field:
        if not isinstance(item, str):
            msg = "Model entry 'modalities.input' items must be strings"
            raise ValueError(msg)
        modalities.append(item)
    return tuple(modalities)


@dataclass(frozen=True)
class ModelEntry:
    id: str
    name: str | None = None
    provider: str | None = None
    modalities_input: tuple[str, ...] = ()
    provider_slug: str | None = None

    @classmethod
    def model_validate(cls, raw: dict[str, object]) -> ModelEntry:
        raw_id = raw.get("id")
        if not isinstance(raw_id, str):
            raise ValueError("Model entry missing required 'id' field")
        name = _optional_str(raw.get("name"))
        provider = _optional_str(raw.get("provider"))
        modalities_input = _normalise_modalities_input(raw.get("modalities"))
        provider_slug = _normalise_provider_slug(provider) if provider is not None else None
        return cls(
            id=raw_id,
            name=name,
            provider=provider,
            modalities_input=modalities_input,
            provider_slug=provider_slug,
        )
