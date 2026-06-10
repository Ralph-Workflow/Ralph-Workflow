"""Regression test: the exec SSE streaming POST must always write a final frame.

``do_POST`` sends the SSE headers before dispatching an exec tools/call. If an
exception then escapes the streaming dispatch (e.g. a session object without
the ``tool_output_sink_entry`` surface), the request thread dies and the socket
closes with an empty body. A streamable-http client cannot distinguish that
from a still-running call, so it waits for its full request timeout and
surfaces ``-32001 Request timed out`` — for every retry, forever. The handler
must instead emit a final JSON-RPC frame (result or -32603 error) in all cases.
"""

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
    """Session lacking the tool_output_sink_entry streaming surface."""

    session_id = "sess-1"


class _RecordingMcp:
    def __init__(self) -> None:
        self._session = _SessionWithoutStreamingSurface()
        self.calls: list[object] = []

    def handle_request(
        self, request: object, state: object
    ) -> tuple[JsonRpcResponse, object]:
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
    """Session carrying the streaming surface so dispatch is actually reached."""

    session_id = "sess-1"

    def __init__(self) -> None:
        self.tool_output_sink_entry: object = None


class _RaisingMcp:
    def __init__(self) -> None:
        self._session = _SessionWithStreamingSurface()

    def handle_request(self, request: object, state: object) -> tuple[JsonRpcResponse, object]:
        raise RuntimeError("dispatch exploded")


class _Server(_FallbackHttpServer):
    """In-memory subclass of the production ``_FallbackHttpServer``.

    PROMPT.md proof obligation B requires the production handler to narrow
    ``self.server`` via ``isinstance`` (no ``cast()``), so the test harness
    must subclass the real class to pass that check.
    """

    def __init__(self, mcp: _RecordingMcp | _RaisingMcp | None = None) -> None:
        self.mcp_server = mcp if mcp is not None else _RecordingMcp()
        self.state: object = ServerState.RUNNING

    def server_bind(self) -> None:
        """No-op: the in-memory harness never binds a real socket."""

    def server_activate(self) -> None:
        """No-op: the in-memory harness never activates a real listener."""


def _run_exec_post(server: _Server) -> str:
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

    handler = object.__new__(_FallbackHttpHandler)
    handler.path = DEFAULT_MOUNT_PATH
    handler.headers = headers
    handler.rfile = io.BytesIO(payload)
    handler.wfile = wfile
    handler.send_response = lambda code: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler.server = server

    handler.do_POST()  # must not raise, must not leave an empty body

    return wfile.getvalue().decode("utf-8")


def test_exec_streaming_post_with_streamless_session_still_writes_final_frame() -> None:
    raw = _run_exec_post(_Server())

    assert raw, "exec streaming POST closed with an empty body (client would hang)"
    assert "exec-req-1" in raw
    # Either the dispatch succeeded (result frame) or it failed as a JSON-RPC
    # error frame — both resolve the client's pending request.
    assert "result" in raw or "-32603" in raw


def test_exec_streaming_post_with_raising_dispatch_writes_error_frame() -> None:
    raw = _run_exec_post(_Server(_RaisingMcp()))

    assert "exec-req-1" in raw
    assert "-32603" in raw
    assert "dispatch exploded" in raw
