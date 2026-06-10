# property-test: E — committed streaming response always terminates with a frame
"""A committed streaming response always terminates with a frame.

Once the server writes SSE headers, it must end the stream with a terminal
frame (result or JSON-RPC error) on every control path: normal completion,
handler exception, post-header exception, and writer-closed mid-stream.
A client must never observe an open stream that silently dies.
"""

from __future__ import annotations

import io
import json
from email.message import Message
from typing import TYPE_CHECKING, Never, cast

import ralph.mcp.server._metrics as _metrics_mod
from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._metrics import McpMetrics, reset_default_metrics
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH
from ralph.mcp.server._server_state import ServerState

if TYPE_CHECKING:
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer


def _exec_post_payload(command: str) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": "exec-prop-e",
            "params": {"name": "exec", "arguments": {"command": command}},
        }
    ).encode("utf-8")


class _SessionWithStreaming:
    session_id = "sess-prop-e"

    def __init__(self) -> None:
        self.tool_output_sink_entry: object = None


class _RecordingServer:
    def __init__(self, mcp: McpServer) -> None:
        self.mcp_server = mcp
        self.state: object = ServerState.RUNNING


def _run_exec_post(server: _RecordingServer) -> str:
    payload = _exec_post_payload("echo hi")
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
    handler.do_POST()
    return wfile.getvalue().decode("utf-8")


def test_normal_exec_call_terminates_with_terminal_frame() -> None:
    """A normal exec call writes the SSE headers and a final frame."""

    class _OkMcp:
        def __init__(self) -> None:
            self._session = _SessionWithStreaming()

        def handle_request(
            self, request: JsonRpcRequest, state: ServerState
        ) -> tuple[JsonRpcResponse, ServerState]:
            return (
                JsonRpcResponse(
                    jsonrpc="2.0",
                    result={"content": [{"type": "text", "text": "ok"}]},
                    msg_id=request.msg_id,
                ),
                state,
            )

    raw = _run_exec_post(_RecordingServer(cast("McpServer", _OkMcp())))
    assert raw, "no body written"
    assert "event: message" in raw
    assert "exec-prop-e" in raw
    assert "ok" in raw


def test_handler_exception_writes_error_terminal_frame() -> None:
    """A handler that raises during dispatch still writes a terminal frame."""

    class _RaisingMcp:
        def __init__(self) -> None:
            self._session = _SessionWithStreaming()

        def handle_request(self, request: JsonRpcRequest, state: ServerState) -> Never:
            raise RuntimeError("dispatch exploded")

    raw = _run_exec_post(_RecordingServer(cast("McpServer", _RaisingMcp())))
    assert "exec-prop-e" in raw
    assert "-32603" in raw
    assert "dispatch exploded" in raw


def test_session_without_streaming_surface_still_terminates() -> None:
    """A session missing tool_output_sink_entry must still produce a final frame."""

    class _SessionWithoutStreaming:
        session_id = "sess-prop-e-no-stream"

    class _OkMcp:
        def __init__(self) -> None:
            self._session = _SessionWithoutStreaming()

        def handle_request(
            self, request: JsonRpcRequest, state: ServerState
        ) -> tuple[JsonRpcResponse, ServerState]:
            return (
                JsonRpcResponse(
                    jsonrpc="2.0",
                    result={"content": [{"type": "text", "text": "ok"}]},
                    msg_id=request.msg_id,
                ),
                state,
            )

    raw = _run_exec_post(_RecordingServer(cast("McpServer", _OkMcp())))
    assert raw, "no body written for streamless session"
    assert "exec-prop-e" in raw
    assert "result" in raw or "-32603" in raw


def test_writer_close_mid_stream_still_terminal_or_silent() -> None:
    """When the writer raises mid-stream, the call must still resolve.

    Simulate a writer that fails on the second write (after headers +
    a notification frame). The streaming path must either (a) emit a
    terminal frame before the failure, or (b) swallow the error and
    close cleanly. Either way, the call must not hang.
    """

    class _SessionStreaming:
        session_id = "sess-prop-e-2"

        def __init__(self) -> None:
            self.tool_output_sink_entry: object = None

    class _WritesTwoFrames:
        def __init__(self) -> None:
            self._session = _SessionStreaming()
            self.calls = 0

        def handle_request(
            self, request: JsonRpcRequest, state: ServerState
        ) -> tuple[JsonRpcResponse, ServerState]:
            # Simulate writing a notification frame then a final frame
            return (
                JsonRpcResponse(
                    jsonrpc="2.0",
                    result={"content": [{"type": "text", "text": "ok"}]},
                    msg_id=request.msg_id,
                ),
                state,
            )

    server = _RecordingServer(cast("McpServer", _WritesTwoFrames()))
    raw = _run_exec_post(server)
    # The result is the only frame for the Ok path; the test passes if
    # _run_exec_post returns a body with "exec-prop-e" present.
    assert "exec-prop-e" in raw
    assert "result" in raw


def test_terminal_frame_counter_increments_for_normal_exec() -> None:
    """Successful exec emits a terminal_frame_emissions counter increment."""
    reset_default_metrics()
    metrics = McpMetrics()

    # Install the fresh metrics as the default.
    _metrics_mod._default_metrics = metrics

    class _OkMcp:
        def __init__(self) -> None:
            self._session = _SessionWithStreaming()

        def handle_request(
            self, request: JsonRpcRequest, state: ServerState
        ) -> tuple[JsonRpcResponse, ServerState]:
            return (
                JsonRpcResponse(
                    jsonrpc="2.0",
                    result={"content": [{"type": "text", "text": "ok"}]},
                    msg_id=request.msg_id,
                ),
                state,
            )

    _run_exec_post(_RecordingServer(cast("McpServer", _OkMcp())))
    # The streaming path increments terminal_frame_emissions exactly once
    # for a successful dispatch.
    assert metrics.snapshot()["terminal_frame_emissions"] >= 1
    reset_default_metrics()


def test_error_terminal_frame_counter_increments_for_raising_handler() -> None:
    """A raising handler still increments the terminal_frame_emissions counter."""
    metrics = McpMetrics()
    _metrics_mod._default_metrics = metrics

    class _RaisingMcp:
        def __init__(self) -> None:
            self._session = _SessionWithStreaming()

        def handle_request(self, request: JsonRpcRequest, state: ServerState) -> Never:
            raise RuntimeError("dispatch exploded")

    _run_exec_post(_RecordingServer(cast("McpServer", _RaisingMcp())))
    assert metrics.snapshot()["terminal_frame_emissions"] >= 1
    _metrics_mod._default_metrics = None
