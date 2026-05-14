"""Claude-specific MCP transport helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers


def claude_mcp_config(endpoint: str, *, workspace_path: Path | None = None) -> str:
    """Return the Claude MCP JSON config string pointing to the given endpoint."""
    del workspace_path
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "type": "http",
                "url": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def load_existing_claude_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read Claude's MCP config files and return any upstream MCP servers found."""
    merged: dict[str, object] = {}
    for path in _claude_mcp_config_paths(workspace_path):
        config_obj = _parse_json_config_file(path)
        if not config_obj:
            continue
        value = config_obj.get("mcpServers")
        if isinstance(value, dict):
            merged = {**merged, **cast("dict[str, object]", value)}
    return normalize_upstream_mcp_servers(merged)


def _claude_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (
            workspace_path / ".mcp.json",
            workspace_path / ".claude.json",
        )
    return (
        Path.home() / ".claude.json",
        *workspace_paths,
    )


def _parse_json_config_file(path: Path) -> dict[str, object]:
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
    "claude_mcp_config",
    "load_existing_claude_upstream_servers",
]
