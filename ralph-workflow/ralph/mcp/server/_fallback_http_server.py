"""_FallbackHttpServer — ThreadingHTTPServer subclass for the fallback MCP runtime."""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from socket import socket

    from ralph.mcp.server._fallback_http_handler_probe import _ProbeResult
    from ralph.mcp.server._metrics import McpMetrics
    from ralph.mcp.server.runtime import McpServer, ServerState


class _FallbackHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    mcp_server: McpServer
    state: ServerState
    shutdown_event: Event

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        parent_socket: socket | None = None,
    ) -> None:
        if parent_socket is not None:
            # Adopt the inherited listening socket WITHOUT re-binding.
            # ``ThreadingHTTPServer.__init__`` normally calls ``bind``
            # then ``listen``; ``bind_and_activate=False`` skips both,
            # we close the constructor's own eager socket, and assign
            # the parent's so the rest of the server lifecycle
            # (``accept`` loop, ``server_close``, ``shutdown``) operates
            # on the inherited FD. This is the R1 invariant: the
            # endpoint is owned by the bridge for its full lifetime, so
            # a respawn never returns ``ECONNREFUSED`` to a queued
            # client.
            super().__init__(server_address, request_handler_class, bind_and_activate=False)
            # The eager constructor socket exists but is not bound;
            # close it so the inherited FD is the only live listener.
            if self.socket is None:
                raise RuntimeError(
                    "ThreadingHTTPServer constructed a None socket even with"
                    " bind_and_activate=False; cannot adopt the inherited FD."
                )
            self.socket.close()
            self.socket = parent_socket
            self.server_address = parent_socket.getsockname()
            # ``server_bind`` is intentionally NOT called here: that
            # would re-bind the inherited socket to a different port.
        else:
            super().__init__(server_address, request_handler_class)
        # Class-level annotations alone do NOT create instance attributes;
        # the /health handler reads both of these, and a missing attribute
        # raised AttributeError on the production standalone runtime (the
        # Kimi live smoke surfaced it). Constructor-owned defaults keep the
        # contract true no matter which caller wires the server up.
        self.health_probe_fn: Callable[[], _ProbeResult] | None = None
        self.metrics: McpMetrics | None = None
        # Initialise the shutdown event here so server_close and
        # shutdown are always safe to call, even when the caller
        # did not run the standalone run() path that historically
        # bound the event. Tests that construct a server directly
        # rely on this default.
        self.shutdown_event = Event()

    def shutdown(self) -> None:
        self.shutdown_event.set()
        super().shutdown()

    def handle_error(
        self,
        request: socket | tuple[bytes, socket],
        client_address: tuple[str, int],
    ) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def server_close(self) -> None:
        self.shutdown_event.set()
        super().server_close()


__all__ = ["_FallbackHttpServer"]
