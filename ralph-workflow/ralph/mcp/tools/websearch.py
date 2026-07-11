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

import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_models import WebSearchConfig
from ralph.mcp.tools._envelope_bytes import finalize_envelope_bytes_out
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


# Phase 4: snippet truncation cap for ``format='summary'``. The raw
# snippet returned by the backend can be very long; the summary card
# keeps the top of the snippet plus a ``...`` marker so the agent
# knows the snippet was elided. The cap is conservative so callers
# see a measurable byte-savings win.
SUMMARY_SNIPPET_MAX_CHARS = 240


def _format_summary_envelope(
    results: list[SearchResult],
    *,
    backend_chain_used: list[str],
    query: str,
) -> str:
    """Build the ``format='summary'`` JSON envelope for ``handle_web_search``.

    The envelope mirrors the Phase-4 byte-savings contract used by
    ``git_log``/``git_show``/``exec``: one compact card per result
    plus ``bytes_in``/``bytes_out``/``backend_chain_used`` counters so
    callers can verify the savings against the legacy shape. The
    snippet is truncated to ``SUMMARY_SNIPPET_MAX_CHARS`` with a
    ``...`` suffix so the agent can see the snippet was elided.
    """
    cards: list[dict[str, object]] = []
    for r in results:
        snippet = r.snippet or ""
        if len(snippet) > SUMMARY_SNIPPET_MAX_CHARS:
            snippet = snippet[:SUMMARY_SNIPPET_MAX_CHARS] + "..."
        # ponytail: ``snippet_budget_bytes`` records the actual
        # UTF-8 bytes the agent would receive when this snippet is
        # sent through JSON; counting characters would undercount
        # multi-byte code points (emoji, CJK) by 2-4x and break
        # byte-budget planning.
        cards.append(
            {
                "title": r.title,
                "url": r.url,
                "snippet": snippet,
                "snippet_budget_bytes": len(snippet.encode("utf-8")),
            }
        )
    envelope = finalize_envelope_bytes_out(
        {
            "format": "summary",
            "query_length": len(query),
            "result_count": len(cards),
            "results": cards,
            "backend_chain_used": list(backend_chain_used),
            "bytes_in": sum(
                len((r.title or "").encode("utf-8"))
                + len((r.url or "").encode("utf-8"))
                + len((r.snippet or "").encode("utf-8"))
                for r in results
            ),
        }
    )
    return json.dumps(envelope, separators=(",", ":"))


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
        params: Mapping with required ``query`` (string), optional
            ``limit`` (int, clamped to ``[MIN_LIMIT, MAX_LIMIT] = [1, 25]``),
            and optional ``format`` (``'raw'|'summary'``, default ``'raw'``).
        web_search_config: Optional injected ``WebSearchConfig`` for the
            dispatch order and per-backend overrides. Defaults to
            ``WebSearchConfig()``.

    Returns:
        A ``ToolResult`` whose text content is the formatted backend
        result list (``Title / URL / Snippet`` blocks joined by blank
        lines) when ``format='raw'`` (default), or a compact JSON
        envelope with bounded snippets, ``bytes_in``/``bytes_out``
        size counters, and the ``backend_chain_used`` list when
        ``format='summary'``. Falls back through the configured
        backend order and only returns ``is_error=True`` after every
        backend has failed.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``WebSearch``. The handler enforces default-deny.
        InvalidParamsError: When ``params`` is missing ``query`` or
            carries an unknown ``format`` value.

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

    format_value = params.get("format", "raw") if isinstance(params, dict) else "raw"
    if format_value not in ("raw", "summary"):
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Invalid web_search format: {format_value!r}; "
                    "expected 'raw' or 'summary'"
                )
            ],
            is_error=True,
        )

    limit = _clamp_limit(params.get("limit", _DEFAULT_LIMIT))
    dispatch_order = _deduplicated([config.backend, *config.fallback])
    used_backends: list[str] = []

    for name in dispatch_order:
        try:
            backend = build_backend(name, config)
            results = backend.search(query, limit=limit)
            used_backends.append(name)
            if format_value == "summary":
                return ToolResult(
                    content=[
                        ToolContent.text_content(
                            _format_summary_envelope(
                                results,
                                backend_chain_used=used_backends,
                                query=query,
                            )
                        )
                    ],
                    is_error=False,
                )
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
