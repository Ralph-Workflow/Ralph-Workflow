"""Shared upstream MCP environment wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.session_plan import effective_session_mcp_plan_from_servers
from ralph.mcp.transport.common import mcp_toml_as_upstreams, set_upstream_mcp_config

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.upstream.config import UpstreamMcpServer


def apply_upstream_env(
    upstreams: tuple[UpstreamMcpServer, ...],
    workspace_path: Path | None,
    runtime_env: dict[str, str],
    server_env: dict[str, str],
) -> None:
    """Apply effective upstream MCP configuration to both environments."""
    effective_mcp = effective_session_mcp_plan_from_servers(
        mcp_toml_as_upstreams(workspace_path),
        agent_upstream_servers=upstreams,
    )
    set_upstream_mcp_config(runtime_env, effective_mcp.effective_servers)
    set_upstream_mcp_config(server_env, effective_mcp.effective_servers)
