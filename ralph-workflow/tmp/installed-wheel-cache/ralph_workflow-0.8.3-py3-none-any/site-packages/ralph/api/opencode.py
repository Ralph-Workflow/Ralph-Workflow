"""Fetch and cache the OpenCode model catalog from models.dev.

This module provides access to the OpenCode model catalog for
discovering available models and providers.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

CATALOG_URL = "https://models.dev/api.json"
TIMEOUT_SECS = 10


@dataclass(frozen=True)
class ModelEntry:
    """Single model entry from the catalog.

    Attributes:
        id: Unique model identifier.
        name: Human-readable model name.
        provider: Model provider name.
    """

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


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("Model entry string fields must be strings")


class _CatalogFetcher:
    """Callable cache around the OpenCode catalog."""

    def __init__(self) -> None:
        self._cache: list[ModelEntry] | None = None

    def __call__(self) -> list[ModelEntry]:
        if self._cache is not None:
            return self._cache

        logger.debug("Fetching model catalog from {}", CATALOG_URL)
        payload: object
        try:
            with httpx.Client(timeout=TIMEOUT_SECS) as client:
                response = client.get(CATALOG_URL)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch model catalog: {}", exc)
            raise

        raw = _parse_catalog_payload(payload)

        models = [ModelEntry.model_validate(entry) for entry in raw]
        logger.debug("Loaded {} models from catalog", len(models))
        self._cache = models
        return models

    def cache_clear(self) -> None:
        """Clear any cached catalog."""

        self._cache = None


def _parse_catalog_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        msg = "Model catalog JSON must be a list"
        logger.error(msg)
        raise ValueError(msg)

    parsed: list[dict[str, object]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            msg = "Model catalog entries must be JSON objects"
            logger.error(msg)
            raise ValueError(msg)
        parsed.append({str(key): value for key, value in entry.items()})

    return parsed


fetch_catalog = _CatalogFetcher()


def get_model_by_id(model_id: str) -> ModelEntry | None:
    """Get a specific model by ID.

    Args:
        model_id: Model identifier to look up.

    Returns:
        ModelEntry if found, None otherwise.
    """
    catalog = fetch_catalog()
    for model in catalog:
        if model.id == model_id:
            return model
    return None


def search_models(query: str) -> list[ModelEntry]:
    """Search models by name or provider.

    Args:
        query: Search query (case-insensitive).

    Returns:
        List of matching ModelEntry instances.
    """
    catalog = fetch_catalog()
    query_lower = query.lower()
    return [
        model
        for model in catalog
        if query_lower in (model.name or "").lower()
        or query_lower in (model.provider or "").lower()
        or query_lower in model.id.lower()
    ]


def list_providers() -> list[str]:
    """List all unique providers in the catalog.

    Returns:
        Sorted list of provider names.
    """
    catalog = fetch_catalog()
    providers = {model.provider for model in catalog if model.provider}
    return sorted(providers)
