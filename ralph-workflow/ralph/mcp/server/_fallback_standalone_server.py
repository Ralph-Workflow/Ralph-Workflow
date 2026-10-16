"""Fallback standalone MCP server using Python's built-in HTTP server."""

from __future__ import annotations

import os
from threading import Event
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from ralph.mcp.runtime_executors import shutdown_runtime_executors
from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._runtime_constants import (
    DEFAULT_MOUNT_PATH,
    DEFAULT_TRANSPORT,
    SERVER_POLL_INTERVAL_SECONDS,
)
from ralph.mcp.server._server_state import ServerState
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Callable
    from socket import socket

    from ralph.mcp.server._mcp_server import McpServer

    EnvGetter = Callable[[str, str | None], str | None]


class _FallbackStandaloneServer:
    def __init__(
        self,
        host: str,
        port: int,
        mcp_server: McpServer,
        *,
        env_getter: EnvGetter = os.environ.get,
        parent_socket: socket | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._mcp_server = mcp_server
        self._env_getter = env_getter
        self._httpd: _FallbackHttpServer | None = None
        # Adopt the parent's already-listening socket when one is
        # supplied. The bridge owns this socket for its full
        # lifetime (R1); the child must not bind a new one. When
        # ``parent_socket`` is ``None`` (one-shot CLI invocations
        # like ``ralph-mcp --port 9000`` with no bridge parent),
        # the standalone server still binds its own port.
        self._parent_socket: socket | None = parent_socket

    @property
    def bound_address(self) -> tuple[str, int]:
        """Return the (host, port) the server is bound to after run() is called."""
        if self._httpd is None:
            raise RuntimeError("Server has not been started yet")
        return cast(
            "tuple[str, int]", self._httpd.server_address
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

    def run(
        self,
        transport: Literal["streamable-http"] = DEFAULT_TRANSPORT,
        *,
        ready_event: Event | None = None,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        if transport != DEFAULT_TRANSPORT:
            raise ValueError(f"Unsupported transport: {transport}")
        if self._parent_socket is not None:
            httpd = _FallbackHttpServer(
                (self._host, self._port),
                _FallbackHttpHandler,
                parent_socket=self._parent_socket,
            )
        else:
            httpd = _FallbackHttpServer((self._host, self._port), _FallbackHttpHandler)
        httpd.mcp_server = self._mcp_server
        httpd.state = ServerState.UNINITIALIZED
        httpd.shutdown_event = Event()
        # Class-annotated optional attributes (``_fallback_http_server.py``)
        # that the handler reads on the /health path. Annotations alone do
        # not create instance attributes, so without these assignments a
        # bare GET /health raised AttributeError and the liveness route
        # served by the production standalone runtime was unreachable.
        httpd.health_probe_fn = None
        httpd.metrics = None
        self._httpd = httpd
        # Startup banner announces the live configuration so an operator can
        # confirm which transport, which session class, and which effective
        # timeouts are running. The earlier code only printed host/port —
        # leaving the session class, the dispatch caps, and the auth posture
        # invisible. Each field is queried from the running configuration so
        # the banner cannot drift from the actual behavior.
        auth_token_set = bool(self._env_getter("MCP_AUTH_TOKEN", None))
        logger.info(
            "ralph-mcp startup: transport={transport} "
            "session_class={session_class} "
            "dispatch_cap_ms={dispatch_cap} "
            "drain_ceiling_ms={drain_ceiling} "
            "kill_escalation_ms={kill_escalation} "
            "probe_timeout_ms={probe_timeout} "
            "auth_token_set={auth} "
            "inherited_fd={inherited_fd}",
            transport="streamable-http",
            session_class=type(self._mcp_server._session).__name__,
            dispatch_cap=EXEC_MAX_TIMEOUT_MS,
            drain_ceiling=5000,
            kill_escalation=5000,
            probe_timeout=int(
                float(self._env_getter("RALPH_MCP_PROBE_TIMEOUT_MS", "5000") or "5000")
            ),
            auth=auth_token_set,
            inherited_fd=self._parent_socket is not None,
        )
        # R4 / R4-banner: signal ready and emit the listening banner
        # ONLY when serving is about to start (before serve_forever
        # blocks). This ensures the banner and the stage file both
        # reflect "server is accepting connections", not "server
        # has been constructed but not started".
        if ready_event is not None:
            ready_event.set()
        if on_ready is not None:
            # The parent records the ready_to_serve stage file at the
            # actual serving boundary, not when ``build_standalone_http_server``
            # returns. Without this hook the stage would land BEFORE
            # the socket is actually accepting connections.
            on_ready()
        print(f"Ralph MCP server listening on http://{self._host}:{self._port}{DEFAULT_MOUNT_PATH}")
        try:
            httpd.serve_forever(poll_interval=SERVER_POLL_INTERVAL_SECONDS)
        finally:
            shutdown_runtime_executors()
            # Release the listening TCP socket on every exit path
            # (normal return, exception, external shutdown). Without
            # this, embedded/long-lived use leaks the FD; one-shot
            # CLI runs rely on OS-level cleanup at exit but the
            # deterministic close is the contract this server
            # advertises (see _fallback_http_server.server_close).
            httpd.server_close()
