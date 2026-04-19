from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_models import WebSearchConfig
from ralph.mcp.tool_coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    SessionLike,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tool_workspace import required_string_param
from ralph.mcp.websearch.backends.base import SearchResult, WebSearchBackend, WebSearchError
from ralph.mcp.websearch.backends.ddgs import DdgsBackend
from ralph.mcp.websearch.secrets import resolve_secret

if TYPE_CHECKING:
    from ralph.workspace import Workspace

WEB_SEARCH_CAPABILITY = "WebSearch"
_DEFAULT_LIMIT = 10
_MIN_LIMIT = 1
_MAX_LIMIT = 25


def _build_backend(name: str, config: WebSearchConfig) -> WebSearchBackend:
    if name == "ddgs":
        return DdgsBackend()
    if name == "searxng":
        spec = config.backends.get("searxng")
        url = spec.url if spec is not None else None
        if not url:
            raise WebSearchError("searxng backend requires url in config")
        from ralph.mcp.websearch.backends.searxng import SearxngBackend  # noqa: PLC0415

        return SearxngBackend(url=url)
    spec = config.backends.get(name)
    if spec is None:
        raise WebSearchError(f"backend {name!r} not configured")
    resolved_key = resolve_secret(spec.api_key, spec.api_key_env)
    if name == "tavily":
        from ralph.mcp.websearch.backends.tavily import TavilyBackend  # noqa: PLC0415

        return TavilyBackend(api_key=resolved_key)
    if name == "brave":
        from ralph.mcp.websearch.backends.brave import BraveBackend  # noqa: PLC0415

        return BraveBackend(api_key=resolved_key)
    if name == "exa":
        from ralph.mcp.websearch.backends.exa import ExaBackend  # noqa: PLC0415

        return ExaBackend(api_key=resolved_key)
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
        return max(_MIN_LIMIT, min(_MAX_LIMIT, value))
    return _DEFAULT_LIMIT


def _format_results(results: list[SearchResult]) -> str:
    if not results:
        return "(no results)"
    blocks = [f"Title: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}" for r in results]
    return "\n\n".join(blocks)


def handle_web_search(
    session: SessionLike,
    _workspace: Workspace,
    params: dict[str, object],
    *,
    web_search_config: WebSearchConfig | None = None,
) -> ToolResult:
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
            backend = _build_backend(name, config)
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


__all__ = ["WEB_SEARCH_CAPABILITY", "_build_backend", "handle_web_search"]
