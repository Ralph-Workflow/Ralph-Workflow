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
``.cursor/mcp.json`` and the user-global ``~/.cursor/mcp.json`` files.
Each invocation publishes its merged run-scoped configuration under a
private ``HOME`` selected by the runtime resolver, so concurrent sessions
never mutate the workspace or operator configuration.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from loguru import logger

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers


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


#: Largest ``~/.cursor`` entry Ralph will copy when it cannot symlink it.
#:
#: The normal symlink path keeps operator credentials and settings available
#: without materializing per-invocation copies. The bounded fallback covers
#: filesystems that reject symlinks without copying Cursor's potentially large
#: session history into every isolated home.
_MIRROR_COPY_BYTE_BUDGET: Final[int] = 64 * 1024 * 1024


def _exceeds_copy_budget(entry: Path, budget: int) -> bool:
    """Return whether an entry exceeds the bounded copy fallback budget."""
    if entry.is_file():
        return entry.stat().st_size > budget
    total = 0
    for child in entry.rglob("*"):
        if not child.is_file() or child.is_symlink():
            continue
        total += child.stat().st_size
        if total > budget:
            return True
    return False


def _mirror_cursor_home(source_cursor_root: Path, private_cursor_root: Path) -> None:
    """Mirror operator Cursor state without replacing Ralph's generated config."""
    if not source_cursor_root.exists():
        return
    for entry in source_cursor_root.iterdir():
        if entry.name == "mcp.json" or entry.name.endswith(".ralph.lock"):
            continue
        destination = private_cursor_root / entry.name
        try:
            target_is_directory = entry.is_dir()
            destination.symlink_to(entry, target_is_directory=target_is_directory)
        except FileNotFoundError:
            continue
        except OSError:
            try:
                _copy_cursor_home_entry(entry, destination)
            except FileNotFoundError:
                continue


def _copy_cursor_home_entry(entry: Path, destination: Path) -> None:
    """Copy one un-symlinkable Cursor-home entry within the bounded budget."""
    if _exceeds_copy_budget(entry, _MIRROR_COPY_BYTE_BUDGET):
        logger.warning(
            "Cursor home entry {} could not be symlinked and is larger than {} bytes; "
            "leaving it out of this run's private HOME rather than copying it per invocation.",
            entry,
            _MIRROR_COPY_BYTE_BUDGET,
        )
        return
    if entry.is_dir():
        # filesystem-write-ok: fallback materialization of isolated temporary Cursor-home input
        shutil.copytree(entry, destination, dirs_exist_ok=True)
    else:
        # filesystem-write-ok: fallback materialization of isolated temporary Cursor-home input
        shutil.copy2(entry, destination)


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
    "_mirror_cursor_home",
    "cursor_mcp_config",
    "load_existing_cursor_upstream_servers",
]
