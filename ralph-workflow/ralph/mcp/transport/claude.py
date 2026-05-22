"""Claude-specific MCP transport helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths
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
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(_claude_mcp_config_paths(workspace_path))
    )


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
