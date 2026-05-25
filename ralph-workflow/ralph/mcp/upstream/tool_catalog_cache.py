"""Workspace-scoped cache of validated upstream tool catalogs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    serialize_upstream_tool_catalog,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ralph.mcp.upstream.models import UpstreamTool

_CACHE: dict[str, dict[str, list[UpstreamTool]]] = {}


def _cache_key(workspace_root: Path | None) -> str | None:
    if workspace_root is None:
        return None
    return str(workspace_root.expanduser().resolve())


def cache_tool_catalog(
    workspace_root: Path | None,
    catalog: dict[str, list[UpstreamTool]],
) -> None:
    key = _cache_key(workspace_root)
    if key is None:
        return
    _CACHE[key] = {name: list(tools) for name, tools in catalog.items()}


def get_tool_catalog(workspace_root: Path | None) -> dict[str, list[UpstreamTool]]:
    key = _cache_key(workspace_root)
    if key is None:
        return {}
    return {name: list(tools) for name, tools in _CACHE.get(key, {}).items()}


def clear_tool_catalog(workspace_root: Path | None) -> None:
    key = _cache_key(workspace_root)
    if key is None:
        return
    _CACHE.pop(key, None)


def collect_tool_catalog(
    servers: Iterable[UpstreamMcpServer],
) -> dict[str, list[UpstreamTool]]:
    return {server.name: list(make_upstream_client(server).list_tools()) for server in servers}


def apply_tool_catalog_env(
    runtime_env: dict[str, str],
    catalog: dict[str, list[UpstreamTool]],
) -> None:
    if catalog:
        runtime_env[UPSTREAM_MCP_TOOL_CATALOG_ENV] = serialize_upstream_tool_catalog(catalog)
        return
    runtime_env.pop(UPSTREAM_MCP_TOOL_CATALOG_ENV, None)


__all__ = [
    "apply_tool_catalog_env",
    "cache_tool_catalog",
    "clear_tool_catalog",
    "collect_tool_catalog",
    "get_tool_catalog",
]
