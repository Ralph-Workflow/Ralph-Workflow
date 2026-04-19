"""Concrete web-search backend implementations."""

from __future__ import annotations

from .base import SearchResult, WebSearchBackend, WebSearchError
from .brave import BraveBackend
from .ddgs import DdgsBackend
from .exa import ExaBackend
from .searxng import SearxngBackend
from .tavily import TavilyBackend

__all__ = [
    "BraveBackend",
    "DdgsBackend",
    "ExaBackend",
    "SearchResult",
    "SearxngBackend",
    "TavilyBackend",
    "WebSearchBackend",
    "WebSearchError",
]
