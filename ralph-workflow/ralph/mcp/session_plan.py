
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.api.opencode import get_model_by_id
from ralph.config.enums import AgentTransport
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp._model_flag_parser import parse_model_flag
from ralph.mcp._session_model_opts import SessionModelOpts
from ralph.mcp.effective_session_mcp_plan import EffectiveSessionMcpPlan
from ralph.mcp.multimodal.capabilities import (
    TRANSPORT_FIXED_PROVIDER,
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    identity_on_transport,
    resolve_capability_profile,
)
from ralph.mcp.protocol.capability_mapping import DrainClass, SessionDrain, drain_class_for_session
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.agy import load_existing_agy_upstream_servers
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    merge_mcp_toml_into_upstreams,
    set_upstream_mcp_config,
)
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.tool_catalog_cache import (
    apply_tool_catalog_env,
    get_tool_catalog_snapshot,
)
from ralph.policy.validation import PolicyValidationError

_CAPABILITY_PRESETS: dict[str, frozenset[str]] = {
    "planning": frozenset(),
    "review": frozenset({"run.report_progress"}),
    "analysis": frozenset({"run.report_progress"}),
    "commit": frozenset({"run.report_progress", "workspace.write_ephemeral"}),
}

_DRAIN_CAPABILITY_DENIALS: dict[str, frozenset[str]] = {
    "policy_remediation": frozenset({"artifact.submit", "artifact.plan_read"}),
}

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy


type UpstreamLoaderFn = Callable[[Path | None], tuple[UpstreamMcpServer, ...]]


@dataclass(frozen=True)
class SessionMcpPlan:

    capabilities: frozenset[str]
    server_env: dict[str, str] | None = None
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)
    capability_profile: ResolvedCapabilityProfile | None = None


def _fixed_provider_for(transport: AgentTransport) -> str | None:
    return TRANSPORT_FIXED_PROVIDER.get(transport.value)


def _resolve_transport_fixed_identity(
    transport: AgentTransport,
    model_flag: str | None,
) -> MultimodalModelIdentity:
    provider_slug, model_id = parse_model_flag(model_flag or "")
    canonical_provider = TRANSPORT_FIXED_PROVIDER[transport.value]
    if provider_slug is None:
        return MultimodalModelIdentity(
            provider=canonical_provider,
            model_id=model_id,
            transport=transport.value,
        )
    return MultimodalModelIdentity(
        provider=provider_slug,
        model_id=model_id,
        transport=transport.value,
    )


def _resolve_opencode_identity(
    transport: AgentTransport,
    model_flag: str | None,
) -> MultimodalModelIdentity:
    provider_slug, model_id = parse_model_flag(model_flag or "")
    if provider_slug is not None and model_id is not None:
        return MultimodalModelIdentity(
            provider=provider_slug,
            model_id=model_id,
            transport=transport.value,
        )
    if model_id is None:
        return MultimodalModelIdentity(
            provider="unknown",
            model_id=None,
            transport=transport.value,
        )

    try:
        entry = get_model_by_id(model_id)
    except Exception:
        return MultimodalModelIdentity(
            provider="unknown",
            model_id=model_id,
            transport=transport.value,
        )
    if entry is not None and entry.provider_slug is not None:
        return MultimodalModelIdentity(
            provider=entry.provider_slug,
            model_id=model_id,
            transport=transport.value,
        )
    return MultimodalModelIdentity(
        provider="unknown",
        model_id=model_id,
        transport=transport.value,
    )


def resolve_model_identity(
    transport: AgentTransport | None,
    model_flag: str | None = None,
) -> MultimodalModelIdentity:
    if transport is None:
        return UNKNOWN_IDENTITY

    if _fixed_provider_for(transport) is not None:
        return _resolve_transport_fixed_identity(transport, model_flag)

    if transport == AgentTransport.OPENCODE:
        return _resolve_opencode_identity(transport, model_flag)

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

    effective_servers = merge_mcp_toml_into_upstreams(agent_upstream_servers, custom_servers)
    return EffectiveSessionMcpPlan(
        custom_servers=custom_servers,
        agent_upstream_servers=agent_upstream_servers,
        effective_servers=effective_servers,
        provider_visible_server_names=provider_visible_server_names,
    )


def reconcile_injected_transport(
    identity: MultimodalModelIdentity,
    transport: AgentTransport | None,
) -> MultimodalModelIdentity:
    stated = (identity.transport or "").strip().lower()
    if not stated:
        normalised = identity if identity.transport is None else replace(identity, transport=None)
    else:
        normalised = (
            identity if identity.transport == stated else replace(identity, transport=stated)
        )
    if transport is None:
        return normalised
    return identity_on_transport(normalised, transport.value)


def build_session_mcp_plan(
    *,
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None = None,
    model_opts: SessionModelOpts | None = None,
    model_flag: str | None = None,
) -> SessionMcpPlan:

    capability_cls = _resolve_capability_cls(drain, agents_policy)
    capabilities = _base_capabilities_for_resolved_class(
        capability_cls,
        planning_drain=drain == "planning",
    )
    mcp_config = load_mcp_config(
        config_path=(
            (workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
        )
    )

    server_env: dict[str, str] = {}
    effective_mcp = resolve_effective_session_mcp_plan(workspace_path)
    upstreams = effective_mcp.effective_servers

    native_upstream_loaders: dict[AgentTransport, UpstreamLoaderFn] = {
        AgentTransport.CLAUDE: load_existing_claude_upstream_servers,
        AgentTransport.CLAUDE_INTERACTIVE: load_existing_claude_upstream_servers,
        AgentTransport.AGY: load_existing_agy_upstream_servers,
    }
    upstream_loader = native_upstream_loaders.get(transport) if transport is not None else None
    if upstream_loader is not None:
        effective_mcp = resolve_effective_session_mcp_plan(
            workspace_path,
            agent_upstream_servers=upstream_loader(workspace_path),
        )
        upstreams = effective_mcp.effective_servers
        set_upstream_mcp_config(server_env, upstreams)

    catalog_snapshot = get_tool_catalog_snapshot(workspace_path)
    if catalog_snapshot.present:
        upstreams = tuple(server for server in upstreams if server.name in catalog_snapshot.value)
        set_upstream_mcp_config(server_env, upstreams)
        apply_tool_catalog_env(
            server_env,
            catalog_snapshot,
        )

    capabilities = _apply_config_driven_capabilities(
        capabilities,
        capability_cls=capability_cls,
        mcp_config_enabled_web_search=mcp_config.web_search.enabled,
        mcp_config_enabled_web_visit=mcp_config.web_visit.enabled,
        has_upstreams=bool(upstreams),
    )

    capabilities -= _DRAIN_CAPABILITY_DENIALS.get(drain, frozenset())

    _model_opts = model_opts or SessionModelOpts(model_flag=model_flag)
    if _model_opts.model_identity is not None:
        resolved_identity = reconcile_injected_transport(_model_opts.model_identity, transport)
    elif _model_opts.model_flag is not None:
        resolved_identity = resolve_model_identity(transport, _model_opts.model_flag)
    elif transport is not None:
        resolved_identity = MultimodalModelIdentity(
            provider=UNKNOWN_IDENTITY.provider,
            model_id=None,
            transport=transport.value,
        )
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
        "run.report_progress",
        "env.read",
        "process.exec_unbounded",
    }
)

_BASE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "workspace.read",
        "git.status_read",
        "git.diff_read",
        "artifact.submit",
        "artifact.plan_read",
        "workspace.metadata_read",
        "process.exec_bounded",
        "media.read",
        "media.capture",
    }
)

_PROMPT_DRAIN_RUNTIME_CLASSES: dict[SessionDrain, DrainClass] = {
    SessionDrain.PLANNING: DrainClass.PLANNING,
    SessionDrain.DEVELOPMENT_ANALYSIS: DrainClass.ANALYSIS,
    SessionDrain.DEVELOPMENT_COMMIT: DrainClass.COMMIT,
    SessionDrain.ANALYSIS: DrainClass.ANALYSIS,
    SessionDrain.REVIEW: DrainClass.REVIEW,
    SessionDrain.REVIEW_ANALYSIS: DrainClass.ANALYSIS,
    SessionDrain.DEVELOPMENT: DrainClass.DEVELOPMENT,
    SessionDrain.FIX: DrainClass.FIX,
    SessionDrain.REVIEW_COMMIT: DrainClass.COMMIT,
    SessionDrain.COMMIT: DrainClass.COMMIT,
}


def _apply_config_driven_capabilities(
    capabilities: set[str],
    *,
    capability_cls: DrainClass,
    mcp_config_enabled_web_search: bool,
    mcp_config_enabled_web_visit: bool,
    has_upstreams: bool,
) -> set[str]:
    is_commit = capability_cls == DrainClass.COMMIT
    if mcp_config_enabled_web_search and not is_commit:
        capabilities.add("web.search")
    if mcp_config_enabled_web_visit and not is_commit:
        capabilities.add("web.visit")
    if mcp_config_enabled_web_visit and capability_cls in (DrainClass.DEVELOPMENT, DrainClass.FIX):
        capabilities.add("web.download")
    if has_upstreams and not is_commit:
        capabilities.add("upstream.tool_use")
    return capabilities


def default_prompt_capability_identifiers(drain: SessionDrain) -> frozenset[str]:
    capability_cls = _PROMPT_DRAIN_RUNTIME_CLASSES[drain]
    mcp_config = load_mcp_config(config_path=None)
    capabilities = _base_capabilities_for_resolved_class(
        capability_cls,
        planning_drain=drain == SessionDrain.PLANNING,
    )
    return frozenset(
        _apply_config_driven_capabilities(
            capabilities,
            capability_cls=capability_cls,
            mcp_config_enabled_web_search=mcp_config.web_search.enabled,
            mcp_config_enabled_web_visit=mcp_config.web_visit.enabled,
            has_upstreams=False,
        )
    )


def _base_capabilities_for_resolved_class(
    capability_cls: DrainClass,
    *,
    planning_drain: bool,
) -> set[str]:
    cls_value = capability_cls.value
    extras: set[str] = set(_CAPABILITY_PRESETS.get(cls_value, frozenset()))
    if planning_drain:
        extras.add("artifact.plan_write")
    if cls_value in _CAPABILITY_PRESETS:
        return set(_BASE_CAPABILITIES | extras)
    return set(_BASE_CAPABILITIES | _DEVELOPMENT_EXTRA | extras)


__all__ = [
    "EffectiveSessionMcpPlan",
    "SessionMcpPlan",
    "SessionModelOpts",
    "build_session_mcp_plan",
    "default_prompt_capability_identifiers",
    "effective_session_mcp_plan_from_servers",
    "resolve_effective_session_mcp_plan",
    "resolve_model_identity",
]
