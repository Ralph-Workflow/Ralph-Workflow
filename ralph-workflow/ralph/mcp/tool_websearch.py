"""Tool websearch handlers - compatibility wrappers over the sub-package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools import websearch as _impl
from ralph.mcp.tools.websearch import (
    _MAX_LIMIT,
    _MIN_LIMIT,
    WEB_SEARCH_CAPABILITY,
    _build_backend,
)

if TYPE_CHECKING:
    from ralph.config.mcp_models import WebSearchConfig
    from ralph.mcp.tools.coordination import SessionLike, ToolResult
    from ralph.workspace import Workspace


def handle_web_search(
    session: SessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    web_search_config: WebSearchConfig | None = None,
) -> ToolResult:
    _impl._build_backend = _build_backend
    return _impl.handle_web_search(
        session,
        workspace,
        params,
        web_search_config=web_search_config,
    )


__all__ = [
    "WEB_SEARCH_CAPABILITY",
    "_MAX_LIMIT",
    "_MIN_LIMIT",
    "_build_backend",
    "handle_web_search",
]
