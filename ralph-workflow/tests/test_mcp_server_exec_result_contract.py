from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.tools.exec as exec_tool
import ralph.mcp.tools.unsafe_exec as unsafe_exec_tool
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server.exec_sse_streaming import exec_sse_streaming_post
from ralph.mcp.server.runtime import build_fastmcp_server

if TYPE_CHECKING:
    from pathlib import Path


def _fake_completed_process(stdout_text: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args="",
        returncode=0,
        stdout=stdout_text.encode("utf-8"),
        stderr=b"",
    )


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
    monkeypatch.setattr(
        exec_tool,
        "run_command",
        lambda *args, **kwargs: exec_tool._CompletedProcessAdapter(
            stdout=(expected_fragment + "\n").encode("utf-8"),
            stderr=b"",
            returncode=0,
        ),
    )
    monkeypatch.setattr(
        unsafe_exec_tool.subprocess,
        "run",
        lambda *args, **kwargs: _fake_completed_process(expected_fragment + "\n"),
    )

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
    assert session.tool_output_sink is None

    final_result: dict[str, object] = {
        "content": [{"type": "text", "text": "stream-result"}],
        "isError": False,
    }

    def fake_handle_request(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        session.stream_tool_output({"tool": "exec", "stream": "combined", "text": "chunk-1"})
        session.stream_tool_output({"tool": "exec", "stream": "combined", "text": "chunk-2"})
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

    assert session.tool_output_sink is None, "Session sink must be restored after streaming"

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

    session.tool_output_sink = _capture_sink

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

    assert received_events, "Expected chunk events to be received via tool_output_sink"
    assert any(e.get("text") == "chunk-A\n" for e in received_events), (
        f"Expected 'chunk-A\\n' in events: {received_events}"
    )
    assert any(e.get("text") == "chunk-B\n" for e in received_events), (
        f"Expected 'chunk-B\\n' in events: {received_events}"
    )
    assert isinstance(result, dict)
    assert result["isError"] is False


def test_exec_sse_streaming_post_restores_sink_on_dispatch_error() -> None:
    """exec_sse_streaming_post restores the session sink even when dispatch raises."""
    session = AgentSession(
        session_id="test-error",
        run_id="run-3",
        drain="http://localhost",
        capabilities={"ProcessExecBounded"},
    )

    def _original_sink(event: dict[str, object]) -> None:
        del event

    session.tool_output_sink = _original_sink

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

    assert session.tool_output_sink is _original_sink, (
        "Session sink must be restored to original value even after dispatch error"
    )
    assert frames_written, "Expected an error frame to be written"
    raw = frames_written[-1].decode("utf-8")
    assert "dispatch failed" in raw
    assert "-32603" in raw


