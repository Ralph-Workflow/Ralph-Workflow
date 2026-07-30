"""Factory helpers for building per-worker MCP session bundles.

Provides ``build_worker_session``, which constructs an ``AgentSession``,
starts an MCP server for it via ``McpServerFactory``, and returns a
``WorkerSessionBundle`` containing the session, its server handle, and the
workspace scope that the worker should operate in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.pipeline.parallel.worker_session_bundle import WorkerSessionBundle

if TYPE_CHECKING:
    from ralph.mcp.server.factory import McpServerFactory
    from ralph.pipeline.work_units import WorkUnit
    from ralph.workspace.scope import WorkspaceScope


@dataclass(frozen=True)
class WorkerSessionConfig:
    """Optional session contract parameters for a parallel worker session."""

    worker_artifact_dir: Path | None = None
    worker_namespace: Path | None = None
    session_drain: str = ""
    session_capabilities: frozenset[str] = frozenset()
    session_model_identity: MultimodalModelIdentity | None = None
    session_capability_profile: ResolvedCapabilityProfile | None = None


def build_worker_session(
    unit: WorkUnit,
    mcp_factory: McpServerFactory,
    workspace_scope: WorkspaceScope,
    config: WorkerSessionConfig | None = None,
) -> WorkerSessionBundle:
    """Create an AgentSession, start an MCP server, and return the worker bundle.

    Pass a ``WorkerSessionConfig`` to propagate session contract parameters
    (drain, capabilities, model identity, capability profile) from the parent
    phase's ``SessionMcpPlan`` so the worker exposes the same multimodal
    capability surface as serial execution.
    """
    cfg = config if config is not None else WorkerSessionConfig()
    session_id = f"dev-{unit.unit_id}-{uuid4().hex[:8]}"
    session = AgentSession(
        session_id=session_id,
        run_id=unit.unit_id,
        drain=cfg.session_drain,
        capabilities=set(cfg.session_capabilities),
        parallel_worker=True,
        worker_artifact_dir=cfg.worker_artifact_dir,
        worker_namespace=cfg.worker_namespace,
        model_identity=(
            cfg.session_model_identity
            if cfg.session_model_identity is not None
            else UNKNOWN_IDENTITY
        ),
        stored_capability_profile=cfg.session_capability_profile,
    )
    mcp_handle = mcp_factory.build(session)
    # AC-04: attach one ExploreIndex per worker so grep/search/list/edit
    # operations share the same generation + dirty-path state as serial
    # execution. Mirrors ``ralph/pipeline/session_bridge.py:218-231``:
    # ``build_explore_index`` only opens the store — it does not index —
    # so the cost is bounded. The try/except keeps the worker alive
    # when the explore index is unavailable (read-only / exotic
    # workspaces); the handler layer already treats a missing handle
    # as live-grep fallback.
    try:
        from ralph.mcp.explore.handlers import build_explore_index

        explore_handle = build_explore_index(Path(workspace_scope.root))
        session.explore_index = explore_handle
    except Exception:
        pass
    return WorkerSessionBundle(
        session=session,
        mcp_handle=mcp_handle,
        workspace_scope=workspace_scope,
    )


__all__ = ["WorkerSessionBundle", "WorkerSessionConfig", "build_worker_session"]
