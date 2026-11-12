from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents._broker_secret import _parent_broker_secret
from ralph.mcp.artifacts.format_docs import materialize_all_format_docs
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.lifecycle import McpServerExtras, SessionBridgeLike, start_mcp_server
from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan
from ralph.runtime_events import record_runtime_event
from ralph.workspace.fs import FsWorkspace


class _ResetScopeState:
    scope: str | None = None


_ACTIVE_RESET_STATE = _ResetScopeState()
_RESET_SCOPE_LOCK = threading.Lock()

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionMcpPlan
    from ralph.policy.models import AgentsPolicy
    from ralph.workspace.protocol import Workspace


class BuildSessionMcpPlanFn(Protocol):
    def __call__(
        self,
        transport: AgentTransport | None,
        drain: str,
        workspace_path: Path | None,
        agents_policy: AgentsPolicy | None,
        model_opts: SessionModelOpts | None,
        model_flag: str | None,
    ) -> SessionMcpPlan: ...


class StartMcpServerFn(Protocol):
    def __call__(
        self,
        session: AgentSession,
        workspace: Workspace,
        extras: McpServerExtras | None = None,
    ) -> SessionBridgeLike: ...


class WorkspaceFactoryFn(Protocol):
    def __call__(self, root: Path) -> Workspace: ...


class BridgeFactory(Protocol):
    def __call__(
        self,
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: AgentsPolicy | None,
        transport: AgentTransport | None = None,
        capabilities: frozenset[str] | None = None,
        session_id_prefix: str | None = None,
        run_id: str | None = None,
        model_identity: MultimodalModelIdentity | None = None,
        parallel_worker: bool = False,
        worker_namespace: Path | None = None,
        worker_artifact_dir: Path | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
        build_session_mcp_plan_fn: BuildSessionMcpPlanFn | None = None,
        start_mcp_server_fn: StartMcpServerFn | None = None,
        workspace_factory: WorkspaceFactoryFn | None = None,
    ) -> SessionBridgeLike: ...


def _build_session_mcp_plan(
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None,
    model_opts: SessionModelOpts | None,
    model_flag: str | None,
) -> SessionMcpPlan:
    return build_session_mcp_plan(
        transport=transport,
        drain=drain,
        workspace_path=workspace_path,
        agents_policy=agents_policy,
        model_opts=model_opts,
        model_flag=model_flag,
    )


def _start_mcp_server(
    session: AgentSession,
    workspace: Workspace,
    extras: McpServerExtras | None = None,
) -> SessionBridgeLike:
    return start_mcp_server(session, workspace, extras=extras)


def _workspace_factory(root: Path) -> Workspace:
    return FsWorkspace(root)


def build_session_bridge(
    *,
    workspace_root: Path,
    drain: str,
    agents_policy: AgentsPolicy | None,
    transport: AgentTransport | None = None,
    capabilities: frozenset[str] | None = None,
    session_id_prefix: str | None = None,
    run_id: str | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    parallel_worker: bool = False,
    worker_namespace: Path | None = None,
    worker_artifact_dir: Path | None = None,
    allowed_roots: tuple[Path, ...] | None = None,
    build_session_mcp_plan_fn: BuildSessionMcpPlanFn | None = None,
    start_mcp_server_fn: StartMcpServerFn | None = None,
    workspace_factory: WorkspaceFactoryFn | None = None,
    conflict_activity_relay: bool = False,
) -> SessionBridgeLike:
    plan_fn = build_session_mcp_plan_fn or _build_session_mcp_plan
    server_fn = start_mcp_server_fn or _start_mcp_server
    workspace_fn = workspace_factory or _workspace_factory

    model_opts = (
        SessionModelOpts(model_identity=model_identity) if model_identity is not None else None
    )
    session_mcp_plan = plan_fn(
        transport,
        drain,
        workspace_root,
        agents_policy,
        model_opts,
        None,
    )

    prefix = session_id_prefix or drain
    effective_capabilities: set[str] = (
        {str(c) for c in capabilities}
        if capabilities is not None
        else {str(c) for c in session_mcp_plan.capabilities}
    )
    session = AgentSession(
        session_id=f"{prefix}-{uuid.uuid4().hex[:8]}",
        run_id=run_id or str(uuid.uuid4()),
        drain=drain,
        capabilities=effective_capabilities,
        activity_only_supervision=conflict_activity_relay,
        model_identity=session_mcp_plan.model_identity,
        stored_capability_profile=session_mcp_plan.capability_profile,
        parallel_worker=parallel_worker,
        worker_artifact_dir=worker_artifact_dir,
        worker_namespace=worker_namespace,
        allowed_roots=allowed_roots or (),
        broker_secret=_parent_broker_secret(),
    )
    workspace = workspace_fn(workspace_root)
    materialize_all_format_docs(workspace_root)
    try:
        from ralph.mcp.tools._exec_resource_uri import ExecResourceResolver

        spill_root = workspace_root / ".agent" / "tmp"
        session.exec_resource_resolver = ExecResourceResolver(spill_roots=(spill_root,))
    except Exception:
        session.exec_resource_resolver = None
    activity_relay = None
    if conflict_activity_relay:
        from ralph.mcp.server._activity_relay import ActivityRelay

        activity_relay = ActivityRelay()
    bridge = server_fn(
        session,
        workspace,
        extras=McpServerExtras(
            extra_env=session_mcp_plan.server_env,
            activity_relay=activity_relay,
        ),
    )
    bridge.start()
    try:
        from ralph.mcp.explore.handlers import build_explore_index

        explore_handle = build_explore_index(workspace_root)
    except Exception:
        explore_handle = None
    if explore_handle is not None:
        session.explore_index = explore_handle
        workspace.explore_index = explore_handle
    return bridge


def bridge_env_for(bridge: SessionBridgeLike) -> dict[str, str]:
    return {
        str(MCP_ENDPOINT_ENV): bridge.agent_endpoint_uri(),
        str(MCP_RUN_ID_ENV): bridge.run_id,
    }


def scoped_reset_tool_registry_callback(
    bridge: object | None,
    scope_key: str | None,
    recorder: Callable[[str], object] | None = None,
) -> Callable[[], object] | None:
    if not isinstance(scope_key, str) or not scope_key.strip() or scope_key == "unscoped":
        raise ValueError("a tool-registry reset requires an invocation scope")
    if bridge is None:
        return None
    reset_tool_registry_obj: object = getattr(bridge, "reset_tool_registry", None)
    if not callable(reset_tool_registry_obj):
        return None

    def reset() -> object:
        record_runtime_event("mcp_operation", "reset_tool_registry")
        if recorder is not None:
            recorder(scope_key)
        with _RESET_SCOPE_LOCK:
            if _ACTIVE_RESET_STATE.scope is not None:
                return None
            _ACTIVE_RESET_STATE.scope = scope_key
        try:
            return cast("Callable[[], object]", reset_tool_registry_obj)()
        finally:
            with _RESET_SCOPE_LOCK:
                _ACTIVE_RESET_STATE.scope = None

    return reset


__all__ = [
    "BridgeFactory",
    "BuildSessionMcpPlanFn",
    "SessionBridgeLike",
    "StartMcpServerFn",
    "WorkspaceFactoryFn",
    "bridge_env_for",
    "build_session_bridge",
    "scoped_reset_tool_registry_callback",
]
