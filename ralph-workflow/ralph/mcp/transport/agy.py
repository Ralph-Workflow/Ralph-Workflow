"""Google Anti Gravity (AGY) transport helpers.

This module provides AGY-specific MCP transport helpers.

Research-confirmed facts:
- Executable: agy
- Print flag: --print
- Yolo flag: --dangerously-skip-permissions
- MCP config paths: ``~/.gemini/antigravity-cli/mcp_config.json`` (legacy/capability-listing path) AND ``~/.gemini/config/mcp_config.json`` (the path AGY's own bundled "agy-customizations" skill documents as "Global Configuration", and the one live dispatch actually reads -- see below).
- HTTP JSON key: serverUrl
- Print output: stream-json emits NDJSON; AgyParser is selected by transport.

Plan (Evidence Provenance, S-2), measured live against agy v1.1.10
(gemini-3.6-flash-low, --print --output-format stream-json), never guessed:
writing the Ralph entry to *only* ~/.gemini/antigravity-cli/mcp_config.json
makes AGY list the generic ``call_mcp_tool`` dispatcher in the ``init``
frame's ``tools`` array, but a live run instructed to use it explicitly
reported the dispatcher "not present in the current toolset" and never
opened a connection to Ralph's MCP server (zero request lines in
``mcp-server.log``, no wire-ledger record). Writing the identical merged
payload to ~/.gemini/config/mcp_config.json as well -- the path AGY's
bundled ``builtin/skills/agy-customizations/docs/mcp_servers.md`` documents
as the actual global MCP config -- made the same prompt produce a genuine
``call_mcp_tool`` invocation that reached Ralph's server and returned a real
tool result. Both paths are therefore kept in sync by this module: the first
for whatever legacy consumer still reads it, the second because it is the
one that makes live dispatch work.

Ralph reads existing AGY upstream servers from the user config files at
both global paths above and workspace .agents/mcp_config.json. The
agy_mcp_config() helper builds the AGY-native JSON payload for Ralph's MCP
endpoint using AGY's serverUrl field.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# AGY home config directory name within its default config root
_AGY_HOME_SUBDIR = "antigravity-cli"

def _agy_global_config_path() -> Path:
    """Return AGY's legacy global MCP config path.

    Measured behaviour: AGY's --print mode in a PTY only initialises its MCP
    client when this global config file exists; the workspace-level
    ``.agents/mcp_config.json`` file is not sufficient. The helper therefore
    writes the run-scoped Ralph entry here and restores the original contents
    on exit. A live v1.1.10 capture (module docstring) showed this path alone
    is enough to make AGY *list* the ``call_mcp_tool`` dispatcher, but not
    enough to make dispatch actually work -- see :func:`_agy_secondary_config_path`.
    """
    return Path.home() / ".gemini" / _AGY_HOME_SUBDIR / "mcp_config.json"


def _agy_secondary_config_path() -> Path:
    """Return the global MCP config path AGY's own docs name and live dispatch reads.

    Measured behaviour (module docstring): a live v1.1.10 run that had the
    Ralph entry written *only* to :func:`_agy_global_config_path` advertised
    ``call_mcp_tool`` in its ``init`` frame but never actually reached
    Ralph's MCP server when instructed to use it. Writing the same entry
    here as well made the identical prompt produce a real ``tools/call``
    round trip. Kept as a separate seam (rather than folded into
    :func:`_agy_global_config_path`) so both paths stay independently
    monkeypatchable in tests.
    """
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def agy_mcp_config(endpoint: str) -> str:
    """Return the AGY MCP JSON config string pointing to the given endpoint.

    Args:
        endpoint: The MCP server HTTP endpoint URL.

    Returns:
        JSON string with mcpServers containing the Ralph entry with serverUrl key.
    """
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "serverUrl": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _normalize_agy_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    """Normalize an AGY server entry to Ralph's expected format.

    AGY uses 'serverUrl' for HTTP servers; Ralph's normalize_upstream_mcp_servers
    expects 'url'. This helper converts 'serverUrl' -> 'url' so the standard
    normalizer can process AGY config entries.

    Args:
        name: Server name.
        entry: Raw server entry dict from mcpServers.

    Returns:
        Tuple of (name, normalized_entry) if valid, None if skipped.
    """
    if name == RALPH_MCP_SERVER_NAME:
        return None
    if not isinstance(entry, Mapping):
        return None
    casted = cast("dict[str, object]", entry)
    # AGY uses serverUrl; Ralph normalizer expects url
    if "serverUrl" in casted and "url" not in casted:
        casted = {**casted, "url": casted["serverUrl"]}
    return name, casted


def load_existing_agy_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read AGY's MCP config files and return any upstream MCP servers found.

    Args:
        workspace_path: Optional workspace path for workspace-level AGY config.

    Returns:
        Tuple of UpstreamMcpServer objects found in AGY config files.
    """
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _agy_mcp_config_paths(workspace_path), _normalize_agy_server_entry
        )
    )


def _agy_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the AGY MCP config file paths to check.

    Order: workspace-level .agents/mcp_config.json first (if workspace_path
    provided), then both of AGY's global config paths (see
    ``_agy_global_config_path`` and ``_agy_secondary_config_path``) so a
    server a user configured through either surface is not dropped by an
    unsafe-mode merge.
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (workspace_path / ".agents" / "mcp_config.json",)
    return (
        *workspace_paths,
        _agy_global_config_path(),
        _agy_secondary_config_path(),
    )


__all__ = [
    "agy_mcp_config",
    "load_existing_agy_upstream_servers",
]
