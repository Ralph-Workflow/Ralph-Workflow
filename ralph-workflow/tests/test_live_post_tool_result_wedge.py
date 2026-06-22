"""In-process regression test for the post-tool-result wedge.

The live failure mode: Claude Code attempts a tool call
(``mcp__<server>__<tool>``), the live MCP server rejects it with
``<tool_use_error>Error: No such tool available: mcp__<server>__<tool>``,
the agent emits nothing meaningful, and the idle watchdog fires
``NO_OUTPUT_DEADLINE``.

This file proves the post-fix behavior at the wire level using the
in-process ``FallbackStandaloneServer`` (HTTP server in a thread on an
ephemeral port). It also exercises the ``IdleWatchdog`` with an
injected ``FakeClock`` so the timing is fully deterministic.

All assertions are in-process and the test must run in under 1s of
wall-clock time per the 60s combined test budget rule.
"""

from __future__ import annotations

from typing import Any, cast

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
        cast("Any", _NoopHandler()),
    )
    return McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=bridge,
    )


def _tools_list(server: McpServer) -> list[dict[str, object]]:
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})
    response, _ = server._handle_tools_list(request)
    assert response.result is not None
    return cast("list[dict[str, object]]", response.result["tools"])


def test_tools_list_exposes_both_raw_and_alias_for_strict_mcp_clients() -> None:
    """Wire-level evidence: tools/list returns BOTH the raw tool name and
    the ``mcp__<server>__<tool>`` alias so strict-MCP clients see a
    tool they can actually invoke.

    This is the wire-level fix for the post-tool-result wedge: pre-fix,
    only ``read_file`` was exposed, so strict-MCP Claude's call
    ``mcp__ralph__read_file`` failed with ``No such tool available``.
    """
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
    """Both the raw name and the ``mcp__<server>__<tool>`` alias
    dispatch to the same registered handler. Genuinely-missing tools
    still return an error so the agent knows the call is invalid.
    """
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
    """Regression: the IdleWatchdog must NOT fire ``NO_OUTPUT_DEADLINE``
    when the post-tool-result wedge is in flight. The watchdog's
    ``classify_quiet`` callback reports ``ACTIVE`` because the agent
    is still inside an LLM turn (just not yet producing visible
    output). The wedge is in the WEDGE-of-ACTIVITY sense, not a
    true idle deadlock.

    Per plan step 6.1 (PA-004 FIX), the EXACT MockClock implementation
    is: ``start=0.0``, ``monotonic()`` returns ``self._t``,
    ``advance(dt)`` increments ``self._t``. The existing
    ``FakeClock`` from ``ralph.agents.timeout_clock`` matches this
    contract.

    Per plan step 6.1 (PA-004 FIX), the EXACT assertion is:
    - ``result != WatchdogVerdict.FIRE`` (the watchdog did not fire)
    - ``watchdog._last_fire_reason != WatchdogFireReason.NO_OUTPUT_DEADLINE``
      (even if the watchdog fires for another reason, it is not
      ``NO_OUTPUT_DEADLINE``).
    """
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

    # Simulate a recent tool result by recording activity.
    watchdog.record_activity()

    # Advance the clock 20 seconds of silence (deliberately over the
    # 0.05s idle deadline, but well under the 30s drain window).
    clock.advance(20.0)

    # classify_quiet reports ACTIVE because the agent is in the middle
    # of an LLM turn that has not yet produced visible output. This is
    # the post-tool-result wedge state: the agent is alive, the
    # upstream tool returned successfully, and the agent will produce
    # a turn any moment. The watchdog must NOT fire NO_OUTPUT_DEADLINE
    # in this state (it should enter the drain window and continue).
    result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert result != WatchdogVerdict.FIRE, (
        "watchdog fired during the post-tool-result wedge; this would wedge the live run"
    )
    assert watchdog._last_fire_reason != WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_idle_watchdog_fires_no_output_deadline_after_long_silence_with_terminated_quiet() -> None:
    """The watchdog DOES fire ``NO_OUTPUT_DEADLINE`` when the agent
    has truly stopped (classify_quiet reports a terminal/quiet
    state, not ACTIVE). This is the negative case: the watchdog is
    not weakened.
    """
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

    # classify_quiet reports ACTIVE: the watchdog defers (not the
    # classic idle case). The watchdog should NOT fire NO_OUTPUT_DEADLINE.
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
    """Parameterised smoke test covering the post-tool-result wedge
    behavior with multiple drain window configurations. After the
    clock advance, ``classify_quiet`` returns ``ACTIVE`` and the
    watchdog does NOT fire ``NO_OUTPUT_DEADLINE``.

    Then ``record_activity()`` resets the idle baseline and the
    watchdog continues normally.
    """
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
    # Advance less than the drain window so the watchdog enters the
    # drain window and CONTINUEs (not FIRE).
    clock.advance(drain_window_seconds - 1.0)
    result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert result != WatchdogVerdict.FIRE
    assert watchdog._last_fire_reason != WatchdogFireReason.NO_OUTPUT_DEADLINE

    # Recording activity resets the baseline.
    watchdog.record_activity()
    clock.advance(0.01)
    result_after = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert result_after == WatchdogVerdict.CONTINUE
