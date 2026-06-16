"""Claude-specific MCP transport helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths, merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers


def claude_mcp_config(
    endpoint: str,
    *,
    workspace_path: Path | None = None,
    unsafe_mode: bool = False,
) -> str:
    """Return the Claude MCP JSON config string pointing to the given endpoint."""
    ralph_entry = {
        RALPH_MCP_SERVER_NAME: {
            "type": "http",
            "url": endpoint,
        }
    }
    current_config: dict[str, object] = {"mcpServers": dict(ralph_entry)}
    if workspace_path is not None:
        current_config["workspace_path"] = workspace_path
    merged_config = merge_existing_upstreams(
        "claude", current_config, unsafe_mode=unsafe_mode, workspace_path=workspace_path
    )
    config_payload = merged_config
    return json.dumps(config_payload, separators=(",", ":"))


def load_existing_claude_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read Claude's MCP config files and return any upstream MCP servers found."""
    servers = _load_mcpservers_from_paths(_claude_mcp_config_paths(workspace_path))
    return normalize_upstream_mcp_servers(servers)


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


__all__ = [
    "claude_mcp_config",
    "load_existing_claude_upstream_servers",
]
