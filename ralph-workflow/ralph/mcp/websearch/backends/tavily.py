"""Tavily web-search backend.

Implements the ``TavilyBackend`` dataclass that wraps the ``tavily-python`` SDK
to deliver web-search results via the Tavily API. Requires ``pip install
ralph-workflow[web-search]`` (or ``pip install tavily-python``) at runtime;
importing without the SDK is safe, but calling ``search`` raises
``WebSearchError``.

API key resolution:

- Pass ``api_key`` directly, or
- set ``api_key_env`` to an environment variable name that holds the key
  (resolved via ``ralph.mcp.websearch.secrets.resolve_secret``).

Typical usage (from ``ralph.config.mcp_models`` backend selection)::

    backend = TavilyBackend(api_key_env="TAVILY_API_KEY")
    results = backend.search("FastAPI dependency injection", limit=5)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, cast

from ..secrets import resolve_secret
from .base import SearchResult, WebSearchError

if TYPE_CHECKING:
    from typing import Protocol

    class _TavilyClient(Protocol):
        def search(self, query: str, *, max_results: int) -> Mapping[str, object]: ...

    class _TavilyClientType(Protocol):
        def __call__(self, *, api_key: str) -> _TavilyClient: ...


@dataclass(frozen=True)
class TavilyBackend:
    """Backend powered by the Tavily Python SDK."""

    api_key: str | None = None
    api_key_env: str | None = None

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        client_type = _load_tavily_client_type()
        resolved_key = resolve_secret(self.api_key, self.api_key_env)
        try:
            payload = client_type(api_key=resolved_key).search(query, max_results=limit)
        except Exception as exc:
            raise WebSearchError("tavily search failed") from exc
        return _normalize_results(payload)


def _load_tavily_client_type() -> _TavilyClientType:
    try:
        module = import_module("tavily")
    except ImportError as exc:
        raise WebSearchError(
            "backend 'tavily' requires 'pip install ralph-workflow[web-search]'"
        ) from exc
    client_type = cast("object | None", getattr(module, "TavilyClient", None))
    if client_type is None:
        raise WebSearchError("tavily backend is unavailable")
    return cast("_TavilyClientType", client_type)


def _normalize_results(payload: Mapping[str, object]) -> list[SearchResult]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, Iterable):
        return []
    results: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, Mapping):
            continue
        title = _string_value(item, "title")
        url = _string_value(item, "url")
        snippet = _string_value(item, "content")
        if not title or not url:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


def _string_value(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = ["TavilyBackend"]
