from __future__ import annotations

import io
import json
from email.message import Message
from threading import Event
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._server_state import ServerState

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.mcp.server._mcp_server import McpServer
    from ralph.mcp.server._metrics import McpMetrics


class _InMemoryServerLike(Protocol):
    mcp_server: McpServer
    state: ServerState
    shutdown_event: Event
    server_address: tuple[str, int]
    health_probe_fn: object | None
    metrics: McpMetrics | None


class _HandlerWithBaseAttrs(Protocol):
    rfile: io.BytesIO
    wfile: io.BytesIO
    headers: Message
    raw_requestline: bytes
    request_version: str
    requestline: str
    command: str
    path: str
    server: _InMemoryServerLike
    client_address: tuple[str, int]
    close_connection: bool

    def do_POST(self) -> None: ...

    def do_GET(self) -> None: ...


class _InMemoryFallbackServer:
    __slots__ = (
        "health_probe_fn",
        "mcp_server",
        "metrics",
        "server_address",
        "shutdown_event",
        "state",
    )

    def __init__(self, mcp_server: McpServer, state: ServerState) -> None:
        self.mcp_server = mcp_server
        self.state = state
        self.shutdown_event = Event()
        self.server_address = ("127.0.0.1", 0)
        self.health_probe_fn: object | None = None
        self.metrics: McpMetrics | None = None

    def server_bind(self) -> None:
        pass

    def server_activate(self) -> None:
        pass


def _make_fake_server(mcp_server: McpServer, state: ServerState) -> _InMemoryFallbackServer:
    return _InMemoryFallbackServer(mcp_server, state)


def _make_headers(payload: bytes, override: dict[str, str] | None) -> Message:
    msg = Message()
    msg["Content-Length"] = str(len(payload))
    for key, value in (override or {}).items():
        msg[key] = value
    return msg


class _BoundInMemoryHandler:
    __slots__ = (
        "_inner",
        "client_address",
        "close_connection",
        "command",
        "headers",
        "path",
        "raw_requestline",
        "request_version",
        "requestline",
        "rfile",
        "server",
        "wfile",
    )

    def __init__(
        self,
        inner: _FallbackHttpHandler,
        *,
        rfile: io.BytesIO,
        wfile: io.BytesIO,
        headers: Message,
        raw_requestline: bytes,
        request_version: str,
        requestline: str,
        command: str,
        path: str,
        server: _InMemoryFallbackServer,
        client_address: tuple[str, int],
        close_connection: bool,
    ) -> None:
        self._inner = inner
        self.rfile = rfile
        self.wfile = wfile
        self.headers = headers
        self.raw_requestline = raw_requestline
        self.request_version = request_version
        self.requestline = requestline
        self.command = command
        self.path = path
        self.server: _InMemoryServerLike = server
        self.client_address = client_address
        self.close_connection = close_connection

    def _sync_inner(self) -> None:
        inner: object = self._inner
        object.__setattr__(inner, "rfile", self.rfile)
        object.__setattr__(inner, "wfile", self.wfile)
        object.__setattr__(inner, "headers", self.headers)
        object.__setattr__(inner, "raw_requestline", self.raw_requestline)
        object.__setattr__(inner, "request_version", self.request_version)
        object.__setattr__(inner, "requestline", self.requestline)
        object.__setattr__(inner, "command", self.command)
        object.__setattr__(inner, "path", self.path)
        object.__setattr__(inner, "server", self.server)
        object.__setattr__(inner, "client_address", self.client_address)
        object.__setattr__(inner, "close_connection", self.close_connection)

    def do_POST(self) -> None:
        self._sync_inner()
        self._inner.do_POST()

    def do_GET(self) -> None:
        self._sync_inner()
        self._inner.do_GET()


def _bind_handler_attrs(
    handler: _FallbackHttpHandler,
    *,
    rfile: io.BytesIO,
    wfile: io.BytesIO,
    headers: Message,
    raw_requestline: bytes,
    request_version: str,
    requestline: str,
    command: str,
    path: str,
    server: _InMemoryFallbackServer,
    client_address: tuple[str, int],
    close_connection: bool,
) -> _HandlerWithBaseAttrs:
    return _BoundInMemoryHandler(
        handler,
        rfile=rfile,
        wfile=wfile,
        headers=headers,
        raw_requestline=raw_requestline,
        request_version=request_version,
        requestline=requestline,
        command=command,
        path=path,
        server=server,
        client_address=client_address,
        close_connection=close_connection,
    )


def _build_handler(
    mcp_server: McpServer,
    payload: bytes,
    *,
    headers: dict[str, str] | None,
    method: str,
    path: str,
) -> tuple[_HandlerWithBaseAttrs, io.BytesIO, _InMemoryFallbackServer]:
    rfile = io.BytesIO(payload)
    wfile = io.BytesIO()
    server = _make_fake_server(mcp_server, ServerState.UNINITIALIZED)
    raw_handler = _FallbackHttpHandler.__new__(_FallbackHttpHandler)
    handler = _bind_handler_attrs(
        raw_handler,
        rfile=rfile,
        wfile=wfile,
        headers=_make_headers(payload, headers),
        raw_requestline=f"{method} {path} HTTP/1.1\r\n".encode(),
        request_version="HTTP/1.1",
        requestline=f"{method} {path} HTTP/1.1",
        command=method,
        path=path,
        server=server,
        client_address=("127.0.0.1", 0),
        close_connection=False,
    )
    return handler, wfile, server


def drive_request(
    mcp_server: McpServer,
    payload_bytes: bytes,
    headers: dict[str, str] | None = None,
    *,
    method: str = "POST",
    path: str = "/mcp",
) -> tuple[int, dict[str, str], bytes]:
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
    handler, wfile, server = _build_handler(
        mcp_server, payload_bytes, headers=headers, method="POST", path="/mcp"
    )
    handler.do_POST()
    server.shutdown_event.set()
    body = wfile.getvalue()
    for chunk in _split_sse_frames(body):
        yield _parse_sse_envelope(chunk)


def _parse_sse_envelope(raw: bytes) -> tuple[int, dict[str, str], bytes]:
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
    if not raw:
        return []
    return [chunk + b"\r\n\r\n" for chunk in raw.split(b"\r\n\r\n") if chunk]


def parse_sse_data(body: bytes) -> dict[str, object]:
    if not body:
        return {}
    text = body.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                decoded: object = json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                return {}
            if isinstance(decoded, dict):
                return {str(key): value for key, value in decoded.items()}
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
