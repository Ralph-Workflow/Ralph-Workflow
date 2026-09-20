"""Development-timebox epoch drives MCP completion admission."""

from __future__ import annotations

import time

import pytest

from ralph.mcp.protocol.env import CYCLE_WARN_EPOCH_ENV, DEV_WARN_EPOCH_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools._development_result_session_gate import development_result_session_diagnostics
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.coordination import handle_declare_complete
from ralph.workspace.fs import FsWorkspace


def _server(tmp_path: pytest.TempPathFactory) -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="declare_complete", description="Complete", input_schema={"type": "object"}
            ),
            required_capability="artifact.submit",
        ),
        handle_declare_complete,
    )
    return McpServer(
        AgentSession(
            session_id="dev-timebox-session",
            run_id="dev-timebox-run",
            drain="development",
            capabilities={"ArtifactSubmit"},
        ),
        FsWorkspace(tmp_path),
        bridge,
    )


def _declare(server: McpServer, msg_id: str) -> str:
    response, _ = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=msg_id,
            params={"name": "declare_complete", "arguments": {}},
        ),
        ServerState.RUNNING,
    )
    assert response is not None and isinstance(response.result, dict)
    content = response.result["content"]
    assert isinstance(content, list) and isinstance(content[0], dict)
    return str(content[0]["text"])


def test_past_development_warning_requires_two_separate_completion_calls(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    writes: list[str] = []
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_completion_sentinel",
        lambda _workspace, run_id, **_kwargs: writes.append(run_id) or True,
    )
    server = _server(tmp_path)

    assert "COMPLETION ADMISSION REQUIRED" in _declare(server, "first")
    assert writes == []
    assert "Task declared complete" in _declare(server, "second")
    assert writes == ["dev-timebox-run"]


def test_past_cycle_warning_does_not_require_development_completion_admission(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    monkeypatch.delenv(DEV_WARN_EPOCH_ENV, raising=False)
    writes: list[str] = []
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_completion_sentinel",
        lambda _workspace, run_id, **_kwargs: writes.append(run_id) or True,
    )

    assert "Task declared complete" in _declare(_server(tmp_path), "cycle-warning")
    assert writes == ["dev-timebox-run"]


@pytest.mark.parametrize("warning", ("missing", "future"))
def test_missing_or_future_development_warning_completes_immediately(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, warning: str
) -> None:
    if warning == "future":
        monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() + 600.0))
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_completion_sentinel",
        lambda *_args, **_kwargs: True,
    )

    assert "Task declared complete" in _declare(_server(tmp_path), warning)


def test_dev014_reads_the_same_development_warning_epoch(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = {"status": "partial"}
    session = AgentSession(
        session_id="dev014-session", run_id="dev014-run", drain="development", capabilities=set()
    )
    workspace = FsWorkspace(tmp_path)

    monkeypatch.delenv(DEV_WARN_EPOCH_ENV, raising=False)
    assert development_result_session_diagnostics(session, workspace, content)[0].rule_id == "DEV014"
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    assert development_result_session_diagnostics(session, workspace, content) == []


def test_reset_keeps_epoch_warning_active(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_completion_sentinel",
        lambda *_args, **_kwargs: True,
    )
    server = _server(tmp_path)

    assert "COMPLETION ADMISSION REQUIRED" in _declare(server, "first")
    server.reset_session_budget()
    assert "COMPLETION ADMISSION REQUIRED" in _declare(server, "after-reset")
