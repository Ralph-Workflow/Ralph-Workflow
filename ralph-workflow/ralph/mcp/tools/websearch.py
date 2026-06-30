"""MCP tool handler for web search across pluggable backends.

Exposes ``handle_web_search``, which dispatches a search query through the
configured backend (and optional fallbacks) and returns a ``ToolResult``.
Backends are loaded lazily; the dispatch order is taken from ``WebSearchConfig``.

Exported surface:

- ``handle_web_search`` — the public MCP tool handler. Requires the
  ``WebSearch`` capability on the session, parses a string ``query``
  (and optional bounded ``limit`` clamped to ``[MIN_LIMIT, MAX_LIMIT]``
  = ``[1, 25]``), and dispatches through the configured backend order
  (default backend followed by the configured fallbacks).
- ``build_backend`` / ``_build_backend`` — the public / private factory
  that returns a ``WebSearchBackend`` instance for a given backend name
  and config. The factory always uses a resolved ``timeout_seconds``
  (per-backend override falls back to the global default).
- ``WEB_SEARCH_CAPABILITY`` / ``MIN_LIMIT`` / ``MAX_LIMIT`` — the
  capability string and the request-size bounds.

Trust boundary: every handler is gated on the ``WebSearch``
``McpCapability``. The backend is selected from a closed allowlist
(``ddgs``, ``searxng``, ``tavily``, ``brave``, ``exa``); an unsupported
backend name or a missing configuration returns ``WebSearchError``.

Side effects (network contract): every backend implementation uses an
injected ``timeout_seconds`` on the network call, so a misbehaving
upstream cannot hang the MCP server thread. The dispatch loop falls
back through the configured backend order and only returns an
``is_error`` result after every backend has failed. ``loguru`` warnings
are emitted on every backend failure so an operator can correlate
upstream outages with retries.
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
    """Dispatch a web search query through the configured backend and return results.

    Args:
        session: Agent session; must declare ``WebSearch``.
        _workspace: Unused; kept for tool-handler signature parity.
        params: Mapping with required ``query`` (string) and optional
            ``limit`` (int, clamped to ``[MIN_LIMIT, MAX_LIMIT] = [1, 25]``).
        web_search_config: Optional injected ``WebSearchConfig`` for the
            dispatch order and per-backend overrides. Defaults to
            ``WebSearchConfig()``.

    Returns:
        A ``ToolResult`` whose text content is the formatted backend
        result list (``Title / URL / Snippet`` blocks joined by blank
        lines). Falls back through the configured backend order and
        only returns ``is_error=True`` after every backend has failed.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``WebSearch``. The handler enforces default-deny.
        InvalidParamsError: When ``params`` is missing ``query``.

    Side effects (network contract):
        Every backend implementation uses an injected ``timeout_seconds``
        on the network call so a misbehaving upstream cannot hang the
        MCP server thread. ``loguru`` warnings are emitted on every
        backend failure so an operator can correlate upstream outages
        with retries. No workspace writes.
    """
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
