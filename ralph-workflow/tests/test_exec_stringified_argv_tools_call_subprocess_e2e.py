"""A stringified argv runs through a real ``tools/call`` even under ledger contention.

Some clients send ``command`` as the text of a list (``'["cat", "a.txt"]'``).
This drives the real ``McpServer`` dispatch and a real exec subprocess, with
the wire-ledger append lock held by another writer, and asserts the command
runs as the intended argv and the call does not fail.
"""

from __future__ import annotations

import fcntl
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge._registry import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.subprocess_e2e

_FAST_DEADLINE_S = 0.05


def _server(root: Path) -> McpServer:
    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="development",
        capabilities={"WorkspaceRead", "ProcessExecBounded"},
        broker_secret="exec-secret",
    )
    workspace = FsWorkspace(root)
    return McpServer(session, workspace, build_ralph_tool_registry(session, workspace))


def _exec(server: McpServer, command: str, msg_id: int) -> dict[str, object]:
    response, _ = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=msg_id,
            params={"name": "exec", "arguments": {"command": command}},
        ),
        ServerState.RUNNING,
    )
    assert response is not None
    assert response.error is None
    assert isinstance(response.result, dict)
    return response.result


def test_stringified_argv_executes_as_argv_while_the_ledger_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ralph.mcp.server._wire_ledger.DEFAULT_LOCK_TIMEOUT_S", _FAST_DEADLINE_S)
    (tmp_path / "a.txt").write_text("hello-argv", encoding="utf-8")
    server = _server(tmp_path)
    server.handle_request(
        JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=1), ServerState.RUNNING
    )
    lock_file = next((tmp_path / ".agent" / "tmp").glob("mcp-wire-ledger-*.jsonl.lock"))

    with lock_file.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        result = _exec(server, '["cat", "a.txt"]', 2)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert result["isError"] is False
    assert "hello-argv" in str(result["content"])
