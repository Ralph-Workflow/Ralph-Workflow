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
        msg_id = getattr(request, "msg_id", None)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"tools": {1, 2, 3}}, msg_id=msg_id),
            state,
        )


class _Server(_FallbackHttpServer):
    def __init__(self) -> None:
        self.mcp_server = _NonSerializableMcp()
        self.state: object = ServerState.RUNNING

    def server_bind(self) -> None:
        pass

    def server_activate(self) -> None:
        pass


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

    handler.do_POST()

    raw = wfile.getvalue().decode("utf-8")
    assert "-32603" in raw
    assert "req-1" in raw


def test_drop_listening_socket_keeps_the_selector_fd_open_and_idle() -> None:
    listener, peer = socket.socketpair()
    server = _FallbackHttpServer(("127.0.0.1", 0), BaseHTTPRequestHandler, parent_socket=listener)
    try:
        served_fd = server.fileno()

        server.drop_listening_socket()

        with pytest.raises(OSError):
            peer.sendall(b"x")
        poller = select.poll()
        poller.register(served_fd, select.POLLIN)
        assert poller.poll(0) == []
        assert server.fileno() == served_fd
    finally:
        server.server_close()
        peer.close()
