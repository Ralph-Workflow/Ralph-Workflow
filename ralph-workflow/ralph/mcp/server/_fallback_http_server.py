"""_FallbackHttpServer — ThreadingHTTPServer subclass for the fallback MCP runtime."""

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
        """Drop the inherited listening socket during make-before-break handover.

        In-flight requests keep draining on their own connections while the
        listener goes away. ``serve_forever()`` registered the listener's fd
        number with its selector once, at start, so that fd must stay open
        and must never become ready: closing it makes every poll report the
        fd invalid and every wake run a failing ``accept()``, a busy-spin for
        the rest of the drain. Instead the fd is re-pointed (``dup2``) at one
        end of an idle socketpair. That releases this process's hold on the
        listener exactly as closing it would, and leaves the selector waiting
        on a socket nothing ever writes to. Both pair ends stay open until
        ``server_close``: a closed peer would make the idle end readable.
        """
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
                # ``self.socket`` keeps the fd the selector watches; it now
                # names the idle pair end and is closed by ``server_close``.
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
