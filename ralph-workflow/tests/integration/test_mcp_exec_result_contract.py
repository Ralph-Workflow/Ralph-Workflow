from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.tools.exec as exec_tool
import ralph.mcp.tools.unsafe_exec as unsafe_exec_tool
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

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
def test_jsonrpc_exec_family_returns_inline_text_result(
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

    session = AgentSession(
        session_id="exec-session",
        run_id="exec-run",
        drain="standalone",
        capabilities={"ProcessExecBounded", "ProcessExecUnbounded"},
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    server = McpServer(session, workspace, registry)

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": tool_name, "arguments": arguments},
        msg_id=1,
    )
    response, _ = server.handle_request(request, ServerState.RUNNING)

    assert response is not None
    assert isinstance(response.result, dict)
    result = response.result
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
