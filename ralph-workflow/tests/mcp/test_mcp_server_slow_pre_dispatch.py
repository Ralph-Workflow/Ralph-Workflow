"""A stalled shared pre-dispatch phase must be visible in the server log.

Every ``tools/call`` passes through the activity relay and the wire ledger
before its handler runs. When those stall, cheap read-only tools time out on
the client (``timed out awaiting tools/call after 75s``) while the server log
shows nothing. One bounded warning names the tool and how long the phase took.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge._registry import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    import loguru

_STALL_S = 6.0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _call_read_file(tmp_path: Path, stall_s: float) -> list[str]:
    clock = _Clock()

    def slow_activity_sink(_tool_name: str) -> None:
        clock.now += stall_s

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    server = McpServer(
        session,
        workspace,
        build_ralph_tool_registry(session, workspace),
        mcp_activity_sink=slow_activity_sink,
        clock=clock,
    )
    messages: list[str] = []

    def _sink(message: loguru.Message) -> None:
        messages.append(str(message))

    sink_id = logger.add(_sink, level="WARNING", format="{message}")
    try:
        server.handle_request(
            JsonRpcRequest(
                jsonrpc="2.0",
                method="tools/call",
                msg_id=1,
                params={"name": "read_file", "arguments": {"path": "a.txt"}},
            ),
            ServerState.RUNNING,
        )
    finally:
        logger.remove(sink_id)
    return messages


def test_a_stalled_pre_dispatch_phase_logs_the_tool_and_duration(tmp_path: Path) -> None:
    messages = _call_read_file(tmp_path, _STALL_S)

    assert len(messages) == 1
    assert "read_file" in messages[0]
    assert "6.0" in messages[0]


def test_a_prompt_pre_dispatch_phase_logs_nothing(tmp_path: Path) -> None:
    assert _call_read_file(tmp_path, 0.01) == []
