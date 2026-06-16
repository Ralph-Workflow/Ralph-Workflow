"""MCP tool handler for web search across pluggable backends.

Exposes ``handle_web_search``, which dispatches a search query through the
configured backend (and optional fallbacks) and returns a ``ToolResult``.
Backends are loaded lazily; the dispatch order is taken from ``WebSearchConfig``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_models import WebSearchConfig
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace import required_string_param
from ralph.mcp.websearch.backends.base import SearchResult, WebSearchBackend, WebSearchError
from ralph.mcp.websearch.backends.brave import BraveBackend
from ralph.mcp.websearch.backends.ddgs import DdgsBackend
from ralph.mcp.websearch.backends.exa import ExaBackend
from ralph.mcp.websearch.backends.searxng import SearxngBackend
from ralph.mcp.websearch.backends.tavily import TavilyBackend
from ralph.mcp.websearch.secrets import resolve_secret

if TYPE_CHECKING:
    from ralph.config._web_search_backend_spec import WebSearchBackendSpec
    from ralph.workspace import Workspace

WEB_SEARCH_CAPABILITY = "WebSearch"
_DEFAULT_LIMIT = 10
MIN_LIMIT = 1
MAX_LIMIT = 25


def _build_backend(name: str, config: WebSearchConfig) -> WebSearchBackend:
    default_timeout = config.web_search_default_timeout_seconds

    def _resolved_timeout(spec: WebSearchBackendSpec | None) -> float | None:
        if spec is None:
            return default_timeout
        per_backend = spec.timeout_seconds
        if per_backend is not None:
            return per_backend
        return default_timeout

    if name == "ddgs":
        return DdgsBackend(timeout_seconds=_resolved_timeout(config.backends.get("ddgs")))
    if name == "searxng":
        spec = config.backends.get("searxng")
        url = spec.url if spec is not None else None
        if not url:
            raise WebSearchError("searxng backend requires url in config")
        return SearxngBackend(url=url, timeout_seconds=_resolved_timeout(spec))
    spec = config.backends.get(name)
    if spec is None:
        raise WebSearchError(f"backend {name!r} not configured")
    resolved_key = resolve_secret(spec.api_key, spec.api_key_env)
    if name == "tavily":
        return TavilyBackend(api_key=resolved_key, timeout_seconds=_resolved_timeout(spec))
    if name == "brave":
        return BraveBackend(api_key=resolved_key, timeout_seconds=_resolved_timeout(spec))
    if name == "exa":
        return ExaBackend(api_key=resolved_key, timeout_seconds=_resolved_timeout(spec))
    raise WebSearchError(f"unsupported backend: {name!r}")


def _deduplicated(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _clamp_limit(value: object) -> int:
    if isinstance(value, int):
        return max(MIN_LIMIT, min(MAX_LIMIT, value))
    return _DEFAULT_LIMIT


def _format_results(results: list[SearchResult]) -> str:
    if not results:
        return "(no results)"
    blocks = [f"Title: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}" for r in results]
    return "\n\n".join(blocks)


def handle_web_search(
    session: CoordinationSessionLike,
    _workspace: Workspace,
    params: dict[str, object],
    *,
    web_search_config: WebSearchConfig | None = None,
) -> ToolResult:
    """Dispatch a web search query through the configured backend and return results."""
    config = web_search_config if web_search_config is not None else WebSearchConfig()
    try:
        require_capability(session, WEB_SEARCH_CAPABILITY, "Web search")
        query = required_string_param(params, "query")
    except (CapabilityDeniedError, InvalidParamsError) as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

    limit = _clamp_limit(params.get("limit", _DEFAULT_LIMIT))
    dispatch_order = _deduplicated([config.backend, *config.fallback])

    for name in dispatch_order:
        try:
            backend = build_backend(name, config)
            results = backend.search(query, limit=limit)
            return ToolResult(
                content=[ToolContent.text_content(_format_results(results))],
                is_error=False,
            )
        except WebSearchError as exc:
            logger.warning("web_search backend {b} failed: {e}; trying next", b=name, e=exc)

    return ToolResult(
        content=[ToolContent.text_content("all web_search backends failed")],
        is_error=True,
    )


build_backend = _build_backend

__all__ = ["MAX_LIMIT", "MIN_LIMIT", "WEB_SEARCH_CAPABILITY", "handle_web_search"]
