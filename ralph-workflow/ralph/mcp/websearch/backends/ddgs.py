"""DuckDuckGo Search backend implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .base import SearchResult, WebSearchError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class _DdgsTextClient(Protocol):
    """Minimal structural protocol matching the DDGS text-search API."""

    def text(
        self, query: str, max_results: int
    ) -> Iterable[Mapping[str, object]] | None: ...


try:  # pragma: no cover - exercised via monkeypatch in tests
    from ddgs import DDGS
except ImportError:  # pragma: no cover - depends on optional runtime dependency
    DDGS = None


class DdgsBackend:
    """In-process default backend backed by the ``ddgs`` package."""

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        client = self._create_client()
        try:
            raw_results = client.text(query, max_results=limit)
        except Exception as exc:
            raise WebSearchError("ddgs search failed") from exc
        return self._normalize_results(raw_results)

    def _create_client(self) -> _DdgsTextClient:
        if DDGS is None:  # type: ignore[misc]
            raise WebSearchError("ddgs backend is unavailable")
        return DDGS()  # type: ignore[misc,no-any-return]

    @staticmethod
    def _normalize_results(
        raw_results: Iterable[Mapping[str, object]] | None,
    ) -> list[SearchResult]:
        if raw_results is None:
            return []
        results: list[SearchResult] = []
        for item in raw_results:
            title = _string_value(item, "title")
            url = _string_value(item, "url", "href")
            snippet = _string_value(item, "snippet", "body", "description")
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


__all__ = ["DdgsBackend"]
