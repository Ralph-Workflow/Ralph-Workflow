from __future__ import annotations

import contextlib
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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
        parent_socket: socket.socket | None = None,
    ) -> None:
        self._dummy_pair: tuple[socket.socket, socket.socket] | None = None
        if parent_socket is not None:
            super().__init__(server_address, request_handler_class, bind_and_activate=False)
            if self.socket is None:
                raise RuntimeError(
                    "ThreadingHTTPServer constructed a None socket even with"
                    " bind_and_activate=False; cannot adopt the inherited FD."
                )
            self.socket.close()
            self.socket = parent_socket
            self.server_address = parent_socket.getsockname()
        else:
            super().__init__(server_address, request_handler_class)
        self.health_probe_fn: Callable[[], _ProbeResult] | None = None
        self.metrics: McpMetrics | None = None
        self.shutdown_event = Event()

    def shutdown(self) -> None:
        if hasattr(self, "shutdown_event"):
            self.shutdown_event.set()
        super().shutdown()

    def handle_error(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def drop_listening_socket(self) -> None:
        old_sock = self.socket
        dummy_r, dummy_w = socket.socketpair()
        self._dummy_pair = (dummy_r, dummy_w)
        old_fd = old_sock.fileno()
        if old_fd >= 0:
            try:
                os.dup2(dummy_r.fileno(), old_fd, inheritable=False)
            except OSError:
                pass
            else:
                return
        self.socket = dummy_r
        with contextlib.suppress(OSError):
            old_sock.close()

    def server_close(self) -> None:
        dummy_pair = self._dummy_pair
        if dummy_pair is not None:
            for s in dummy_pair:
                with contextlib.suppress(OSError):
                    s.close()
            self._dummy_pair = None
        if hasattr(self, "shutdown_event"):
            self.shutdown_event.set()
        super().server_close()


__all__ = ["_FallbackHttpServer"]
