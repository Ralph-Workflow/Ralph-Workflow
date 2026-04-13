"""Fetch and cache the OpenCode model catalog from models.dev.

This module provides access to the OpenCode model catalog for
discovering available models and providers.
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from loguru import logger
from pydantic import BaseModel

CATALOG_URL = "https://models.dev/api.json"
TIMEOUT_SECS = 10


class ModelEntry(BaseModel):
    """Single model entry from the catalog.

    Attributes:
        id: Unique model identifier.
        name: Human-readable model name.
        provider: Model provider name.
    """

    id: str
    name: str | None = None
    provider: str | None = None


@lru_cache(maxsize=1)
def fetch_catalog() -> list[ModelEntry]:
    """Fetch model catalog from models.dev.

    The catalog is cached for the process lifetime since it's
    fetched infrequently and should be consistent within a run.

    Returns:
        List of ModelEntry instances.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    logger.debug("Fetching model catalog from {}", CATALOG_URL)
    try:
        with httpx.Client(timeout=TIMEOUT_SECS) as client:
            response = client.get(CATALOG_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch model catalog: {}", exc)
        raise

    try:
        raw: list[dict[str, object]] = response.json()
    except Exception as exc:
        logger.error("Failed to parse model catalog JSON: {}", exc)
        raise

    models = [ModelEntry.model_validate(entry) for entry in raw]
    logger.debug("Loaded {} models from catalog", len(models))
    return models


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
