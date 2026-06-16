"""Fetch and cache the OpenCode model catalog from models.dev.

This module provides access to the OpenCode model catalog for
discovering available models and providers.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
from loguru import logger

from ralph.api.model_entry import ModelEntry
from ralph.executor.process import ProcessRunOptions, run_process

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.executor.process import ProcessResult
    from ralph.process.manager import ProcessManager


class ProcessRunner(Protocol):
    """Callable process-execution seam used for local OpenCode preflight probes."""

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
        _pm: ProcessManager | None = None,
    ) -> ProcessResult: ...


CATALOG_URL = "https://models.dev/api.json"
TIMEOUT_SECS = 10
_LOCAL_COMMAND_TIMEOUT_SECS = 30.0


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
    if isinstance(payload, list):
        parsed: list[dict[str, object]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                msg = "Model catalog entries must be JSON objects"
                logger.error(msg)
                raise ValueError(msg)
            parsed.append({str(key): value for key, value in entry.items()})
        return parsed

    if isinstance(payload, dict):
        parsed = []
        for provider_key, provider_entry in payload.items():
            if not isinstance(provider_entry, dict):
                msg = "Model catalog provider entries must be JSON objects"
                logger.error(msg)
                raise ValueError(msg)
            models = provider_entry.get("models")
            if not isinstance(models, dict):
                msg = "Model catalog provider entries must contain a models object"
                logger.error(msg)
                raise ValueError(msg)
            for model_key, model_entry in models.items():
                if not isinstance(model_entry, dict):
                    msg = "Model catalog model entries must be JSON objects"
                    logger.error(msg)
                    raise ValueError(msg)
                model_name = model_entry.get("name")
                parsed.append(
                    {
                        "id": f"{provider_key}/{model_key}",
                        "provider": str(provider_key),
                        "name": str(model_name) if isinstance(model_name, str) else None,
                    }
                )
        return parsed

    msg = "Model catalog JSON must be a list or provider map"
    logger.error(msg)
    raise ValueError(msg)


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


def _path_command_candidates(command: str, env_path: str | None = None) -> tuple[str, ...]:
    path_value = env_path if env_path is not None else os.environ.get("PATH", "")
    seen: set[str] = set()
    candidates: list[str] = []
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / command
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates.append(resolved)
    return tuple(candidates)


def _local_opencode_version(
    command: str,
    *,
    _run_process: ProcessRunner = run_process,
) -> str | None:
    result = _run_process(
        command,
        ["--version"],
        options=ProcessRunOptions(timeout=_LOCAL_COMMAND_TIMEOUT_SECS),
    )
    if result.returncode != 0:
        return None
    version = result.stdout.strip() or result.stderr.strip()
    return version or None


def _normalize_local_model_lines(stdout: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in stdout.splitlines() if line.strip())


def validate_local_model_support(
    model_id: str,
    *,
    command: str = "opencode",
    env_path: str | None = None,
    _run_process: ProcessRunner = run_process,
) -> str | None:
    """Return a human-readable error when the local OpenCode binary cannot use a model."""
    if "/" not in model_id:
        return None

    provider, _, _model_name = model_id.partition("/")
    resolved = shutil.which(command, path=env_path)
    candidates = _path_command_candidates(command, env_path)
    version = _local_opencode_version(command, _run_process=_run_process)
    chosen = resolved or command
    version_suffix = f" (version {version})" if version else ""
    extra_candidates = tuple(path for path in candidates if path != resolved)
    candidates_suffix = ""
    if extra_candidates:
        candidates_suffix = " Other PATH candidates: " + ", ".join(extra_candidates)
    path_prefix = (
        f"the first '{command}' on PATH is '{chosen}'{version_suffix}"
        if resolved is not None
        else f"'{command}' resolved to '{chosen}'{version_suffix}"
    )

    result = _run_process(
        command,
        ["models", "--refresh", provider],
        options=ProcessRunOptions(timeout=_LOCAL_COMMAND_TIMEOUT_SECS),
    )
    details = (result.stderr.strip() or result.stdout.strip()).strip()

    if result.returncode != 0:
        return (
            f"OpenCode local model preflight failed for '{model_id}': {path_prefix} and "
            f"it could not refresh provider '{provider}'. "
            f"{details or 'No error details were returned.'}{candidates_suffix}"
        )

    available_models = _normalize_local_model_lines(result.stdout)
    if model_id in available_models:
        return None

    sample_suffix = ""
    if available_models:
        sample_suffix = " Available models after refresh: " + ", ".join(available_models[:5])
    return (
        f"OpenCode local model preflight failed for '{model_id}': {path_prefix}, but that "
        "binary does not list this model after refresh."
        f"{sample_suffix}{candidates_suffix}"
    )
