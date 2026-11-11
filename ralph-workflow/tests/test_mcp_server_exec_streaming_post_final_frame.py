from __future__ import annotations

import io
import json
from email.message import Message

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH
from ralph.mcp.server._server_state import ServerState


class _SessionWithoutStreamingSurface:
    session_id = "sess-1"


class _RecordingMcp:
    def __init__(self) -> None:
        self._session = _SessionWithoutStreamingSurface()
        self.calls: list[object] = []

    def handle_request(self, request: object, state: object) -> tuple[JsonRpcResponse, object]:
        self.calls.append(request)
        msg_id = getattr(request, "msg_id", None)
        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"content": [{"type": "text", "text": "ok"}]},
                msg_id=msg_id,
            ),
            state,
        )


class _SessionWithStreamingSurface:
    session_id = "sess-1"

    def __init__(self) -> None:
        self.tool_output_sink_entry: object = None


class _RaisingMcp:
    def __init__(self) -> None:
        self._session = _SessionWithStreamingSurface()

    def handle_request(self, request: object, state: object) -> tuple[JsonRpcResponse, object]:
        raise RuntimeError("dispatch exploded")


class _Server(_FallbackHttpServer):
    def __init__(self, mcp: _RecordingMcp | _RaisingMcp | None = None) -> None:
        self.mcp_server = mcp if mcp is not None else _RecordingMcp()
        self.state: object = ServerState.RUNNING

    def server_bind(self) -> None:
        pass

    def server_activate(self) -> None:
        pass


def _run_exec_post(server: _Server) -> tuple[str, list[tuple[str, str]], bool]:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": "exec-req-1",
            "params": {"name": "exec", "arguments": {"command": "echo hi"}},
        }
    ).encode("utf-8")

    headers = Message()
    headers["Content-Length"] = str(len(payload))
    wfile = io.BytesIO()
    sent_headers: list[tuple[str, str]] = []

    handler = object.__new__(_FallbackHttpHandler)
    handler.path = DEFAULT_MOUNT_PATH
    handler.headers = headers
    handler.rfile = io.BytesIO(payload)
    handler.wfile = wfile
    handler.send_response = lambda code: None
    handler.send_header = lambda name, value: sent_headers.append((name, value))
    handler.end_headers = lambda: None
    handler.server = server

    handler.do_POST()

    return (
        wfile.getvalue().decode("utf-8"),
        sent_headers,
        bool(getattr(handler, "close_connection", False)),
    )


def test_exec_streaming_post_with_streamless_session_still_writes_final_frame() -> None:
    raw, _headers, _closed = _run_exec_post(_Server())

    assert raw, "exec streaming POST closed with an empty body (client would hang)"
    assert "exec-req-1" in raw
    assert "result" in raw or "-32603" in raw


def test_exec_streaming_post_with_raising_dispatch_writes_error_frame() -> None:
    raw, _headers, _closed = _run_exec_post(_Server(_RaisingMcp()))

    assert "exec-req-1" in raw
    assert "-32603" in raw
    assert "dispatch exploded" in raw


def test_exec_streaming_post_closes_response_after_final_frame() -> None:
    raw, headers, closed = _run_exec_post(_Server())

    assert raw, "precondition: final SSE frame was written"
    assert ("Connection", "close") in headers
    assert closed is True
