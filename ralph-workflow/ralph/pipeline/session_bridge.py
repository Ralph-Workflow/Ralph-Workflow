"""Shared session-bridge construction for the main pipeline and plumbing commands.

This module is the single owner of ``AgentSession`` + ``FsWorkspace`` +
``McpServerExtras`` + ``start_mcp_server`` construction. Both the canonical
pipeline and plumbing commands consume it so bridge, env, and reset-tool
wiring cannot drift between the two paths.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.lifecycle import McpServerExtras, SessionBridgeLike, start_mcp_server
from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.session_plan import SessionMcpPlan
    from ralph.policy.models import AgentsPolicy
    from ralph.workspace.protocol import Workspace


class BuildSessionMcpPlanFn(Protocol):
    """Injectable planner returning a ``SessionMcpPlan``."""

    def __call__(
        self,
        transport: AgentTransport | None,
        drain: str,
        workspace_path: Path | None,
        agents_policy: AgentsPolicy | None,
        model_opts: SessionModelOpts | None,
        model_flag: str | None,
    ) -> SessionMcpPlan:
        ...


class StartMcpServerFn(Protocol):
    """Injectable MCP server factory returning a ``SessionBridgeLike``."""

    def __call__(
        self,
        session: AgentSession,
        workspace: Workspace,
        extras: McpServerExtras | None = None,
    ) -> SessionBridgeLike:
        ...


class WorkspaceFactoryFn(Protocol):
    """Injectable workspace factory."""

    def __call__(self, root: Path) -> Workspace:
        ...


class BridgeFactory(Protocol):
    """Factory that builds a session bridge from a workspace root."""

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
    ) -> SessionBridgeLike:
        ...


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
) -> SessionBridgeLike:
    """Build and start a session bridge for the given workspace.

    This is the single owner of ``AgentSession`` + workspace + MCP server
    construction. Callers inject collaborators via the ``*_fn`` parameters;
    production defaults are provided for each.
    """
    plan_fn = build_session_mcp_plan_fn or _build_session_mcp_plan
    server_fn = start_mcp_server_fn or _start_mcp_server
    workspace_fn = workspace_factory or _workspace_factory

    model_opts = (
        SessionModelOpts(model_identity=model_identity)
        if model_identity is not None
        else None
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
        model_identity=session_mcp_plan.model_identity,
        stored_capability_profile=session_mcp_plan.capability_profile,
        parallel_worker=parallel_worker,
        worker_artifact_dir=worker_artifact_dir,
        worker_namespace=worker_namespace,
        allowed_roots=allowed_roots or (),
    )
    workspace = workspace_fn(workspace_root)
    bridge = server_fn(
        session,
        workspace,
        extras=McpServerExtras(extra_env=session_mcp_plan.server_env),
    )
    bridge.start()
    return bridge


def bridge_env_for(bridge: SessionBridgeLike, *, run_id_label: str) -> dict[str, str]:
    """Return the two-key MCP environment dict used by plumbing commands."""
    return {
        str(MCP_ENDPOINT_ENV): bridge.agent_endpoint_uri(),
        str(MCP_RUN_ID_ENV): run_id_label,
    }


def reset_tool_registry_callback(
    bridge: object | None,
) -> object | None:
    """Return a reset callback if the bridge exposes one, else ``None``."""
    if bridge is None:
        return None
    reset_tool_registry_obj: object = getattr(bridge, "reset_tool_registry", None)
    if not callable(reset_tool_registry_obj):
        return None
    return cast("object", reset_tool_registry_obj)


__all__ = [
    "BridgeFactory",
    "BuildSessionMcpPlanFn",
    "SessionBridgeLike",
    "StartMcpServerFn",
    "WorkspaceFactoryFn",
    "bridge_env_for",
    "build_session_bridge",
    "reset_tool_registry_callback",
]
