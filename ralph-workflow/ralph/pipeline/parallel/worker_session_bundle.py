"""Bundle of assembled session resources for a parallel worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.protocol.session import AgentSession

if TYPE_CHECKING:
    from ralph.mcp.server.factory import McpServerHandle
    from ralph.workspace.scope import WorkspaceScope


@dataclass(frozen=True)
class WorkerSessionBundle:
    """Assembled session, MCP server handle, and workspace scope for a parallel worker."""

    session: AgentSession
    mcp_handle: McpServerHandle
    workspace_scope: WorkspaceScope
