"""Google Anti Gravity (AGY) transport helpers.

This module provides AGY-specific MCP transport helpers.

Research-confirmed facts:
- Executable: agy
- Print flag: --print
- Yolo flag: --dangerously-skip-permissions
- Session flag: --conversation {}
- MCP config path: ~/.gemini/antigravity-cli/mcp_config.json
- HTTP JSON key: serverUrl
- Output format: plain text (not NDJSON) - uses JsonParserType.GENERIC

Ralph reads existing AGY upstream servers from the user config files at
~/.gemini/antigravity-cli/mcp_config.json and workspace .agents/mcp_config.json.
The agy_mcp_config() helper builds the AGY-native JSON payload for Ralph's MCP
endpoint using AGY's serverUrl field.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

# AGY home config directory name within its default config root
_AGY_HOME_SUBDIR = "antigravity-cli"


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
    merged: dict[str, object] = {}
    for path in _agy_mcp_config_paths(workspace_path):
        config_obj = _parse_json_config_file(path)
        if not config_obj:
            continue
        value = config_obj.get("mcpServers")
        if isinstance(value, dict):
            for srv_name, srv_entry in value.items():
                normalized = _normalize_agy_server_entry(srv_name, srv_entry)
                if normalized is not None:
                    merged[normalized[0]] = normalized[1]
    return normalize_upstream_mcp_servers(merged)


def _agy_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the AGY MCP config file paths to check.

    Order: workspace-level .agents/mcp_config.json first (if workspace_path provided),
    then global ~/.gemini/antigravity-cli/mcp_config.json.
    """
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (
            workspace_path / ".agents" / "mcp_config.json",
        )
    return (
        *workspace_paths,
        Path.home() / ".gemini" / _AGY_HOME_SUBDIR / "mcp_config.json",
    )


def _parse_json_config_file(path: Path) -> dict[str, object]:
    """Parse a JSON config file, returning empty dict on error."""
    if not path.exists():
        return {}
    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_payload, dict):
        return {}
    return cast("dict[str, object]", raw_payload)


__all__ = [
    "agy_mcp_config",
    "load_existing_agy_upstream_servers",
]
