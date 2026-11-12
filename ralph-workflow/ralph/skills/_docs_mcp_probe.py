from __future__ import annotations

from urllib.parse import urlparse

import httpx

SUPPORTED_DOCS_MCP_PATHS: frozenset[str] = frozenset({"/mcp", "/sse"})
_HTTP_OK = 200


def is_supported_docs_mcp_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "http"
        and parsed.netloc == "localhost:6280"
        and parsed.path in SUPPORTED_DOCS_MCP_PATHS
    )


def probe_docs_mcp(url: str, *, timeout: float = 2.0) -> bool:
    if not is_supported_docs_mcp_url(url):
        return False
    try:
        response = httpx.get(url, timeout=timeout)
        return response.status_code == _HTTP_OK
    except Exception:
        return False


__all__ = ["SUPPORTED_DOCS_MCP_PATHS", "is_supported_docs_mcp_url", "probe_docs_mcp"]
