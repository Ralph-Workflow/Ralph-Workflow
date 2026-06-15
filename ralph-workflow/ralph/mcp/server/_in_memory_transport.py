"""In-memory transport harness for the production MCP HTTP handler.

Drives the production ``_FallbackHttpHandler`` over in-memory request and
response buffers (no sockets, no real time) so the full decode -> dispatch ->
stream -> terminal-frame path is exercised on the SHIPPED path. This is the
foundation that every other property test (A, B, C, E, K, and any future
behavior test) routes through -- one transport, one behavior, the shipped
path is the tested path.

The driver must mirror the production ``_FallbackHttpServer`` attribute
surface: the handler casts ``self.server`` to ``_FallbackHttpServer`` and
accesses ``mcp_server``, ``state``, ``shutdown_event``, and the inherited
``server_address`` tuple from ``BaseHTTPRequestHandler``. The SimpleNamespace
returned by :func:`_make_fake_server` carries every attribute the handler
touches in the same order it touches them, so divergence from production is
a test failure rather than a silent half-spec.

Constraints:
- No real time (no ``time.sleep``); the SSE keepalive loop in do_GET must
  be advanced by closing the server's ``shutdown_event``.
- No real subprocess / sockets / network.
- Single function under 80 lines; if it grows, split it.
"""

from __future__ import annotations

import io
import json
from email.message import Message
from threading import Event
from typing import TYPE_CHECKING, cast

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._server_state import ServerState

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.mcp.server._mcp_server import McpServer

    class _HandlerWithBaseAttrs(_FallbackHttpHandler):
        """Typed view of the BaseHTTPRequestHandler attributes the harness sets.

        The stdlib ``BaseHTTPRequestHandler`` does not annotate its instance
        attributes (they are populated in ``setup()``), so we declare them
        here once for the harness's narrow seam.
        """

        rfile: io.BytesIO
        wfile: io.BytesIO
        headers: Message
        raw_requestline: bytes
        request_version: str
        requestline: str
        command: str
        path: str
        server: _FallbackHttpServer
        client_address: tuple[str, int]
        close_connection: bool


class _InMemoryFallbackServer(_FallbackHttpServer):
    """Minimal in-memory subclass of the production ``_FallbackHttpServer``.

    The production ``_FallbackHttpServer`` extends ``ThreadingHTTPServer`` and
    would bind a real socket on construction. The in-memory transport needs
    the same attribute surface (``mcp_server``, ``state``, ``shutdown_event``,
    ``server_address``, ``health_probe_fn``, ``metrics``) without any real
    network binding, so we override ``__init__`` and the network-bound
    ``server_bind``/``server_activate`` to no-ops.

    Because this subclass lives in the test harness, the production
    ``isinstance`` narrowing in ``_coerce_fallback_server`` (PROMPT.md proof
    obligation B) accepts it as a valid bound server.
    """

    def __init__(self, mcp_server: McpServer, state: ServerState) -> None:
        self.mcp_server = mcp_server
        self.state = state
        self.shutdown_event = Event()
        self.server_address = ("127.0.0.1", 0)
        self.health_probe_fn = None
        self.metrics = None

    def server_bind(self) -> None:
        """No-op: the in-memory harness never binds a real socket."""

    def server_activate(self) -> None:
        """No-op: the in-memory harness never activates a real listener."""


def _make_fake_server(mcp_server: McpServer, state: ServerState) -> _FallbackHttpServer:
    """Build an in-memory server that satisfies the production class surface."""
    return _InMemoryFallbackServer(mcp_server, state)


def _make_headers(payload: bytes, override: dict[str, str] | None) -> Message:
    """Build an http.client-style headers Message with the right Content-Length."""
    msg = Message()
    msg["Content-Length"] = str(len(payload))
    for key, value in (override or {}).items():
        msg[key] = value
    return msg


def _build_handler(
    mcp_server: McpServer,
    payload: bytes,
    *,
    headers: dict[str, str] | None,
    method: str,
    path: str,
) -> tuple[_HandlerWithBaseAttrs, io.BytesIO, _FallbackHttpServer]:
    """Build a configured handler with a fake server, rfile, wfile, and headers."""
    rfile = io.BytesIO(payload)
    wfile = io.BytesIO()
    server = _make_fake_server(mcp_server, ServerState.UNINITIALIZED)
    handler: _HandlerWithBaseAttrs = cast(
        "_HandlerWithBaseAttrs",
        _FallbackHttpHandler.__new__(_FallbackHttpHandler),
    )
    handler.rfile = rfile
    handler.wfile = wfile
    handler.headers = _make_headers(payload, headers)
    handler.raw_requestline = f"{method} {path} HTTP/1.1\r\n".encode()
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.command = method
    handler.path = path
    handler.server = server
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = False
    return handler, wfile, server


def drive_request(
    mcp_server: McpServer,
    payload_bytes: bytes,
    headers: dict[str, str] | None = None,
    *,
    method: str = "POST",
    path: str = "/mcp",
) -> tuple[int, dict[str, str], bytes]:
    """Drive one HTTP request through the production handler; return (status, headers, body)."""
    handler, wfile, _server = _build_handler(
        mcp_server, payload_bytes, headers=headers, method=method, path=path
    )
    if method == "POST":
        handler.do_POST()
    else:
        handler.do_GET()
    return _parse_sse_envelope(wfile.getvalue())


def drive_streamed_request(
    mcp_server: McpServer,
    payload_bytes: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> Iterator[tuple[int, dict[str, str], bytes]]:
    """Yield one (status, headers, body) tuple per SSE frame for a streaming call."""
    handler, wfile, server = _build_handler(
        mcp_server, payload_bytes, headers=headers, method="POST", path="/mcp"
    )
    handler.do_POST()
    server.shutdown_event.set()
    body = wfile.getvalue()
    for chunk in _split_sse_frames(body):
        yield _parse_sse_envelope(chunk)


def _parse_sse_envelope(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    """Parse a 200/SSE response into (status, headers, body) for the harness."""
    if not raw:
        return (0, {}, b"")
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = 0
    headers: dict[str, str] = {}
    for line in lines:
        if line.startswith(b"HTTP/1.1 "):
            try:
                status = int(line.split(b" ")[1])
            except (IndexError, ValueError):
                status = 0
        elif b": " in line:
            key, _, value = line.partition(b": ")
            headers[key.decode("ascii", errors="replace").lower()] = value.decode(
                "ascii", errors="replace"
            ).strip()
    return (status, headers, body)


def _split_sse_frames(raw: bytes) -> list[bytes]:
    """Split a concatenated SSE stream into individual frames (each ends in ``\\r\\n\\r\\n``)."""
    if not raw:
        return []
    return [chunk + b"\r\n\r\n" for chunk in raw.split(b"\r\n\r\n") if chunk]


def parse_sse_data(body: bytes) -> dict[str, object]:
    """Extract the JSON-RPC payload from an SSE ``event: message\\r\\ndata: ...`` body."""
    if not body:
        return {}
    text = body.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                return cast("dict[str, object]", json.loads(line[len("data: ") :]))
            except json.JSONDecodeError:
                return {}
    return {}


def _build_tools_list_payload() -> bytes:
    payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    return json.dumps(payload).encode()


__all__ = [
    "_build_tools_list_payload",
    "_make_fake_server",
    "drive_request",
    "drive_streamed_request",
    "parse_sse_data",
]
