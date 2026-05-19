"""Fallback standalone MCP server using Python's built-in HTTP server."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING, Literal, cast

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._runtime_constants import DEFAULT_TRANSPORT, SERVER_POLL_INTERVAL_SECONDS
from ralph.mcp.server._server_state import ServerState

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
        httpd.serve_forever(poll_interval=SERVER_POLL_INTERVAL_SECONDS)
