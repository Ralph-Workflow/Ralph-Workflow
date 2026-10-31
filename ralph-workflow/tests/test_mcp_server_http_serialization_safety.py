"""Regression test: the HTTP handler must not crash on a non-serializable result.

`McpServer.handle_request` now converts handler exceptions to JSON-RPC errors,
but `do_POST` serializes the response with `json.dumps` AFTER that call returns.
If a tool result leaks a non-JSON-serializable value (bytes/set/Path/datetime
through a to_dict()/model_dump()), the `json.dumps` raised OUTSIDE the net and
produced a bare HTTP 500 with no JSON-RPC body — the same broken-session failure
class. This pins that the non-streaming POST path converts a serialization
failure into a -32603 JSON-RPC error frame instead.

It also pins ``_FallbackHttpServer.drop_listening_socket``: dropping the
listener during a handover must leave the fd that ``serve_forever``'s selector
registered open and never readable. Closing it made every poll report the fd
invalid, and each wake ran a failing ``accept`` -- a busy-spin that burned a
whole core for the rest of the retiring child's drain.
"""

from __future__ import annotations

import io
import json
import select
import socket
from email.message import Message
from http.server import BaseHTTPRequestHandler

import pytest

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH
from ralph.mcp.server._server_state import ServerState


class _NonSerializableMcp:
    def handle_request(self, request: object, state: object) -> tuple[JsonRpcResponse, object]:
        # A set is not JSON-serializable; it passes through handle_request
        # (a valid JsonRpcResponse) and only fails at json.dumps in do_POST.
        msg_id = getattr(request, "msg_id", None)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"tools": {1, 2, 3}}, msg_id=msg_id),
            state,
        )


class _Server(_FallbackHttpServer):
    """In-memory subclass of the production ``_FallbackHttpServer``.

    PROMPT.md proof obligation B requires the production handler to narrow
    ``self.server`` via ``isinstance`` (no ``cast()``), so the test harness
    must subclass the real class to pass that check.
    """

    def __init__(self) -> None:
        self.mcp_server = _NonSerializableMcp()
        self.state: object = ServerState.RUNNING

    def server_bind(self) -> None:
        """No-op: the in-memory harness never binds a real socket."""

    def server_activate(self) -> None:
        """No-op: the in-memory harness never activates a real listener."""


def test_do_post_non_serializable_result_emits_jsonrpc_error_not_crash() -> None:
    payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": "req-1"}).encode("utf-8")

    headers = Message()
    headers["Content-Length"] = str(len(payload))
    wfile = io.BytesIO()

    handler = object.__new__(_FallbackHttpHandler)
    handler.path = DEFAULT_MOUNT_PATH
    handler.headers = headers
    handler.rfile = io.BytesIO(payload)
    handler.wfile = wfile
    handler.send_response = lambda code: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler.server = _Server()

    handler.do_POST()  # must not raise

    raw = wfile.getvalue().decode("utf-8")
    assert "-32603" in raw
    assert "req-1" in raw


def test_drop_listening_socket_keeps_the_selector_fd_open_and_idle() -> None:
    listener, peer = socket.socketpair()
    server = _FallbackHttpServer(("127.0.0.1", 0), BaseHTTPRequestHandler, parent_socket=listener)
    try:
        # ``serve_forever`` registers this fd with its selector once, at start.
        served_fd = server.fileno()

        server.drop_listening_socket()

        # The inherited listener itself is released ...
        with pytest.raises(OSError):
            peer.sendall(b"x")
        # ... but the registered fd still names an open socket that never
        # becomes ready, so the serve loop sleeps instead of spinning.
        poller = select.poll()
        poller.register(served_fd, select.POLLIN)
        assert poller.poll(0) == []
        assert server.fileno() == served_fd
    finally:
        server.server_close()
        peer.close()
