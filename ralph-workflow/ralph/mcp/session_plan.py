"""Central runtime planner for per-session MCP availability.

This module is the single runtime source of truth for what MCP capabilities a new
agent session should receive and what upstream MCP environment must be injected
into the Ralph MCP subprocess for that session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.protocol.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    merge_mcp_toml_into_upstreams,
    set_upstream_mcp_config,
)

_CAPABILITY_PRESETS: dict[str, frozenset[str]] = {
    "planning": frozenset(),
    "review": frozenset({"run.report_progress"}),
    "analysis": frozenset({"process.exec_bounded", "run.report_progress"}),
    # workspace.write_ephemeral allows the write_file fallback path when
    # artifact.submit is unavailable; it only permits writes to non-tracked
    # files (.agent/tmp/commit_message.json), not codebase files.
    "commit": frozenset({"run.report_progress", "workspace.write_ephemeral"}),
}

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.policy.models import AgentsPolicy


@dataclass(frozen=True)
class SessionMcpPlan:
    capabilities: frozenset[str]
    server_env: dict[str, str] | None = None
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)


def resolve_model_identity(
    transport: AgentTransport | None,
    model_flag: str | None = None,
) -> MultimodalModelIdentity:
    """Resolve multimodal model identity from agent transport and model flag.

    Returns UNKNOWN_IDENTITY when the provider cannot be determined.
    For OpenCode transport, attempts a catalog lookup to determine the provider.
    On catalog failure or unmapped model, returns an unknown-provider identity
    that still carries model_id and transport so delivery falls back safely.
    """
    if transport is None:
        return UNKNOWN_IDENTITY
    if transport == AgentTransport.CLAUDE:
        return MultimodalModelIdentity(
            provider="claude",
            model_id=model_flag,
            transport=transport.value,
        )
    if transport == AgentTransport.CODEX:
        return MultimodalModelIdentity(
            provider="openai",
            model_id=model_flag,
            transport=transport.value,
        )
    if transport == AgentTransport.OPENCODE and model_flag is not None:
        try:
            from ralph.api.opencode import get_model_by_id  # noqa: PLC0415
            entry = get_model_by_id(model_flag)
            if entry is not None and entry.provider is not None:
                return MultimodalModelIdentity(
                    provider=entry.provider,
                    model_id=model_flag,
                    transport=transport.value,
                )
        except Exception:
            pass
        return MultimodalModelIdentity(
            provider="unknown",
            model_id=model_flag,
            transport=transport.value,
        )
    return MultimodalModelIdentity(
        provider=transport.value,
        model_id=model_flag,
        transport=transport.value,
    )


def build_session_mcp_plan(  # noqa: PLR0913
    *,
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    model_flag: str | None = None,
) -> SessionMcpPlan:
    """Build the runtime MCP plan for a new agent session.

    The result captures both session capability grants and any upstream MCP
    environment that must be present in the Ralph MCP subprocess so its runtime
    tool registry matches what the agent is expected to see.

    Identity resolution precedence:
    1. ``model_identity`` (explicit, if provided)
    2. ``model_flag`` resolved via ``resolve_model_identity(transport, model_flag)``
    3. ``UNKNOWN_IDENTITY`` fallback
    """

    capabilities = _base_capabilities_for_drain(drain, agents_policy)
    mcp_config = load_mcp_config(
        config_path=(
            (workspace_path / ".agent" / "mcp.toml")
            if workspace_path is not None
            else None
        )
    )

    capability_cls = _resolve_capability_cls(drain, agents_policy)
    is_commit = capability_cls == DrainClass.COMMIT

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

    if model_identity is not None:
        resolved_identity = model_identity
    elif model_flag is not None:
        resolved_identity = resolve_model_identity(transport, model_flag)
    else:
        resolved_identity = UNKNOWN_IDENTITY
    return SessionMcpPlan(
        capabilities=frozenset(capabilities),
        server_env=server_env or None,
        model_identity=resolved_identity,
    )


def _resolve_capability_cls(
    drain: str,
    agents_policy: AgentsPolicy | None = None,
) -> DrainClass:
    """Resolve the effective capability class for a drain.

    Uses capability_class from agents_policy when declared, falling back to
    drain_class. This is the single source of truth for MCP surface selection.
    """
    drain_class = drain_class_for_session(drain, agents_policy)
    if agents_policy is not None:
        drain_cfg = agents_policy.agent_drains.get(drain)
        if drain_cfg is not None and drain_cfg.capability_class is not None:
            return DrainClass(drain_cfg.capability_class)
    return drain_class


_DEVELOPMENT_EXTRA: frozenset[str] = frozenset({
    "workspace.write_ephemeral",
    "workspace.write_tracked",
    "workspace.edit",
    "workspace.delete",
    "process.exec_bounded",
    "run.report_progress",
    "env.read",
})


def _base_capabilities_for_drain(
    drain: str,
    agents_policy: AgentsPolicy | None = None,
) -> set[str]:
    capability_cls = _resolve_capability_cls(drain, agents_policy)

    base = {
        "workspace.read",
        "git.status_read",
        "git.diff_read",
        "artifact.submit",
        "workspace.metadata_read",
    }

    cls_value = capability_cls.value
    if cls_value in _CAPABILITY_PRESETS:
        return base | _CAPABILITY_PRESETS[cls_value]
    # development and fix classes: full write surface
    return base | _DEVELOPMENT_EXTRA


__all__ = ["SessionMcpPlan", "build_session_mcp_plan", "resolve_model_identity"]
