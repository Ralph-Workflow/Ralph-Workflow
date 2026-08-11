"""ModelEntry: single model entry from the OpenCode catalog."""

from __future__ import annotations

# Characters preserved verbatim by the parser when normalising the
# provider slug. Anything outside this allowlist (uppercase letters,
# spaces, punctuation, symbols) is replaced with ``-`` so providers
# like ``Anthropic`` and ``bedrock us`` normalise to canonical,
# lowercase, alphanum-+-underscore slugs.
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
    """Lowercase and strip disallowed characters from a provider slug.

    The replacement character is ``-`` so consecutive disallowed chars
    collapse to a single dash rather than producing empty slots. This
    matches the catalog's slug convention (``anthropic``, ``openai``,
    ``amazon-bedrock``).
    """
    lowered = raw.lower()
    return _PROVIDER_SLUG_NORMALISE_PATTERN.sub("-", lowered)


def _normalise_modalities_input(raw: object | None) -> tuple[str, ...]:
    """Extract the catalog's ``modalities.input`` array as a tuple of strings.

    The catalog payload carries ``modalities: {input: [...], output: [...]}``
    per model. Callers want a stable, hashable tuple so the entry is
    directly comparable across catalog refreshes.

    Raises:
        ValueError: When ``modalities.input`` is present but contains
            a non-string element, or when ``modalities`` is present but
            is not a dict.
    """
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
        modalities_input: Tuple of supported input modality strings
            (e.g. ``("text", "image")``) preserved from the catalog's
            ``modalities.input`` field. Empty tuple when the catalog
            entry omits the field.
        provider_slug: Normalized provider slug — lowercase, with only
            alphanumerics, ``-`` and ``_`` retained. Always populated
            when ``provider`` is set; ``None`` otherwise. Computed by
            collapsing any non-allowlist character to ``-`` so catalog
            slugs like ``Anthropic`` or ``bedrock us`` map to the
            canonical form (``anthropic``, ``bedrock-us``).
    """

    id: str
    name: str | None = None
    provider: str | None = None
    modalities_input: tuple[str, ...] = ()
    provider_slug: str | None = None

    @classmethod
    def model_validate(cls, raw: dict[str, object]) -> ModelEntry:
        """Validate and normalize a raw catalog entry.

        Args:
            raw: Mapping parsed from a single catalog record. Must
                contain ``"id"``; ``"name"``, ``"provider"`` and
                ``"modalities"`` are optional.

        Returns:
            A :class:`ModelEntry` with ``name`` and ``provider``
            coerced to ``str | None`` and ``provider_slug`` populated
            from ``provider`` via :func:`_normalise_provider_slug`
            (lowercase + alphanum-+-underscore only). ``modalities_input``
            is populated from ``raw["modalities"]["input"]`` as a
            tuple of strings.

        Raises:
            ValueError: When ``raw["id"]`` is missing or not a
                ``str``, when a provided ``"name"`` /
                ``"provider"`` value is neither ``None`` nor a
                ``str``, or when ``"modalities.input"`` carries
                non-string items.
        """
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
