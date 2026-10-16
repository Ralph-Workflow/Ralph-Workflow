"""Concrete MCP server factory that allocates dynamically bound localhost endpoints.

``DynamicBindingMcpServerFactory`` is the production implementation of
``McpServerFactory``. It reserves a unique TCP port per worker session, starts an
MCP server subprocess via ``lifecycle.start_mcp_server``, and returns a
``McpServerHandle`` that callers can use to reach the server or shut it down.
"""

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
        """Callable signature for the MCP server start function."""

        def __call__(
            self,
            session: AgentSession,
            workspace: WorkspaceLike,
            *,
            deps: lifecycle.LifecycleDeps | None = None,
        ) -> SessionBridgeLike: ...


_log = logging.getLogger(__name__)


class DynamicBindingMcpServerFactory(McpServerFactory):
    """Build MCP server handles with dynamically allocated localhost endpoints."""

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
        self._allocated_endpoints: set[str] = set()  # bounded-accumulator-ok: drained
        self._allocation_lock = Lock()

    def build(self, session: object) -> McpServerHandle:
        agent_session = self._coerce_session(session)
        # R1: reserve a real LISTENING socket (not a bare port number)
        # so a client connecting during a respawn queues in the kernel
        # backlog instead of receiving ECONNREFUSED. The socket is
        # handed back to ``_start_server`` via a deps override whose
        # ``reserve_listening_socket`` returns the stashed socket, and
        # the bridge's ``shutdown`` closes it (lifecycle stashes it on
        # ``bridge._listening_socket``; the wrapped shutdown below
        # releases the endpoint bookkeeping after teardown).
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
            # Startup failure path: the bridge never came up, so no
            # handle is returned to the caller. Close the reserved
            # listening socket and release the reserved endpoint now
            # so the port is available for the next ``build()`` call.
            # Re-raise the original exception unchanged so callers
            # still observe the failure mode.
            reserved_sock.close()
            self._release_endpoint(reserved_endpoint)
            raise
        # wt-024 iteration-4 (AC-06): every failure path AFTER
        # ``_start_server`` succeeds must also release the
        # reserved endpoint. ``_bridge_pid(bridge)`` and
        # ``bridge.agent_endpoint_uri()`` can both raise in real
        # deployments (a custom bridge implementation may not
        # expose ``process.pid``, or the bridge may have torn
        # itself down between the start_server return and our
        # access). If we let the endpoint leak here, the factory
        # loses one port from its pool on every such failure.
        try:
            pid = self._bridge_pid(bridge)
            # Use the bridge's own endpoint so test doubles that
            # synthesize their own endpoint are honored. The reserved
            # endpoint is the source of truth for release because
            # that is what we added to ``_allocated_endpoints``; the
            # bridge's endpoint may differ in test doubles but is
            # what callers see on the handle.
            endpoint = bridge.agent_endpoint_uri()
        except Exception:
            # Post-startup extraction failure: the bridge exists but
            # we cannot hand a usable handle to the caller. Tear the
            # server down (best effort) and release the reserved
            # endpoint so the port returns to the pool. Re-raise the
            # original exception unchanged so callers still observe
            # the failure mode.
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
        # R1: make the listening-socket release deterministic. The
        # production ``start_mcp_server`` already stashes the socket
        # on ``bridge._listening_socket``; for custom start_server
        # doubles that skip the lifecycle, stash it here so bridge
        # ``shutdown`` still closes it.
        # Custom bridge doubles may skip the lifecycle's socket stash;
        # re-stash so bridge shutdown still closes the socket.
        existing_socket: object | None = getattr(bridge, "_listening_socket", None)
        if existing_socket is None:
            bridge._listening_socket = reserved_sock  # type: ignore[attr-defined]
        # wt-024 M8 (AC-06): release the endpoint from
        # ``_allocated_endpoints`` AFTER the server process is down
        # so the same factory can reuse the port on a later build.
        # The release happens after ``bridge.shutdown`` so callers
        # cannot observe a port as available while the underlying
        # server is still bound. The original ``bridge.shutdown`` is
        # captured so a subclass override still gets called.
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
        """Reserve a unique live listening socket for one handle.

        Returns the endpoint string (for ``_allocated_endpoints``
        bookkeeping) and the ``(host, port, socket)`` triple. Retries
        on the (astronomically unlikely) collision with an endpoint
        still tracked in ``_allocated_endpoints``.
        """
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
        # wt-024 M8 (AC-06): opposite of ``_reserve_unique_port``.
        # Called from the wrapped ``shutdown`` so the endpoint is
        # available for reuse on the next ``build()`` call. The
        # discard is a no-op if the endpoint was never reserved
        # (defense in depth: the wrapped shutdown may run more than
        # once in pathological error paths).
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
