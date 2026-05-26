"""Docs MCP server reachability probe with URL-shape-first validation."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

SUPPORTED_DOCS_MCP_PATHS: frozenset[str] = frozenset({"/mcp", "/sse"})


def is_supported_docs_mcp_url(url: str) -> bool:
    """Return True if the URL matches the supported docs-MCP endpoint shape."""
    parsed = urlparse(url)
    return (
        parsed.scheme == "http"
        and parsed.netloc == "localhost:6280"
        and parsed.path in SUPPORTED_DOCS_MCP_PATHS
    )


def probe_docs_mcp(url: str, *, timeout: float = 2.0) -> bool:
    """Return True if the URL is a reachable docs-MCP server returning HTTP 200."""
    if not is_supported_docs_mcp_url(url):
        return False
    try:
        response = httpx.get(url, timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


__all__ = ["SUPPORTED_DOCS_MCP_PATHS", "is_supported_docs_mcp_url", "probe_docs_mcp"]
