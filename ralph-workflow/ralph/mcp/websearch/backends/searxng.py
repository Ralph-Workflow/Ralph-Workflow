"""SearXNG web-search backend.

Implements the ``SearxngBackend`` dataclass that queries a self-hosted SearXNG
instance over HTTP.  Unlike the API-key backends (Exa, Tavily, Brave), this
backend requires no credentials — only the base URL of a running SearXNG
server.

The backend POSTs to ``{url}/search?format=json`` with a 10-second timeout and
normalises the JSON response into a list of ``SearchResult`` objects.  Network
errors and non-200 responses raise ``WebSearchError``.

Typical usage (from ``ralph.config.mcp_models`` backend selection)::

    backend = SearxngBackend(url="http://localhost:8080")
    results = backend.search("Python type hints", limit=5)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin

import httpx

from ralph.timeout_defaults import WEBSEARCH_BACKEND_TIMEOUT_SECONDS

from .base import SearchResult, WebSearchError

_SEARCH_PATH = "/search"


@dataclass(frozen=True)
class SearxngBackend:
    """Backend that queries a user-managed SearXNG instance."""

    url: str
    timeout_seconds: float | None = None

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        request_data: dict[str, str] = {"q": query, "format": "json"}
        effective_timeout = (
            self.timeout_seconds
            if self.timeout_seconds is not None
            else WEBSEARCH_BACKEND_TIMEOUT_SECONDS
        )
        try:
            response = httpx.post(
                self._search_url,
                data=request_data,
                timeout=effective_timeout,
            )
            response.raise_for_status()
            payload = cast("object", response.json())
        except Exception as exc:
            raise WebSearchError("searxng search failed") from exc
        return self._normalize_results(payload, limit=limit)

    @property
    def _search_url(self) -> str:
        return urljoin(f"{self.url.rstrip('/')}/", _SEARCH_PATH.lstrip("/"))

    @staticmethod
    def _normalize_results(payload: object, *, limit: int) -> list[SearchResult]:
        if not isinstance(payload, Mapping):
            raise WebSearchError("searxng returned an invalid response")
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
            if len(results) >= limit:
                break
        return results


def _string_value(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = ["SearxngBackend"]
