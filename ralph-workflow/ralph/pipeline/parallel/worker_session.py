"""Factory helpers for building per-worker MCP session bundles.

Provides ``build_worker_session``, which constructs an ``AgentSession``,
starts an MCP server for it via ``McpServerFactory``, and returns a
``WorkerSessionBundle`` containing the session, its server handle, and the
workspace scope that the worker should operate in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from ralph.mcp.protocol.session import AgentSession

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server.factory import McpServerFactory, McpServerHandle
    from ralph.pipeline.work_units import WorkUnit
    from ralph.workspace.scope import WorkspaceScope


@dataclass(frozen=True)
class WorkerSessionBundle:
    session: AgentSession
    mcp_handle: McpServerHandle
    workspace_scope: WorkspaceScope


def build_worker_session(
    unit: WorkUnit,
    mcp_factory: McpServerFactory,
    workspace_scope: WorkspaceScope,
    *,
    worker_artifact_dir: Path | None = None,
    worker_namespace: Path | None = None,
) -> WorkerSessionBundle:
    session_id = f"dev-{unit.unit_id}-{uuid4().hex[:8]}"
    session = AgentSession(
        session_id=session_id,
        run_id=unit.unit_id,
        drain="",
        parallel_worker=True,
        worker_artifact_dir=worker_artifact_dir,
        worker_namespace=worker_namespace,
    )
    mcp_handle = mcp_factory.build(session)
    return WorkerSessionBundle(
        session=session,
        mcp_handle=mcp_handle,
        workspace_scope=workspace_scope,
    )


__all__ = ["WorkerSessionBundle", "build_worker_session"]
