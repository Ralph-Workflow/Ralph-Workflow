"""Central runtime planner for per-session MCP availability.

This module is the single runtime source of truth for what MCP capabilities a new
agent session should receive and what upstream MCP environment must be injected
into the Ralph MCP subprocess for that session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.api.opencode import get_model_by_id
from ralph.config.enums import AgentTransport
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp._session_model_opts import SessionModelOpts
from ralph.mcp.effective_session_mcp_plan import EffectiveSessionMcpPlan
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.mcp.protocol.capability_mapping import DrainClass, drain_class_for_session
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    merge_mcp_toml_into_upstreams,
    set_upstream_mcp_config,
)
from ralph.policy.validation import PolicyValidationError

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

    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.policy.models import AgentsPolicy


@dataclass(frozen=True)
class SessionMcpPlan:
    """Resolved MCP plan capturing capability grants and server environment for a session."""

    capabilities: frozenset[str]
    server_env: dict[str, str] | None = None
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)
    capability_profile: ResolvedCapabilityProfile | None = None


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
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
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


def resolve_effective_session_mcp_plan(
    workspace_path: Path | None,
    *,
    agent_upstream_servers: tuple[UpstreamMcpServer, ...] = (),
    provider_visible_server_names: tuple[str, ...] = (RALPH_MCP_SERVER_NAME,),
) -> EffectiveSessionMcpPlan:
    """Return the canonical effective MCP inventory for a session.

    ``provider_visible_server_names`` captures the direct provider-visible MCP
    entries (typically just ``ralph``), while ``effective_servers`` captures the
    merged custom + agent-native upstream server set that Ralph will proxy.
    """

    custom_servers = mcp_toml_as_upstreams(workspace_path)
    return effective_session_mcp_plan_from_servers(
        custom_servers,
        agent_upstream_servers=agent_upstream_servers,
        provider_visible_server_names=provider_visible_server_names,
    )


def effective_session_mcp_plan_from_servers(
    custom_servers: tuple[UpstreamMcpServer, ...],
    *,
    agent_upstream_servers: tuple[UpstreamMcpServer, ...] = (),
    provider_visible_server_names: tuple[str, ...] = (RALPH_MCP_SERVER_NAME,),
) -> EffectiveSessionMcpPlan:
    """Build the canonical effective session MCP inventory from preloaded servers."""

    effective_servers = merge_mcp_toml_into_upstreams(agent_upstream_servers, custom_servers)
    return EffectiveSessionMcpPlan(
        custom_servers=custom_servers,
        agent_upstream_servers=agent_upstream_servers,
        effective_servers=effective_servers,
        provider_visible_server_names=provider_visible_server_names,
    )


def build_session_mcp_plan(
    *,
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None = None,
    model_opts: SessionModelOpts | None = None,
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
            (workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
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
    effective_mcp = resolve_effective_session_mcp_plan(workspace_path)
    upstreams = effective_mcp.effective_servers
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        effective_mcp = resolve_effective_session_mcp_plan(
            workspace_path,
            agent_upstream_servers=load_existing_claude_upstream_servers(workspace_path),
        )
        upstreams = effective_mcp.effective_servers
        set_upstream_mcp_config(server_env, upstreams)

    if upstreams and not is_commit:
        capabilities.add("upstream.tool_use")

    _model_opts = model_opts or SessionModelOpts(model_flag=model_flag)
    if _model_opts.model_identity is not None:
        resolved_identity = _model_opts.model_identity
    elif _model_opts.model_flag is not None:
        resolved_identity = resolve_model_identity(transport, _model_opts.model_flag)
    else:
        resolved_identity = UNKNOWN_IDENTITY
    return SessionMcpPlan(
        capabilities=frozenset(capabilities),
        server_env=server_env or None,
        model_identity=resolved_identity,
        capability_profile=resolve_capability_profile(resolved_identity),
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
            try:
                return DrainClass(drain_cfg.capability_class)
            except ValueError as err:
                raise PolicyValidationError(
                    f"Drain '{drain}' has invalid capability_class "
                    f"'{drain_cfg.capability_class}'; expected one of: "
                    f"planning, development, analysis, review, fix, commit."
                ) from err
    return drain_class


_DEVELOPMENT_EXTRA: frozenset[str] = frozenset(
    {
        "workspace.write_ephemeral",
        "workspace.write_tracked",
        "workspace.edit",
        "workspace.delete",
        "process.exec_bounded",
        "run.report_progress",
        "env.read",
    }
)


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
        "artifact.plan_read",
        "workspace.metadata_read",
    }

    cls_value = capability_cls.value
    extras: set[str] = set(_CAPABILITY_PRESETS.get(cls_value, frozenset()))
    if drain == "planning":
        extras.add("artifact.plan_write")
    if cls_value in _CAPABILITY_PRESETS:
        return base | extras
    # development and fix classes: full write surface
    return base | _DEVELOPMENT_EXTRA | extras


__all__ = [
    "EffectiveSessionMcpPlan",
    "SessionMcpPlan",
    "SessionModelOpts",
    "build_session_mcp_plan",
    "effective_session_mcp_plan_from_servers",
    "resolve_effective_session_mcp_plan",
    "resolve_model_identity",
]
