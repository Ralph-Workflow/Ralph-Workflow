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
from ralph.mcp.protocol.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    merge_mcp_toml_into_upstreams,
    set_upstream_mcp_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.policy.models import AgentsPolicy


@dataclass(frozen=True)
class SessionMcpPlan:
    capabilities: frozenset[str]
    server_env: dict[str, str] | None = None


def build_session_mcp_plan(
    *,
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None = None,
) -> SessionMcpPlan:
    """Build the runtime MCP plan for a new agent session.

    The result captures both session capability grants and any upstream MCP
    environment that must be present in the Ralph MCP subprocess so its runtime
    tool registry matches what the agent is expected to see.
    """

    capabilities = _base_capabilities_for_drain(drain, agents_policy)
    mcp_config = load_mcp_config(
        config_path=(
            (workspace_path / ".agent" / "mcp.toml")
            if workspace_path is not None
            else None
        )
    )

    drain_class = drain_class_for_session(drain, agents_policy)
    is_commit = drain_class.value == "commit"

    if mcp_config.web_search.enabled and not is_commit:
        capabilities.add("web.search")
    if mcp_config.web_visit.enabled and not is_commit:
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

    if upstreams and not is_commit:
        capabilities.add("upstream.tool_use")

    return SessionMcpPlan(
        capabilities=frozenset(capabilities),
        server_env=server_env or None,
    )


def _base_capabilities_for_drain(
    drain: str,
    agents_policy: AgentsPolicy | None = None,
) -> set[str]:
    drain_class = drain_class_for_session(drain, agents_policy)
    # capability_class overrides drain_class for MCP tool surface selection
    capability_cls: DrainClass = drain_class
    if agents_policy is not None:
        drain_cfg = agents_policy.agent_drains.get(drain)
        if drain_cfg is not None and drain_cfg.capability_class is not None:
            capability_cls = DrainClass(drain_cfg.capability_class)

    base = {
        "workspace.read",
        "git.status_read",
        "git.diff_read",
        "artifact.submit",
        "workspace.metadata_read",
    }

    if capability_cls == DrainClass.PLANNING:
        return base
    if capability_cls == DrainClass.REVIEW:
        return base | {"run.report_progress"}
    if capability_cls == DrainClass.ANALYSIS:
        return base | {"process.exec_bounded", "run.report_progress"}
    # Commit drains are strictly read-only: git.write is reserved to the orchestrator.
    if capability_cls == DrainClass.COMMIT:
        return base | {"run.report_progress"}
    return base | {
        "workspace.write_ephemeral",
        "workspace.write_tracked",
        "workspace.edit",
        "workspace.delete",
        "process.exec_bounded",
        "run.report_progress",
        "env.read",
    }


__all__ = ["SessionMcpPlan", "build_session_mcp_plan"]
