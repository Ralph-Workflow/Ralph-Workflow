"""DuckDuckGo Search backend implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.websearch._bounded_sdk_call import default_sdk_timeout_seconds, with_timeout

from .base import SearchResult, WebSearchError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from typing import Protocol

    class _DdgsTextClient(Protocol):
        """Minimal structural protocol matching the DDGS text-search API."""

        def text(self, query: str, max_results: int) -> Iterable[Mapping[str, object]] | None: ...


_ddgs_module: object | None
try:  # pragma: no cover - exercised via monkeypatch in tests
    import ddgs as _loaded_ddgs_module
except ImportError:  # pragma: no cover - depends on optional runtime dependency
    _ddgs_module = None
else:
    _ddgs_module = _loaded_ddgs_module

DDGS = cast("Callable[[], _DdgsTextClient] | None", getattr(_ddgs_module, "DDGS", None))


class DdgsBackend:
    """In-process default backend backed by the ``ddgs`` package."""

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float | None:
        """Return the per-call timeout, or None to inherit the central default."""
        return self._timeout_seconds

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        client = self._create_client()
        effective_timeout = (
            self._timeout_seconds
            if self._timeout_seconds is not None
            else default_sdk_timeout_seconds()
        )
        try:
            raw_results = with_timeout(
                lambda: client.text(query, max_results=limit),
                effective_timeout,
                label="ddgs",
            )
        except WebSearchError:
            raise
        except Exception as exc:
            raise WebSearchError("ddgs search failed") from exc
        return self._normalize_results(raw_results)

    def _create_client(self) -> _DdgsTextClient:
        if DDGS is None:
            raise WebSearchError("ddgs backend is unavailable")
        return DDGS()

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
