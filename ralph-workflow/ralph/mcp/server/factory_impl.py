"""Concrete MCP server factory that allocates dynamically bound localhost endpoints.

``DynamicBindingMcpServerFactory`` is the production implementation of
``McpServerFactory``. It reserves a unique TCP port per worker session, starts an
MCP server subprocess via ``lifecycle.start_mcp_server``, and returns a
``McpServerHandle`` that callers can use to reach the server or shut it down.
"""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.protocol.startup import SessionBridgeLike, WorkspaceLike
from ralph.mcp.server import lifecycle
from ralph.mcp.server.factory import McpServerFactory, McpServerHandle


class StartServer(Protocol):
    def __call__(
        self,
        session: AgentSession,
        workspace: WorkspaceLike,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> SessionBridgeLike: ...


@runtime_checkable
class _ProcessWithPid(Protocol):
    pid: int


@runtime_checkable
class _BridgeWithProcess(SessionBridgeLike, Protocol):
    process: _ProcessWithPid


class DynamicBindingMcpServerFactory(McpServerFactory):
    """Build MCP server handles with dynamically allocated localhost endpoints."""

    def __init__(
        self,
        workspace: WorkspaceLike,
        *,
        reserve_port: Callable[[], int] | None = None,
        start_server: StartServer = lifecycle.start_mcp_server,
        lifecycle_deps: lifecycle.LifecycleDeps | None = None,
    ) -> None:
        self.workspace = workspace
        self._start_server = start_server
        self._base_deps = lifecycle_deps or lifecycle._default_lifecycle_deps()
        self._reserve_port = reserve_port or self._base_deps.reserve_port
        self._allocated_endpoints: set[str] = set()
        self._allocation_lock = Lock()

    def build(self, session: object) -> McpServerHandle:
        agent_session = self._coerce_session(session)
        bridge = self._start_server(
            agent_session,
            self.workspace,
            deps=replace(self._base_deps, reserve_port=self._reserve_unique_port),
        )
        pid = self._bridge_pid(bridge)
        return McpServerHandle(
            endpoint=bridge.agent_endpoint_uri(),
            pid=pid,
            shutdown=bridge.shutdown,
        )

    def _reserve_unique_port(self) -> int:
        while True:
            port = self._reserve_port()
            endpoint = f"http://127.0.0.1:{port}/mcp"
            with self._allocation_lock:
                if endpoint in self._allocated_endpoints:
                    continue
                self._allocated_endpoints.add(endpoint)
                return port

    @staticmethod
    def _bridge_pid(bridge: SessionBridgeLike) -> int:
        if not isinstance(bridge, _BridgeWithProcess):
            msg = "MCP server bridge must expose process.pid"
            raise TypeError(msg)
        return bridge.process.pid

    @staticmethod
    def _coerce_session(session: object) -> AgentSession:
        if isinstance(session, AgentSession):
            return session
        msg = "DynamicBindingMcpServerFactory.build requires an AgentSession"
        raise TypeError(msg)


__all__ = ["DynamicBindingMcpServerFactory"]
