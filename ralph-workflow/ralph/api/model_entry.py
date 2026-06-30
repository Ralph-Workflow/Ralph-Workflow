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
    """Single model entry from the catalog.

    Attributes:
        id: Required fully-qualified identifier of the form
            ``"provider/model"``. Set from the catalog's
            provider / model key pair; unique within a single
            ``fetch_catalog()`` snapshot.
        name: Optional human-readable display name. ``None`` when
            the catalog entry omits it.
        provider: Optional provider slug (matches the catalog's
            provider key). ``None`` when the catalog entry omits it.

    The dataclass is ``frozen=True`` so callers can hash and compare
    entries safely across catalog refreshes.
    """

    id: str
    name: str | None = None
    provider: str | None = None

    @classmethod
    def model_validate(cls, raw: dict[str, object]) -> ModelEntry:
        """Validate and normalize a raw catalog entry.

        Args:
            raw: Mapping parsed from a single catalog record. Must
                contain ``"id"``; ``"name"`` and ``"provider"`` are
                optional.

        Returns:
            A :class:`ModelEntry` with ``name`` and ``provider``
            coerced to ``str | None``.

        Raises:
            ValueError: When ``raw["id"]`` is missing or not a
                ``str``, or when a provided ``"name"`` /
                ``"provider"`` value is neither ``None`` nor a
                ``str``.
        """
        raw_id = raw.get("id")
        if not isinstance(raw_id, str):
            raise ValueError("Model entry missing required 'id' field")
        name = _optional_str(raw.get("name"))
        provider = _optional_str(raw.get("provider"))
        return cls(id=raw_id, name=name, provider=provider)
