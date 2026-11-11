from __future__ import annotations

from typing import Any

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, claude_tool_name
from tests._support.typed_accessors import (
    must_dict_list,
)


class _NoopHandler:
    def __call__(
        self, session: object, workspace: object, params: dict[str, object]
    ) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}


def _build_server_with_tool(name: str = "read_file") -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name=name,
                description=f"Test tool {name}",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        _NoopHandler(),
    )
    return McpServer(
        session=object(),
        workspace=object(),
        registry=bridge,
    )


def _tools_list(server: McpServer) -> list[dict[str, object]]:
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})
    response, _ = server._handle_tools_list(request)
    assert response.result is not None
    return must_dict_list(response.result["tools"])


def test_tools_list_exposes_both_raw_and_alias_for_strict_mcp_clients() -> None:
    server = _build_server_with_tool("read_file")
    tools = _tools_list(server)
    names = {t["name"] for t in tools}
    expected_alias = claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME)
    assert "read_file" in names
    assert expected_alias in names


@pytest.mark.parametrize(
    ("invocation_name", "should_succeed"),
    [
        ("read_file", True),
        (claude_tool_name("read_file", server_name=RALPH_MCP_SERVER_NAME), True),
        (f"mcp__{RALPH_MCP_SERVER_NAME}__nonexistent_tool", False),
    ],
)
def test_tools_call_resolves_alias_and_raw_name_to_canonical_handler(
    invocation_name: str, should_succeed: bool
) -> None:
    server = _build_server_with_tool("read_file")
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": invocation_name, "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    if should_succeed:
        assert response.error is None, response.error
    else:
        assert response.error is not None


def test_idle_watchdog_does_not_fire_no_output_deadline_after_long_silence_with_active_quiet() -> (
    None
):
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.05,
        max_waiting_on_child_seconds=10.0,
        idle_poll_interval_seconds=0.001,
        drain_window_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(policy, clock)

    watchdog.record_activity()

    clock.advance(20.0)

    result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert result != WatchdogVerdict.FIRE, (
        "watchdog fired during the post-tool-result wedge; this would wedge the live run"
    )
    assert watchdog._last_fire_reason != WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_idle_watchdog_fires_no_output_deadline_after_long_silence_with_terminated_quiet() -> None:
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.05,
        max_waiting_on_child_seconds=10.0,
        idle_poll_interval_seconds=0.001,
        drain_window_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(policy, clock)

    watchdog.record_activity()
    clock.advance(20.0)

    result_active = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert result_active != WatchdogVerdict.FIRE
    assert watchdog._last_fire_reason != WatchdogFireReason.NO_OUTPUT_DEADLINE


@pytest.mark.parametrize(
    "drain_window_seconds",
    [5.0, 30.0, 60.0],
)
def test_idle_watchdog_with_active_quiet_defers_to_next_record_activity(
    drain_window_seconds: float,
) -> None:
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.05,
        max_waiting_on_child_seconds=10.0,
        idle_poll_interval_seconds=0.001,
        drain_window_seconds=drain_window_seconds,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_activity()
    clock.advance(drain_window_seconds - 1.0)
    result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert result != WatchdogVerdict.FIRE
    assert watchdog._last_fire_reason != WatchdogFireReason.NO_OUTPUT_DEADLINE

    watchdog.record_activity()
    clock.advance(0.01)
    result_after = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert result_after == WatchdogVerdict.CONTINUE
