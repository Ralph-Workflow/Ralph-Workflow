"""Cursor Agent CLI transport helpers.

This module provides Cursor-specific MCP transport helpers.

Research-confirmed facts (Cursor Agent CLI ``agent``):

* Executable: ``agent`` (binary name on ``PATH``)
* Headless flag: ``--print`` with ``--output-format stream-json``
* Autonomy flag: ``--yolo`` (or ``--auto-review`` when configured)
* MCP config path: workspace ``.cursor/mcp.json`` AND user-global
  ``~/.cursor/mcp.json`` (Cursor may prefer one over the other
  depending on cwd; writing both ensures MCP is wired for any
  invocation pattern)
* HTTP JSON key: ``url`` (Cursor's documented MCP server shape)
* Output format: NDJSON ``stream-json`` (parsed by ``CursorParser``)

Cursor's MCP server configuration uses the standard MCP convention::

    {
        "mcpServers": {
            "ralph": {
                "url": "http://127.0.0.1:<port>/mcp"
            }
        }
    }

Ralph reads existing Cursor upstream servers from the workspace-local
``.cursor/mcp.json`` and the user-global ``~/.cursor/mcp.json`` files,
merges the run-scoped ``ralph`` entry through the existing upstream
merge flow (``merge_existing_upstreams``), and writes the merged
config to BOTH paths so the agent picks up MCP regardless of the cwd
it was launched from.

The write/restore protocol is the shared one in
``ralph/mcp/transport/config_overlay.py``: a process-local
``threading.Lock`` for sibling threads, a bounded cross-process advisory
lock for sibling PROCESSES, a durable ``mcp.json.ralph-backup`` record
written before the overwrite so a killed run self-heals on the next
invocation, and an atomic ``Path.replace`` that keeps every publication
torn-write-safe.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths, merge_existing_upstreams
from ralph.mcp.transport.config_overlay import (
    mcp_config_lock_path,
    mcp_config_overlay_lock,
    reclaim_config_overlay,
    restore_config_overlay,
    stage_config_overlay,
)
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# Process-local lock that serialises concurrent invocations of
# :func:`cursor_workspace_mcp_endpoint` so two sibling Cursor sessions IN
# THIS PROCESS cannot interleave their read/write/restore steps on the
# global MCP config file.  Cross-process contention is handled by the
# bounded advisory lock below; both layers are needed because the
# advisory lock serialises processes while the threading lock keeps the
# retry loop from racing two threads in this process against one
# lock-file handle.  See the context manager's docstring for the full
# concurrency contract.
_cursor_mcp_lock = threading.Lock()

#: Bounded acquisition budget for the cross-process advisory lock that
#: guards Cursor's user-global MCP config.  Read at call time so a test
#: can shrink the budget by assigning this module attribute.
_CURSOR_CONFIG_LOCK_TIMEOUT_SECONDS = 10.0


def _cursor_global_config_path() -> Path:
    """Return Cursor's global MCP config path.

    The documented Cursor MCP config surface is ``~/.cursor/mcp.json``.
    """
    return Path.home() / ".cursor" / "mcp.json"


def _cursor_workspace_config_path(workspace_path: Path) -> Path:
    """Return the workspace-local Cursor MCP config path.

    The documented workspace-local Cursor MCP config surface is
    ``.cursor/mcp.json`` (relative to the workspace root).
    """
    return workspace_path / ".cursor" / "mcp.json"


def cursor_mcp_config(endpoint: str) -> str:
    """Return the Cursor MCP JSON config string pointing to the given endpoint.

    Args:
        endpoint: The MCP server HTTP endpoint URL.

    Returns:
        JSON string with ``mcpServers`` containing the Ralph entry with
        the ``url`` key (Cursor's documented MCP server shape).
    """
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "url": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _cursor_paths_to_consider(
    workspace_path: Path | None,
) -> tuple[Path, ...]:
    """Return the list of Cursor MCP config paths to consider.

    Order: workspace-local ``.cursor/mcp.json`` first (when ``workspace_path``
    is provided), then the user-global ``~/.cursor/mcp.json`` (always).
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (_cursor_workspace_config_path(workspace_path),)
    return (
        *workspace_paths,
        _cursor_global_config_path(),
    )


@contextmanager
def cursor_workspace_mcp_endpoint(
    workspace_path: Path, endpoint: str, *, unsafe_mode: bool = False
) -> Iterator[None]:
    """Write a run-scoped Ralph MCP config to Cursor's paths and restore them on exit.

    Writes the merged config (Ralph entry + merged upstream servers in
    ``unsafe_mode``) to BOTH the workspace-local ``.cursor/mcp.json`` and
    the user-global ``~/.cursor/mcp.json`` so a Cursor invocation launched
    from inside or outside the workspace picks up the run-scoped Ralph MCP
    endpoint.  On exit the original bytes are restored on each path that
    was modified.

    Crash recovery: each overwrite is preceded by a durable
    ``<name>.ralph-backup`` record, and every transaction begins by
    reclaiming a record an earlier run was killed before it could apply
    (:func:`ralph.mcp.transport.config_overlay.reclaim_config_overlay`).
    An operator whose machine died mid-run gets their own MCP servers back
    on the next invocation instead of a permanently Ralph-only config file
    pointing at a dead port.  The reclaim runs BEFORE the snapshot, so this
    run also cannot mistake an abandoned overlay for the operator's config.

    Concurrency safety: callers are serialised by TWO layers -- a
    process-local :class:`threading.Lock` for sibling threads, and the
    bounded cross-process advisory lock in
    :mod:`ralph.mcp.transport.config_overlay` so two INDEPENDENT Ralph
    processes cannot interleave their snapshot/write/restore steps on the
    shared user-global file.  Both the original-bytes read and the restore
    happen INSIDE the critical section.  The advisory lock is bounded
    (``_CURSOR_CONFIG_LOCK_TIMEOUT_SECONDS``) and fails closed with
    :class:`~ralph.mcp.transport.config_overlay.McpConfigOverlayLockTimeoutError`
    rather than hanging the launch path.
    """
    config_paths = _cursor_paths_to_consider(workspace_path)
    lock_path = mcp_config_lock_path(_cursor_global_config_path())
    _cursor_mcp_lock.acquire()
    try:
        with mcp_config_overlay_lock(
            lock_path, timeout_seconds=_CURSOR_CONFIG_LOCK_TIMEOUT_SECONDS
        ):
            for config_path in config_paths:
                reclaim_config_overlay(config_path)

            current_config: dict[str, object] = {
                "mcpServers": {RALPH_MCP_SERVER_NAME: {"url": endpoint}},
                "workspace_path": workspace_path,
            }
            merged_config = merge_existing_upstreams(
                "cursor", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
            )
            config_payload = json.dumps(merged_config, indent=2).encode("utf-8")
            original_bytes_by_path = {
                config_path: stage_config_overlay(config_path, config_payload)
                for config_path in config_paths
            }
            try:
                yield
            finally:
                for config_path in config_paths:
                    restore_config_overlay(config_path, original_bytes_by_path[config_path])
    finally:
        _cursor_mcp_lock.release()


def _normalize_cursor_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    """Normalize a Cursor server entry to Ralph's expected format.

    Cursor's MCP server shape uses ``url`` for HTTP servers, which is
    the standard Ralph normalizer's expected key.  This helper is the
    identity mapping for cursor (kept as a normalizer hook for parity
    with the agy / claude / nanocoder helpers, and as the documented
    extension point if a future Cursor release uses a different key).

    Args:
        name: Server name.
        entry: Raw server entry dict from ``mcpServers``.

    Returns:
        Tuple of ``(name, normalized_entry)`` if valid, ``None`` if
        skipped.
    """
    if name == RALPH_MCP_SERVER_NAME:
        return None
    if not isinstance(entry, Mapping):
        return None
    return name, cast("dict[str, object]", entry)


def load_existing_cursor_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read Cursor's MCP config files and return any upstream MCP servers found.

    Args:
        workspace_path: Optional workspace path for the workspace-local
            ``.cursor/mcp.json``.

    Returns:
        Tuple of :class:`UpstreamMcpServer` objects found in Cursor
        config files.  The Ralph entry is filtered out so it does not
        collide with the run-scoped ``ralph`` injection.
    """
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _cursor_paths_to_consider(workspace_path),
            _normalize_cursor_server_entry,
        )
    )


__all__ = [
    "cursor_mcp_config",
    "cursor_workspace_mcp_endpoint",
    "load_existing_cursor_upstream_servers",
]
