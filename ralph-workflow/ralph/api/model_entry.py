"""ModelEntry: single model entry from the OpenCode catalog."""

from __future__ import annotations

from dataclasses import dataclass


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("Model entry string fields must be strings")


@dataclass(frozen=True)
class ModelEntry:
    """Single model entry from the catalog."""

    id: str
    name: str | None = None
    provider: str | None = None

    @classmethod
    def model_validate(cls, raw: dict[str, object]) -> ModelEntry:
        """Validate and normalize a raw catalog entry."""
        raw_id = raw.get("id")
        if not isinstance(raw_id, str):
            raise ValueError("Model entry missing required 'id' field")
        name = _optional_str(raw.get("name"))
        provider = _optional_str(raw.get("provider"))
        return cls(id=raw_id, name=name, provider=provider)
