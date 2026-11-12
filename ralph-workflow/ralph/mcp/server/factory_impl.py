from __future__ import annotations

import logging
from dataclasses import replace
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.server._reserved_socket import _reserve_listening_socket

if TYPE_CHECKING:
    from socket import socket

    from ralph.mcp.protocol._session_bridge_like import SessionBridgeLike
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._bridge_with_process import _BridgeWithProcess
from ralph.mcp.server.factory import McpServerFactory, McpServerHandle

if TYPE_CHECKING:
    from ralph.mcp.protocol.startup import WorkspaceLike

    class StartServer(Protocol):
        def __call__(
            self,
            session: AgentSession,
            workspace: WorkspaceLike,
            *,
            deps: lifecycle.LifecycleDeps | None = None,
        ) -> SessionBridgeLike: ...


_log = logging.getLogger(__name__)


class DynamicBindingMcpServerFactory(McpServerFactory):
    def __init__(
        self,
        workspace: WorkspaceLike,
        *,
        start_server: StartServer = lifecycle.start_mcp_server,
        lifecycle_deps: lifecycle.LifecycleDeps | None = None,
    ) -> None:
        self.workspace = workspace
        self._start_server = start_server
        self._base_deps = lifecycle_deps or lifecycle._default_lifecycle_deps()
        self._allocated_endpoints: set[str] = set()
        self._allocation_lock = Lock()

    def build(self, session: object) -> McpServerHandle:
        agent_session = self._coerce_session(session)
        reserved_endpoint, (reserved_host, reserved_port, reserved_sock) = (
            self._reserve_listening_socket()
        )

        def _reserve_stashed_socket() -> tuple[str, int, socket]:
            return reserved_host, reserved_port, reserved_sock

        try:
            bridge = self._start_server(
                agent_session,
                self.workspace,
                deps=replace(
                    self._base_deps,
                    reserve_listening_socket=_reserve_stashed_socket,
                ),
            )
        except Exception:
            reserved_sock.close()
            self._release_endpoint(reserved_endpoint)
            raise
        try:
            pid = self._bridge_pid(bridge)
            endpoint = bridge.agent_endpoint_uri()
        except Exception:
            try:
                bridge.shutdown()
            except Exception:
                _log.debug(
                    "MCP factory: bridge.shutdown raised during post-startup "
                    "failure recovery (suppressed)",
                    exc_info=True,
                )
            reserved_sock.close()
            self._release_endpoint(reserved_endpoint)
            raise
        existing_socket: object | None = getattr(bridge, "_listening_socket", None)
        if existing_socket is None:
            object.__setattr__(bridge, "_listening_socket", reserved_sock)
        original_shutdown = bridge.shutdown
        endpoint_ref = reserved_endpoint

        def _shutdown_and_release() -> None:
            try:
                original_shutdown()
            finally:
                self._release_endpoint(endpoint_ref)

        return McpServerHandle(
            endpoint=endpoint,
            pid=pid,
            shutdown=_shutdown_and_release,
        )

    def _reserve_listening_socket(self) -> tuple[str, tuple[str, int, socket]]:
        reserve = self._base_deps.reserve_listening_socket or _reserve_listening_socket
        while True:
            host, port, sock = reserve()
            endpoint = f"http://127.0.0.1:{port}/mcp"
            with self._allocation_lock:
                if endpoint in self._allocated_endpoints:
                    sock.close()
                    continue
                self._allocated_endpoints.add(endpoint)
                return endpoint, (host, port, sock)

    def _release_endpoint(self, endpoint: str) -> None:
        with self._allocation_lock:
            self._allocated_endpoints.discard(endpoint)

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
