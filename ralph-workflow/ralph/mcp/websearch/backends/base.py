"""Shared web-search backend abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    """Normalized search result shape shared by all backends."""

    title: str
    url: str
    snippet: str


class WebSearchError(RuntimeError):
    """Raised when a web-search backend fails."""


class WebSearchBackend(Protocol):
    """Protocol implemented by concrete web-search backends."""

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...


__all__ = ["SearchResult", "WebSearchBackend", "WebSearchError"]
