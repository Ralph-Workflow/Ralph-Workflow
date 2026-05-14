"""Web-search backends and helpers for the MCP web_search tool."""

from __future__ import annotations

from .backends import DdgsBackend, SearchResult, WebSearchBackend, WebSearchError
from .secrets import resolve_secret

__all__ = [
    "DdgsBackend",
    "SearchResult",
    "WebSearchBackend",
    "WebSearchError",
    "resolve_secret",
]
