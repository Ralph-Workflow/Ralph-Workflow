"""Workspace-scoped durable three-state catalog snapshot of upstream tool catalogs.

Representation: a snapshot is either *absent* (no authoritative record on
disk and no in-process cache entry) or *present* (a validated mapping of
server name -> tool list). Identity/authority lives on disk at
``<workspace>/.agent/mcp/parent_catalog.json``; the in-process cache is
only a fast path. Fail-closed: malformed or non-mapping disk payloads are
removed and reported as absent.
"""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    serialize_upstream_tool_catalog,
)
from ralph.mcp.upstream.models import UpstreamTool

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)

# wt-024 M5 (AC-02): bound the module-level cache so a session that
# touches many distinct workspace roots cannot grow the cache
# unboundedly. FIFO eviction mirrors ProcessManager._terminal_records
# (ralph/process/manager.py). 32 entries covers typical build/test
# concurrency without dropping recently-used workspaces.
_MAX_CACHE_ENTRIES: int = 32

# bounded-accumulator-ok: FIFO cap 32
# (OrderedDict.popitem(last=False) eviction in cache_tool_catalog)
_CACHE: OrderedDict[str, dict[str, list[UpstreamTool]]] = OrderedDict()

_CATALOG_DIR_MODE: int = 0o700
_CATALOG_FILE_MODE: int = 0o600


@dataclass(frozen=True)
class UpstreamToolCatalogSnapshot:
    """Presence-aware view of the upstream tool catalog for one workspace.

    ``present=False`` means "no authoritative record" (consumers must
    pop the env var). ``present=True`` with ``value={}`` is distinct and
    meaningful: the parent knows the catalog and it is empty, so the
    env var must be SET to a serialized empty mapping.
    """

    present: bool
    value: dict[str, list[UpstreamTool]] = field(default_factory=dict)


def _catalog_file(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "mcp" / "parent_catalog.json"


def _ensure_catalog_dir(workspace_root: Path) -> None:
    directory = _catalog_file(workspace_root).parent
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            directory.chmod(_CATALOG_DIR_MODE)
        except OSError:
            logger.debug("could not chmod catalog dir %s", directory)


def _write_catalog_file(workspace_root: Path, catalog: dict[str, list[UpstreamTool]]) -> None:
    """Atomically write the authoritative catalog file; remove it on failure."""
    target = _catalog_file(workspace_root)
    try:
        _ensure_catalog_dir(workspace_root)
        tmp = target.with_name(target.name + ".tmp")
        payload = serialize_upstream_tool_catalog(catalog)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _CATALOG_FILE_MODE)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        tmp.replace(target)
        if os.name == "posix":
            target.chmod(_CATALOG_FILE_MODE)
    except OSError:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove stale catalog file %s", target)
        raise


def _cache_key(workspace_root: Path | None) -> str | None:
    if workspace_root is None:
        return None
    return str(workspace_root.expanduser().resolve())


def cache_tool_catalog(
    workspace_root: Path,
    catalog: dict[str, list[UpstreamTool]],
) -> None:
    """Store a copy of the validated catalog in cache AND on disk atomically."""
    key = _cache_key(workspace_root)
    if key is None:
        return
    cached = {name: list(tools) for name, tools in catalog.items()}
    _write_catalog_file(workspace_root, cached)
    _CACHE[key] = cached
    _CACHE.move_to_end(key)
    while len(_CACHE) > _MAX_CACHE_ENTRIES:
        _CACHE.popitem(last=False)


def _decode_catalog_mapping(decoded: dict[str, object]) -> dict[str, list[UpstreamTool]]:
    """Decode a validated mapping payload into the typed catalog (drop bad rows)."""
    catalog: dict[str, list[UpstreamTool]] = {}
    for server_name, raw_tools in decoded.items():
        if not isinstance(server_name, str) or not isinstance(raw_tools, list):
            continue
        tools: list[UpstreamTool] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                continue
            name = raw_tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            description = raw_tool.get("description")
            schema = raw_tool.get("input_schema")
            tools.append(
                UpstreamTool(
                    name=name,
                    description=str(description) if description is not None else "",
                    input_schema=dict(schema) if isinstance(schema, dict) else {},
                )
            )
        if tools:
            catalog[server_name] = tools
    return catalog


def _read_disk_snapshot(workspace_root: Path) -> UpstreamToolCatalogSnapshot | None:
    """Read only the authoritative disk file. ``None`` means no file present."""
    path = _catalog_file(workspace_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("unreadable tool catalog file %s; treating as absent", path)
        return UpstreamToolCatalogSnapshot(present=False)
    decoded: object
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, dict):
        # Fail closed: malformed payload is removed and reported absent.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove malformed catalog file %s", path)
        return UpstreamToolCatalogSnapshot(present=False)
    return UpstreamToolCatalogSnapshot(present=True, value=_decode_catalog_mapping(decoded))


def get_tool_catalog_snapshot(workspace_root: Path | None) -> UpstreamToolCatalogSnapshot:
    """Canonical reader: disk is authoritative, the in-process cache is a fast path."""
    if workspace_root is None:
        return UpstreamToolCatalogSnapshot(present=False)
    disk = _read_disk_snapshot(workspace_root)
    if disk is not None:
        if disk.present:
            key = _cache_key(workspace_root)
            if key is not None:
                _CACHE[key] = disk.value
                _CACHE.move_to_end(key)
                while len(_CACHE) > _MAX_CACHE_ENTRIES:
                    _CACHE.popitem(last=False)
        return disk
    # No file: fall back to the in-process cache fast path.
    key = _cache_key(workspace_root)
    if key is not None and key in _CACHE:
        cached = {name: list(tools) for name, tools in _CACHE[key].items()}
        return UpstreamToolCatalogSnapshot(present=True, value=cached)
    return UpstreamToolCatalogSnapshot(present=False)


def read_snapshot_from_disk(workspace_root: Path) -> UpstreamToolCatalogSnapshot:
    """Read the authoritative disk snapshot only (no cache fallback)."""
    disk = _read_disk_snapshot(workspace_root)
    if disk is None:
        return UpstreamToolCatalogSnapshot(present=False)
    return disk


def publish_tool_catalog_to_parent(
    workspace_root: Path,
    catalog: dict[str, list[UpstreamTool]],
) -> None:
    """Write the authoritative disk catalog for the parent to read; disk-only."""
    _write_catalog_file(workspace_root, {name: list(tools) for name, tools in catalog.items()})


def get_tool_catalog(workspace_root: Path | None) -> dict[str, list[UpstreamTool]]:
    """Thin wrapper over the canonical snapshot reader returning its value."""
    return get_tool_catalog_snapshot(workspace_root).value


def clear_tool_catalog(workspace_root: Path | None) -> None:
    """Drop the cached catalog AND the authoritative disk file for one workspace."""
    key = _cache_key(workspace_root)
    if key is None:
        return
    _CACHE.pop(key, None)
    assert workspace_root is not None
    try:
        _catalog_file(workspace_root).unlink(missing_ok=True)
    except OSError:
        logger.debug("could not remove catalog file for %s", workspace_root)


def collect_tool_catalog(
    servers: Iterable[UpstreamMcpServer],
) -> dict[str, list[UpstreamTool]]:
    """Probe configured upstream servers and return their advertised tool catalogs."""
    return {server.name: list(make_upstream_client(server).list_tools()) for server in servers}


def apply_tool_catalog_env(
    runtime_env: dict[str, str],
    snapshot: UpstreamToolCatalogSnapshot,
) -> None:
    """Presence-aware env materialization: present -> SET (even if empty), absent -> POP."""
    if snapshot.present:
        runtime_env[UPSTREAM_MCP_TOOL_CATALOG_ENV] = serialize_upstream_tool_catalog(snapshot.value)
        return
    runtime_env.pop(UPSTREAM_MCP_TOOL_CATALOG_ENV, None)


__all__ = [
    "UpstreamToolCatalogSnapshot",
    "apply_tool_catalog_env",
    "cache_tool_catalog",
    "clear_tool_catalog",
    "collect_tool_catalog",
    "get_tool_catalog",
    "get_tool_catalog_snapshot",
    "publish_tool_catalog_to_parent",
    "read_snapshot_from_disk",
]
