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
    def __init__(self, mcp_server: McpServer, state: ServerState) -> None:
        self.mcp_server = mcp_server
        self.state = state
        self.shutdown_event = Event()
        self.server_address = ("127.0.0.1", 0)
        self.health_probe_fn = None
        self.metrics = None

    def server_bind(self) -> None:
        pass

    def server_activate(self) -> None:
        pass


def _make_fake_server(mcp_server: McpServer, state: ServerState) -> _FallbackHttpServer:
    return _InMemoryFallbackServer(mcp_server, state)


def _make_headers(payload: bytes, override: dict[str, str] | None) -> Message:
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
