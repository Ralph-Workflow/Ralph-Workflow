"""_FallbackHttpServer — ThreadingHTTPServer subclass for the fallback MCP runtime."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from ralph.mcp.server._fallback_http_handler_probe import _ProbeResult
    from ralph.mcp.server._metrics import McpMetrics
    from ralph.mcp.server.runtime import McpServer, ServerState


class _FallbackHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    mcp_server: McpServer
    state: ServerState
    shutdown_event: Event
    health_probe_fn: Callable[[], _ProbeResult] | None
    metrics: McpMetrics | None

    def shutdown(self) -> None:
        self.shutdown_event.set()
        super().shutdown()

    def server_close(self) -> None:
        self.shutdown_event.set()
        super().server_close()


__all__ = ["_FallbackHttpServer"]
