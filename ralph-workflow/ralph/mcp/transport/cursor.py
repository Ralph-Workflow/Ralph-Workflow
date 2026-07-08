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

The write/restore protocol mirrors the AGY pattern in
``ralph/mcp/transport/agy.py``: a process-local ``threading.Lock``
serialises concurrent cursors, an atomic ``Path.replace`` keeps the
write torn-write-safe, and the original-bytes restore happens INSIDE
the critical section so a parallel sibling cannot interleave its own
write/restore between our read and our restore.
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
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# Process-local lock that serialises concurrent invocations of
# :func:`cursor_workspace_mcp_endpoint` so two sibling Cursor sessions
# cannot interleave their read/write/restore steps on the global MCP
# config file.  See the context manager's docstring for the full
# concurrency contract.
_cursor_mcp_lock = threading.Lock()


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

    Concurrency safety: this context manager serialises concurrent callers
    with a single :class:`threading.Lock` (process-local) and writes the
    merged config atomically (via ``Path.replace``) so a parallel Cursor
    session cannot observe a torn write or clobber a sibling session's
    restore step.  The original-bytes read happens INSIDE the critical
    section so a parallel sibling cannot interleave its own write/restore
    between our read and our restore.
    """
    _cursor_mcp_lock.acquire()
    try:
        # Snapshot the original bytes BEFORE we write so the restore step
        # can put each path back exactly as we found it (including the
        # missing-file case for paths that did not exist).
        original_by_path: dict[Path, bytes | None] = {}
        for config_path in _cursor_paths_to_consider(workspace_path):
            original_by_path[config_path] = (
                config_path.read_bytes() if config_path.is_file() else None
            )

        current_config: dict[str, object] = {
            "mcpServers": {RALPH_MCP_SERVER_NAME: {"url": endpoint}},
            "workspace_path": workspace_path,
        }
        merged_config = merge_existing_upstreams(
            "cursor", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
        )

        try:
            for config_path in _cursor_paths_to_consider(workspace_path):
                config_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic write: stage to a sibling temp file then
                # ``Path.replace`` so a concurrent reader (Cursor's Go
                # runtime) never sees a half-written config.  The temp
                # file is unique per call to avoid a TOCTOU between two
                # siblings staging the same name.
                _stage_path = config_path.with_suffix(
                    config_path.suffix + f".ralph-staging.{id(merged_config)}"
                )
                _stage_path.write_text(json.dumps(merged_config, indent=2), encoding="utf-8")
                _stage_path.replace(config_path)
            yield
        finally:
            for config_path in _cursor_paths_to_consider(workspace_path):
                original_bytes = original_by_path.get(config_path)
                if original_bytes is None:
                    if config_path.is_file():
                        config_path.unlink()
                else:
                    # Atomic restore via the same staging pattern so a
                    # concurrent sibling that staged its own write
                    # between our yield and our restore does not lose
                    # its bytes.
                    _restore_path = config_path.with_suffix(
                        config_path.suffix + f".ralph-restore.{id(original_bytes)}"
                    )
                    _restore_path.write_bytes(original_bytes)
                    _restore_path.replace(config_path)
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
