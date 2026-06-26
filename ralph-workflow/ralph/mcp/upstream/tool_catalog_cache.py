"""Workspace-scoped cache of validated upstream tool catalogs."""

from __future__ import annotations

from collections import OrderedDict
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

# wt-024 M5 (AC-02): bound the module-level cache so a session that
# touches many distinct workspace roots cannot grow the cache
# unboundedly. FIFO eviction mirrors ProcessManager._terminal_records
# (ralph/process/manager.py). 32 entries covers typical build/test
# concurrency without dropping recently-used workspaces.
_MAX_CACHE_ENTRIES: int = 32

# bounded-accumulator-ok: FIFO LRU cap _MAX_CACHE_ENTRIES=32
# (OrderedDict.popitem(last=False) eviction in cache_tool_catalog)
_CACHE: OrderedDict[str, dict[str, list[UpstreamTool]]] = OrderedDict()  # bounded-accumulator-ok


def _cache_key(workspace_root: Path | None) -> str | None:
    if workspace_root is None:
        return None
    return str(workspace_root.expanduser().resolve())


def cache_tool_catalog(
    workspace_root: Path | None,
    catalog: dict[str, list[UpstreamTool]],
) -> None:
    """Store a copy of the validated upstream tool catalog for one workspace."""
    key = _cache_key(workspace_root)
    if key is None:
        return
    _CACHE[key] = {name: list(tools) for name, tools in catalog.items()}
    _CACHE.move_to_end(key)
    while len(_CACHE) > _MAX_CACHE_ENTRIES:
        _CACHE.popitem(last=False)


def get_tool_catalog(workspace_root: Path | None) -> dict[str, list[UpstreamTool]]:
    """Return a defensive copy of the cached tool catalog for one workspace."""
    key = _cache_key(workspace_root)
    if key is None:
        return {}
    return {name: list(tools) for name, tools in _CACHE.get(key, {}).items()}


def clear_tool_catalog(workspace_root: Path | None) -> None:
    """Drop any cached upstream tool catalog associated with one workspace."""
    key = _cache_key(workspace_root)
    if key is None:
        return
    _CACHE.pop(key, None)


def collect_tool_catalog(
    servers: Iterable[UpstreamMcpServer],
) -> dict[str, list[UpstreamTool]]:
    """Probe configured upstream servers and return their advertised tool catalogs."""
    return {server.name: list(make_upstream_client(server).list_tools()) for server in servers}


def apply_tool_catalog_env(
    runtime_env: dict[str, str],
    catalog: dict[str, list[UpstreamTool]],
) -> None:
    """Materialize the upstream tool catalog into runtime environment variables."""
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
