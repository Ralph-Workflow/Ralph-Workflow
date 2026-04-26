"""Central runtime planner for per-session MCP availability.

This module is the single runtime source of truth for what MCP capabilities a new
agent session should receive and what upstream MCP environment must be injected
into the Ralph MCP subprocess for that session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol.capability_mapping import drain_class_for_session
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    merge_mcp_toml_into_upstreams,
    set_upstream_mcp_config,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class SessionMcpPlan:
    capabilities: frozenset[str]
    server_env: dict[str, str] | None = None


def build_session_mcp_plan(
    *,
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
) -> SessionMcpPlan:
    """Build the runtime MCP plan for a new agent session.

    The result captures both session capability grants and any upstream MCP
    environment that must be present in the Ralph MCP subprocess so its runtime
    tool registry matches what the agent is expected to see.
    """

    capabilities = _base_capabilities_for_drain(drain)
    mcp_config = load_mcp_config(
        config_path=(workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
    )

    drain_class = drain_class_for_session(drain)
    if mcp_config.web_search.enabled and drain_class.value != "commit":
        capabilities.add("web.search")
    if mcp_config.web_visit.enabled:
        capabilities.add("web.visit")
    if mcp_config.media.enabled:
        capabilities.add("media.read")

    server_env: dict[str, str] = {}
    upstreams = mcp_toml_as_upstreams(workspace_path)
    if transport == AgentTransport.CLAUDE:
        upstreams = merge_mcp_toml_into_upstreams(
            load_existing_claude_upstream_servers(workspace_path),
            upstreams,
        )
        set_upstream_mcp_config(server_env, upstreams)
    elif upstreams:
        capabilities.add("upstream.tool_use")

    if upstreams:
        capabilities.add("upstream.tool_use")

    return SessionMcpPlan(
        capabilities=frozenset(capabilities),
        server_env=server_env or None,
    )


def _base_capabilities_for_drain(drain: str) -> set[str]:
    drain_class = drain_class_for_session(drain)
    base = {
        "workspace.read",
        "git.status_read",
        "git.diff_read",
        "artifact.submit",
    }

    if drain_class.value == "planning":
        return base
    if drain_class.value == "review":
        return base | {"run.report_progress"}
    if drain_class.value == "analysis":
        return base | {"process.exec_bounded", "run.report_progress"}
    if drain_class.value == "commit":
        return base | {"workspace.write_ephemeral", "git.write", "run.report_progress"}
    return base | {
        "workspace.write_ephemeral",
        "workspace.write_tracked",
        "process.exec_bounded",
        "run.report_progress",
        "env.read",
    }


__all__ = ["SessionMcpPlan", "build_session_mcp_plan"]
