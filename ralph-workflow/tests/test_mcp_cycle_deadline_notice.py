"""Cycle-deadline nag carried on MCP tool results.

The cycle-timebox warning used to exist only as an appendix on the prompt that
STARTED an invocation, so it was invisible to an agent whose context had since
been compacted, and an invocation that began before the 80% point was never
warned at all — it could run straight through the deadline. The deadline
instant is fixed for the duration of an invocation, so the pipeline publishes
it as wall-clock epochs the MCP server process reads, and every tool result
carries the nag once the warning point passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._cycle_deadline import CycleDeadlineNotifier, cycle_deadline_notice
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.tool_content import ToolContent
from ralph.mcp.tools.tool_result import ToolResult
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pathlib

    from pytest import MonkeyPatch

_WARN_EPOCH = 1_000_000.0
_DEADLINE_EPOCH = 1_001_440.0  # 24 minutes after the warning point.
_TARGET = "development_final_commit_cleanup"


class _NoopHandler:
    def __call__(self, *_args: object, **_kwargs: object) -> ToolResult:
        return ToolResult(content=[ToolContent.text_content("ok")])


class _FakeEpochClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def test_no_notice_before_the_warning_point() -> None:
    assert (
        cycle_deadline_notice(
            now_epoch=_WARN_EPOCH - 1.0,
            warn_epoch=_WARN_EPOCH,
            deadline_epoch=_DEADLINE_EPOCH,
            finalization_target=_TARGET,
        )
        is None
    )


def test_notice_after_the_warning_point_reports_remaining_minutes() -> None:
    notice = cycle_deadline_notice(
        now_epoch=_WARN_EPOCH + 240.0,
        warn_epoch=_WARN_EPOCH,
        deadline_epoch=_DEADLINE_EPOCH,
        finalization_target=_TARGET,
    )
    assert notice is not None
    assert "20 min" in notice
    assert _TARGET in notice


def test_notice_past_the_deadline_says_the_next_entry_is_redirected() -> None:
    notice = cycle_deadline_notice(
        now_epoch=_DEADLINE_EPOCH + 60.0,
        warn_epoch=_WARN_EPOCH,
        deadline_epoch=_DEADLINE_EPOCH,
        finalization_target=_TARGET,
    )
    assert notice is not None
    assert "0 min" in notice


def test_no_notice_without_a_published_deadline() -> None:
    assert (
        cycle_deadline_notice(
            now_epoch=_WARN_EPOCH,
            warn_epoch=None,
            deadline_epoch=None,
            finalization_target=None,
        )
        is None
    )


def test_notifier_reads_the_published_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, str(_WARN_EPOCH))
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, str(_DEADLINE_EPOCH))
    monkeypatch.setenv(CYCLE_FINALIZATION_TARGET_ENV, _TARGET)

    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH + 600.0))

    notice = notifier.notice()
    assert notice is not None
    assert "14 min" in notice


def _server_with_notifier(notifier: CycleDeadlineNotifier, *, tmp_path: pathlib.Path) -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="read_file",
                description="Test tool",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        _NoopHandler(),
    )
    return McpServer(
        session=AgentSession(
            session_id="session-cycle-deadline-test",
            run_id="run-cycle-deadline-test",
            drain="development",
            capabilities={"WorkspaceRead"},
        ),
        workspace=FsWorkspace(root=tmp_path),
        registry=bridge,
        cycle_deadline_provider=notifier.notice,
    )


def _call_read_file(server: McpServer) -> str:
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    return str(response.result)


def test_tool_result_carries_the_deadline_nag(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """Every tool result nags once the warning point has passed."""
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, str(_WARN_EPOCH))
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, str(_DEADLINE_EPOCH))
    monkeypatch.setenv(CYCLE_FINALIZATION_TARGET_ENV, _TARGET)
    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH + 60.0))

    payload = _call_read_file(_server_with_notifier(notifier, tmp_path=tmp_path))

    assert "cycle timebox" in payload.lower()


def test_tool_result_is_clean_before_the_warning_point(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, str(_WARN_EPOCH))
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, str(_DEADLINE_EPOCH))
    monkeypatch.setenv(CYCLE_FINALIZATION_TARGET_ENV, _TARGET)
    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH - 60.0))

    payload = _call_read_file(_server_with_notifier(notifier, tmp_path=tmp_path))

    assert "cycle timebox" not in payload.lower()


def test_standalone_server_wires_the_deadline_nag(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """The production composition root carries the nag, not just the tests.

    Without this the whole feature can be deleted from the standalone server
    with every other test still green.
    """
    from ralph.mcp.server.runtime import build_standalone_http_server

    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, "0")
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, "9999999999")
    monkeypatch.setenv(CYCLE_FINALIZATION_TARGET_ENV, _TARGET)

    http_server = build_standalone_http_server(tmp_path, port=0)

    notice = http_server._mcp_server._cycle_deadline_provider()
    assert notice is not None
    assert "Cycle timebox" in notice
