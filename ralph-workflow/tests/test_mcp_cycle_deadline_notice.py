"""Cycle-deadline submission notice carried on MCP tool results.

Past the cycle's warning point, `development_result` validation starts
requiring `## Plan Items Proven` on a completed result and `## Incomplete
Work` (stable ID + `Reason:` + `Evidence:`) on a partial or failed one. That
gate reads the run's clock, not the agent's frontmatter, so it fires whether
or not the agent knows — hence the notice.

The prompt appendix cannot carry it alone: the appendix is fixed when the
prompt is materialized, so a session that starts before the warning point and
runs through it never gets one, and compaction drops it from the sessions that
do. Tool results reach both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import (
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


class _NoopHandler:
    def __call__(self, *_args: object, **_kwargs: object) -> ToolResult:
        return ToolResult(content=[ToolContent.text_content("ok")])


class _FakeEpochClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def _env(**overrides: str) -> dict[str, str]:
    return {CYCLE_WARN_EPOCH_ENV: str(_WARN_EPOCH), **overrides}


def test_no_notice_before_the_warning_point() -> None:
    assert cycle_deadline_notice(now_epoch=_WARN_EPOCH - 1.0, env_getter=_env().get) is None


def test_notice_names_the_submission_requirements_that_just_turned_on() -> None:
    """The notice's whole job is telling the agent what validation now demands.

    Without these section names the agent learns the rules changed but not
    what to write, and an honest report still fails validation.
    """
    notice = cycle_deadline_notice(now_epoch=_WARN_EPOCH + 240.0, env_getter=_env().get)

    assert notice is not None
    assert "## Plan Items Proven" in notice
    assert "## Incomplete Work" in notice
    assert "Reason:" in notice
    assert "Evidence:" in notice
    # The gate reads the run's clock, so staying silent cannot clear it.
    assert "not from anything you declare" in notice


def test_notice_does_not_read_as_a_countdown_on_the_agents_own_session() -> None:
    """No remaining-minutes text, no routing detail — those caused early exits.

    The cycle deadline is enforced at routing boundaries and never interrupts
    a running invocation. A countdown here gets read as the agent's own clock;
    the only stop signal is `_session_wrapup.wrapup_notice`.
    """
    notice = cycle_deadline_notice(now_epoch=_WARN_EPOCH + 240.0, env_getter=_env().get)

    assert notice is not None
    assert "does not shorten your session" in notice
    assert "session wrap-up notice" in notice
    # A countdown needs a number to count; there is none to read as a clock.
    assert not any(char.isdigit() for char in notice)
    assert "redirect" not in notice.lower()


def test_notice_text_does_not_drift_with_elapsed_time() -> None:
    """One fixed string: nothing in it varies, so nothing in it can imply urgency."""
    early = cycle_deadline_notice(now_epoch=_WARN_EPOCH, env_getter=_env().get)
    late = cycle_deadline_notice(now_epoch=_WARN_EPOCH + 10_000.0, env_getter=_env().get)

    assert early is not None
    assert early == late


def test_no_notice_without_a_published_warning_point() -> None:
    """A run with no cycle timebox must not be told about a gate that cannot fire."""
    assert cycle_deadline_notice(now_epoch=_WARN_EPOCH, env_getter={}.get) is None


def test_notifier_reads_the_published_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, str(_WARN_EPOCH))

    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH + 600.0))

    assert notifier.notice() is not None


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


def test_tool_result_carries_the_notice(tmp_path: pathlib.Path, monkeypatch: MonkeyPatch) -> None:
    """Every tool result carries it once the warning point has passed."""
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, str(_WARN_EPOCH))
    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH + 60.0))

    payload = _call_read_file(_server_with_notifier(notifier, tmp_path=tmp_path))

    assert "cycle timebox" in payload.lower()


def test_tool_result_is_clean_before_the_warning_point(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, str(_WARN_EPOCH))
    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH - 60.0))

    payload = _call_read_file(_server_with_notifier(notifier, tmp_path=tmp_path))

    assert "cycle timebox" not in payload.lower()


def test_standalone_server_wires_the_notice(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """The production composition root carries the notice, not just the tests.

    Without this the whole feature can be deleted from the standalone server
    with every other test still green.
    """
    from ralph.mcp.server.runtime import build_standalone_http_server

    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, "0")

    http_server = build_standalone_http_server(tmp_path, port=0)

    notice = http_server._mcp_server._cycle_deadline_provider()
    assert notice is not None
    assert "Cycle timebox" in notice


def test_non_finite_published_epoch_leaves_a_successful_call_intact(
    tmp_path: pathlib.Path, monkeypatch: MonkeyPatch
) -> None:
    """A junk epoch must not turn every successful tool call into an error.

    The notice is appended AFTER the tool has run, so an exception here would
    return an internal error for work whose side effects already happened.
    `nan` parses as a float, so only an explicit finiteness check stops it
    reaching the comparison.
    """
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, "nan")
    notifier = CycleDeadlineNotifier(_FakeEpochClock(_WARN_EPOCH))

    payload = _call_read_file(_server_with_notifier(notifier, tmp_path=tmp_path))

    assert "ok" in payload
    assert "cycle timebox" not in payload.lower()
