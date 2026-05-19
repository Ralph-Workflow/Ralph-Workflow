"""SearchResult — normalized search result shape shared by all backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """Normalized search result shape shared by all backends."""

    title: str
    url: str
    snippet: str


__all__ = ["SearchResult"]
