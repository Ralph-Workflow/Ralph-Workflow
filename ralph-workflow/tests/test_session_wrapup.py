"""MCP development-timebox warning and completion-admission regressions."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import DEV_WARN_EPOCH_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import (
    request_completion_admission,
    reset_completion_admissions,
)
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.coordination import ToolContent, ToolResult, handle_declare_complete
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pytest


class _ReadHandler:
    def __call__(self, _session: object, _workspace: object, _params: dict[str, object]) -> ToolResult:
        return ToolResult(content=[ToolContent.text_content("ok")], is_error=False)


def _server(tmp_path: Path) -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(name="read_file", description="Read", input_schema={"type": "object"}),
            required_capability="workspace.read",
        ),
        _ReadHandler(),
    )
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
            session_id="wrapup-session",
            run_id="wrapup-run",
            drain="development",
            capabilities={"ArtifactSubmit", "WorkspaceRead"},
        ),
        FsWorkspace(tmp_path),
        bridge,
    )


def _call(server: McpServer, name: str, msg_id: str = "call") -> list[dict[str, object]]:
    response, _ = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=msg_id,
            params={"name": name, "arguments": {}},
        ),
        ServerState.RUNNING,
    )
    assert response is not None and isinstance(response.result, dict)
    content = response.result["content"]
    assert isinstance(content, list)
    return [block for block in content if isinstance(block, dict)]


def _text(blocks: list[dict[str, object]]) -> str:
    return "\n".join(str(block.get("text", "")) for block in blocks)


def test_epoch_warning_appends_development_timebox_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path)
    assert "DEVELOPMENT-TIMEBOX WARNING" not in _text(_call(server, "read_file"))

    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    assert "DEVELOPMENT-TIMEBOX WARNING" in _text(_call(server, "read_file", "warned"))


def test_reset_clears_admission_but_does_not_reset_epoch_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEV_WARN_EPOCH_ENV, repr(time.time() - 1.0))
    monkeypatch.setattr(
        "ralph.mcp.tools.coordination._write_completion_sentinel", lambda *_args, **_kwargs: True
    )
    server = _server(tmp_path)

    assert "COMPLETION ADMISSION REQUIRED" in _text(_call(server, "declare_complete", "first"))
    server.reset_session_budget()
    assert "COMPLETION ADMISSION REQUIRED" in _text(_call(server, "declare_complete", "after-reset"))
    assert "DEVELOPMENT-TIMEBOX WARNING" in _text(_call(server, "read_file", "notice-after-reset"))


def test_completion_admissions_are_identity_scoped_and_resettable() -> None:
    reset_completion_admissions()
    assert request_completion_admission(("session", "run")) is True
    assert request_completion_admission(("session", "run")) is False
    assert request_completion_admission(("other", "run")) is True
    reset_completion_admissions()
    assert request_completion_admission(("session", "run")) is True


def test_reset_wrapup_notification_preserves_wire_compatibility(tmp_path: Path) -> None:
    server = _server(tmp_path)
    response, state = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0", method="notifications/reset_wrapup", msg_id="reset", params={}
        ),
        ServerState.RUNNING,
    )
    assert response is None
    assert state is ServerState.RUNNING
