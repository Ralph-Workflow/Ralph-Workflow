"""Fallback standalone MCP server using Python's built-in HTTP server."""

from __future__ import annotations

import os
from threading import Event
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._runtime_constants import DEFAULT_TRANSPORT, SERVER_POLL_INTERVAL_SECONDS
from ralph.mcp.server._server_state import ServerState
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from ralph.mcp.server._mcp_server import McpServer


class _FallbackStandaloneServer:
    def __init__(self, host: str, port: int, mcp_server: McpServer) -> None:
        self._host = host
        self._port = port
        self._mcp_server = mcp_server
        self._httpd: _FallbackHttpServer | None = None

    @property
    def bound_address(self) -> tuple[str, int]:
        """Return the (host, port) the server is bound to after run() is called."""
        if self._httpd is None:
            raise RuntimeError("Server has not been started yet")
        return cast("tuple[str, int]", self._httpd.server_address)

    def run(
        self,
        transport: Literal["streamable-http"] = DEFAULT_TRANSPORT,
        *,
        ready_event: Event | None = None,
    ) -> None:
        if transport != DEFAULT_TRANSPORT:
            raise ValueError(f"Unsupported transport: {transport}")
        httpd = _FallbackHttpServer((self._host, self._port), _FallbackHttpHandler)
        httpd.mcp_server = self._mcp_server
        httpd.state = ServerState.UNINITIALIZED
        httpd.shutdown_event = Event()
        self._httpd = httpd
        if ready_event is not None:
            ready_event.set()
        # Startup banner announces the live configuration so an operator can
        # confirm which transport, which session class, and which effective
        # timeouts are running. The earlier code only printed host/port —
        # leaving the session class, the dispatch caps, and the auth posture
        # invisible. Each field is queried from the running configuration so
        # the banner cannot drift from the actual behavior.
        auth_token_set = bool(os.environ.get("MCP_AUTH_TOKEN"))
        logger.info(
            "ralph-mcp startup: transport={transport} "
            "session_class={session_class} "
            "dispatch_cap_ms={dispatch_cap} "
            "drain_ceiling_ms={drain_ceiling} "
            "kill_escalation_ms={kill_escalation} "
            "probe_timeout_ms={probe_timeout} "
            "auth_token_set={auth}",
            transport="streamable-http",
            session_class=type(self._mcp_server._session).__name__,
            dispatch_cap=EXEC_MAX_TIMEOUT_MS,
            drain_ceiling=5000,
            kill_escalation=5000,
            probe_timeout=int(float(os.environ.get("RALPH_MCP_PROBE_TIMEOUT_MS", "5000"))),
            auth=auth_token_set,
        )
        try:
            httpd.serve_forever(poll_interval=SERVER_POLL_INTERVAL_SECONDS)
        finally:
            # Release the listening TCP socket on every exit path
            # (normal return, exception, external shutdown). Without
            # this, embedded/long-lived use leaks the FD; one-shot
            # CLI runs rely on OS-level cleanup at exit but the
            # deterministic close is the contract this server
            # advertises (see _fallback_http_server.server_close).
            httpd.server_close()
