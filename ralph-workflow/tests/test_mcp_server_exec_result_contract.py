from __future__ import annotations

import asyncio
import contextlib
import io
import json
import subprocess
from typing import TYPE_CHECKING, cast

import pytest

import ralph.mcp.tools.exec as exec_tool
import ralph.mcp.tools.unsafe_exec as unsafe_exec_tool
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server.runtime import build_fastmcp_server

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server._fallback_http_server import _FallbackHttpServer


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


def test_fallback_http_exec_streaming_sends_notification_frames_then_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exec tools/call on the fallback HTTP handler emits SSE notification frames
    before the final response frame, then restores the session sink."""
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

    class _CapturingHandler(_FallbackHttpHandler):
        """Testable handler that captures output without a real socket."""

        def __init__(self, output_buf: io.BytesIO) -> None:
            self.wfile: io.BytesIO = output_buf
            self._response_codes: list[int] = []

        def send_response(self, code: int, message: str | None = None) -> None:
            del message
            self._response_codes.append(code)

        def send_header(self, keyword: str, value: str) -> None:
            del keyword, value

        def end_headers(self) -> None:
            pass

    output_buf = io.BytesIO()
    handler = _CapturingHandler(output_buf)

    class _FakeMcpServer:
        _session = session

        def handle_request(
            self, request: JsonRpcRequest, state: ServerState
        ) -> tuple[JsonRpcResponse, ServerState]:
            return fake_handle_request(request, state)

    class _FakeHttpServer:
        def __init__(self) -> None:
            self.mcp_server = _FakeMcpServer()
            self.state = ServerState.RUNNING

    fake_server = cast("_FallbackHttpServer", _FakeHttpServer())

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": "exec", "arguments": {"command": "echo", "args": []}},
        msg_id=42,
    )

    handler._handle_exec_streaming_post(request, fake_server)

    assert session.tool_output_sink is None, "Session sink must be restored after streaming"

    raw = output_buf.getvalue().decode("utf-8")
    frames: list[dict[str, object]] = []
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
