"""Shared web-search backend abstractions."""

from __future__ import annotations

from typing import Protocol

from ralph.mcp.websearch.backends._search_result import SearchResult
from ralph.mcp.websearch.backends._web_search_error import WebSearchError


class WebSearchBackend(Protocol):
    """Protocol implemented by concrete web-search backends."""

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...


__all__ = ["SearchResult", "WebSearchBackend", "WebSearchError"]
