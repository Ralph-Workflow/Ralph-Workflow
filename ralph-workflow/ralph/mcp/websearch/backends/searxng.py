"""SearXNG web-search backend."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin

import httpx

from .base import SearchResult, WebSearchError

_SEARCH_PATH = "/search"
_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SearxngBackend:
    """Backend that queries a user-managed SearXNG instance."""

    url: str

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        request_data: dict[str, str] = {"q": query, "format": "json"}
        try:
            response = httpx.post(
                self._search_url,
                data=request_data,
                timeout=_TIMEOUT_SECONDS,
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
