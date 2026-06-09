from __future__ import annotations

import asyncio
import contextlib
import io
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

import ralph.mcp.tools.exec as exec_tool
import ralph.mcp.tools.unsafe_exec as unsafe_exec_tool
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server.exec_sse_streaming import exec_sse_streaming_post
from ralph.mcp.server.runtime import build_fastmcp_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_fragment"),
    [
        ("exec", {"command": "python", "args": ["-c", "print('bounded')"]}, "bounded"),
        ("unsafe_exec", {"command": "python -c \"print('unsafe')\""}, "unsafe"),
        ("raw_exec", {"command": "python -c \"print('raw')\""}, "raw"),
    ],
)
def test_fastmcp_exec_family_returns_inline_text_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, object],
    expected_fragment: str,
) -> None:
    def _fake_run_command(*_args: object, **_kwargs: object) -> exec_tool._CompletedProcessAdapter:
        return exec_tool._CompletedProcessAdapter(
            stdout=(expected_fragment + "\n").encode("utf-8"),
            stderr=b"",
            returncode=0,
        )

    # Both exec and the unsafe/raw family run through the bounded `run_command`;
    # patch it in each module's namespace (unsafe_exec imports it by name).
    monkeypatch.setattr(exec_tool, "run_command", _fake_run_command)
    monkeypatch.setattr(unsafe_exec_tool, "run_command", _fake_run_command)

    server = build_fastmcp_server(tmp_path)

    result = asyncio.run(server._tool_manager.call_tool(tool_name, arguments))

    assert isinstance(result, dict)
    assert result["isError"] is False
    content = result["content"]
    assert isinstance(content, list)
    assert content
    first_block = content[0]
    assert isinstance(first_block, dict)
    assert first_block.get("type") == "text"
    text = first_block.get("text")
    assert isinstance(text, str)
    assert text.strip()
    assert expected_fragment in text
    assert "Exit code: 0" in text


def test_exec_sse_streaming_post_sends_notification_frames_then_response() -> None:
    """exec_sse_streaming_post emits SSE notification frames before the final
    response frame, then restores the session sink."""
    session = AgentSession(
        session_id="test-stream",
        run_id="run-1",
        drain="http://localhost",
        capabilities={"ProcessExecBounded"},
    )
    assert session.tool_output_sink_entry is None

    final_result: dict[str, object] = {
        "content": [{"type": "text", "text": "stream-result"}],
        "isError": False,
    }

    def fake_handle_request(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        sink = session.current_thread_tool_output_sink()
        assert sink is not None
        sink({"tool": "exec", "stream": "combined", "text": "chunk-1"})
        sink({"tool": "exec", "stream": "combined", "text": "chunk-2"})
        return (
            JsonRpcResponse(jsonrpc="2.0", result=final_result, msg_id=request.msg_id),
            state,
        )

    frames_written: list[bytes] = []

    exec_sse_streaming_post(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            params={"name": "exec", "arguments": {"command": "echo", "args": []}},
            msg_id=42,
        ),
        session,
        fake_handle_request,
        ServerState.RUNNING,
        write_frame=frames_written.append,
    )

    assert session.tool_output_sink_entry is None, "Session sink must be cleared after streaming"

    frames: list[dict[str, object]] = []
    for raw_bytes in frames_written:
        raw = raw_bytes.decode("utf-8")
        for block in raw.split("\r\n\r\n"):
            for line in block.splitlines():
                if line.startswith("data: "):
                    with contextlib.suppress(json.JSONDecodeError):
                        frames.append(json.loads(line[6:]))

    assert len(frames) >= 3, (
        f"Expected ≥3 frames (2 notifications + 1 response), got {len(frames)}: {frames}"
    )
    notification_frames = [f for f in frames if f.get("method") == "notifications/message"]
    response_frames = [f for f in frames if "result" in f]
    assert len(notification_frames) == 2, (
        f"Expected 2 notification frames, got {notification_frames}"
    )
    assert len(response_frames) == 1, f"Expected 1 response frame, got {response_frames}"

    last_notification_idx = max(frames.index(f) for f in notification_frames)
    first_response_idx = frames.index(response_frames[0])
    assert last_notification_idx < first_response_idx, (
        "All notification frames must precede the final tools/call response frame"
    )

    texts: list[str] = []
    for f in notification_frames:
        params = f.get("params")
        if isinstance(params, dict):
            text_val = params.get("text")
            if isinstance(text_val, str):
                texts.append(text_val)
    assert "chunk-1" in texts
    assert "chunk-2" in texts


def test_fastmcp_exec_session_sink_receives_output_chunks_before_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgentSession.tool_output_sink receives chunk events before the final result
    when exec runs through the FastMCP server path (build_fastmcp_server + session)."""
    session = AgentSession(
        session_id="test-fastmcp-stream",
        run_id="run-2",
        drain="http://localhost",
        capabilities={"ProcessExecBounded"},
    )

    received_events: list[dict[str, object]] = []

    def _capture_sink(event: dict[str, object]) -> None:
        received_events.append(event)

    # Owner None = "any thread": the FastMCP path dispatches tools on a
    # worker thread, so a single-tenant embedder installs an unowned sink.
    session.tool_output_sink_entry = (None, _capture_sink)

    def fake_run_command(
        command: str,
        args: list[str],
        workspace: object,
        timeout_ms: int,
        deps: exec_tool.ExecRunDeps | None = None,
    ) -> exec_tool._CompletedProcessAdapter:
        if deps is not None and deps.on_output_chunk is not None:
            deps.on_output_chunk("chunk-A\n")
            deps.on_output_chunk("chunk-B\n")
        return exec_tool._CompletedProcessAdapter(
            stdout=b"chunk-A\nchunk-B\n",
            stderr=b"",
            returncode=0,
        )

    monkeypatch.setattr(exec_tool, "run_command", fake_run_command)

    server = build_fastmcp_server(tmp_path, session=session)
    result = asyncio.run(
        server._tool_manager.call_tool("exec", {"command": "echo", "args": ["hi"]})
    )

    assert received_events, "Expected chunk events via the unowned session sink entry"
    assert any(e.get("text") == "chunk-A\n" for e in received_events), (
        f"Expected 'chunk-A\\n' in events: {received_events}"
    )
    assert any(e.get("text") == "chunk-B\n" for e in received_events), (
        f"Expected 'chunk-B\\n' in events: {received_events}"
    )
    assert isinstance(result, dict)
    assert result["isError"] is False


def test_fallback_handler_exec_streaming_post_via_handler_seam() -> None:
    """_handle_exec_streaming_post sends SSE notification frames before the final
    response frame, does not set Content-Length, and restores AgentSession.tool_output_sink."""
    session = AgentSession(
        session_id="test-handler-seam",
        run_id="run-seam",
        drain="http://localhost",
        capabilities={"ProcessExecBounded"},
    )
    assert session.tool_output_sink_entry is None

    final_result: dict[str, object] = {
        "content": [{"type": "text", "text": "handler-seam-result"}],
        "isError": False,
    }

    def _fake_handle(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        sink = session.current_thread_tool_output_sink()
        assert sink is not None
        sink({"tool": "exec", "stream": "combined", "text": "seam-chunk-1"})
        sink({"tool": "exec", "stream": "combined", "text": "seam-chunk-2"})
        return (
            JsonRpcResponse(jsonrpc="2.0", result=final_result, msg_id=request.msg_id),
            state,
        )

    mock_mcp = MagicMock()
    mock_mcp._session = session
    mock_mcp.handle_request = _fake_handle

    mock_server = MagicMock()
    mock_server.mcp_server = mock_mcp
    mock_server.state = ServerState.RUNNING

    wfile_buf = io.BytesIO()
    sent_headers: dict[str, str] = {}

    def _record_header(name: str, value: str) -> None:
        sent_headers[name] = value

    handler = object.__new__(_FallbackHttpHandler)
    handler.wfile = wfile_buf
    handler.send_response = lambda code: None
    handler.send_header = _record_header
    handler.end_headers = lambda: None

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": "exec", "arguments": {"command": "echo", "args": []}},
        msg_id=77,
    )

    handler._handle_exec_streaming_post(request, mock_server)

    assert session.tool_output_sink_entry is None, (
        "Session sink must be cleared after handler returns"
    )

    assert "Content-Length" not in sent_headers, (
        "Exec SSE streaming response must not set Content-Length"
    )
    assert sent_headers.get("Content-Type") == "text/event-stream"

    raw = wfile_buf.getvalue().decode("utf-8")
    frames: list[dict[str, object]] = []
    for block in raw.split("\r\n\r\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                with contextlib.suppress(json.JSONDecodeError):
                    frames.append(json.loads(line[6:]))

    notification_frames = [f for f in frames if f.get("method") == "notifications/message"]
    response_frames = [f for f in frames if "result" in f]

    assert len(notification_frames) >= 2, (
        f"Expected ≥2 notification frames from handler seam, got {notification_frames}"
    )
    assert len(response_frames) >= 1, (
        f"Expected ≥1 response frame from handler seam, got {response_frames}"
    )

    last_notif_idx = max(frames.index(f) for f in notification_frames)
    first_resp_idx = frames.index(response_frames[0])
    assert last_notif_idx < first_resp_idx, (
        "All notification frames must precede the final response frame"
    )

    result_value = response_frames[0].get("result")
    assert result_value is not None, "Final response must include inline exec result"


def test_exec_sse_streaming_post_clears_sink_on_dispatch_error() -> None:
    """exec_sse_streaming_post clears its sink entry even when dispatch raises.

    Clearing (rather than restoring a predecessor) guarantees a sink bound to
    a finished request can never be resurrected and capture later output.
    """
    session = AgentSession(
        session_id="test-error",
        run_id="run-3",
        drain="http://localhost",
        capabilities={"ProcessExecBounded"},
    )

    def _raises(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        raise RuntimeError("dispatch failed")

    frames_written: list[bytes] = []

    exec_sse_streaming_post(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            params={"name": "exec", "arguments": {"command": "echo", "args": []}},
            msg_id=99,
        ),
        session,
        _raises,
        ServerState.RUNNING,
        write_frame=frames_written.append,
    )

    assert session.tool_output_sink_entry is None, (
        "Session sink entry must be cleared even after dispatch error"
    )
    assert frames_written, "Expected an error frame to be written"
    raw = frames_written[-1].decode("utf-8")
    assert "dispatch failed" in raw
    assert "-32603" in raw


