"""Bundle of assembled session resources for a parallel worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.factory import McpServerHandle
    from ralph.workspace.scope import WorkspaceScope



class _ClosableStore(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class _ExploreIndexOwner(Protocol):
    store: _ClosableStore


@dataclass(frozen=True)
class WorkerSessionBundle:
    """Assembled session, MCP server handle, and workspace scope for a parallel worker."""

    session: AgentSession
    mcp_handle: McpServerHandle
    workspace_scope: WorkspaceScope
    _close_lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def close(self) -> None:
        """Release every resource owned by this worker session exactly once."""
        with self._close_lock:
            if self._closed:
                return
            object.__setattr__(self, "_closed", True)
        try:
            self.mcp_handle.shutdown()
        finally:
            explore_index = self.session.explore_index
            if isinstance(explore_index, _ExploreIndexOwner):
                explore_index.store.close()
