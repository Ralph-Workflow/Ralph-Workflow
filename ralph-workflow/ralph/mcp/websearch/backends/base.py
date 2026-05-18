"""Shared web-search backend abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    class WebSearchBackend(Protocol):
        """Protocol implemented by concrete web-search backends."""

        def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...


class WebSearchError(RuntimeError):
    """Raised when a web-search backend fails."""

    @dataclass(frozen=True)
    class SearchResult:
        """Normalized search result shape shared by all backends."""

        title: str
        url: str
        snippet: str


SearchResult = WebSearchError.SearchResult


__all__ = ["SearchResult", "WebSearchBackend", "WebSearchError"]
