from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.timeout_clock import FakeClock

_TICK_SECONDS = 5.0

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_tool_call_that_never_returns_is_bounded_by_the_child_wait_ceiling": (
        "replaced by test_a_command_that_never_returns_is_cut_soon_after_the_longest_command_timeout, "
        "which tightens the bound from the 30-minute child-wait ceiling to the idle deadline plus "
        "the longest command timeout, because a command blocked forever on a named pipe kept an "
        "agent alive for 30 minutes"
    ),
}


def _run(*, result_at: float | None, horizon: float) -> float | None:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=300.0),
        clock,
        corroborator=lambda: CorroborationSnapshot(
            scoped_child_active=False,
            scoped_child_count=0,
            alive_by=None,
        ),
    )
    watchdog.record_invocation_start()
    watchdog.record_tool_use_activity()
    watchdog.record_mcp_tool_call(tool_name="ctx_execute")
    while clock.monotonic() < horizon:
        clock.advance(_TICK_SECONDS)
        if result_at is not None and clock.monotonic() >= result_at:
            watchdog.record_tool_result_activity()
            result_at = None
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
        if verdict == WatchdogVerdict.FIRE:
            return clock.monotonic()
    return None


def test_a_slow_mcp_tool_call_is_not_killed_at_the_idle_deadline() -> None:
    assert _run(result_at=600.0, horizon=590.0) is None


def test_an_agent_that_goes_quiet_after_its_tool_returned_is_still_killed() -> None:
    fired_at = _run(result_at=60.0, horizon=1200.0)
    assert fired_at is not None
    assert fired_at <= 60.0 + 300.0 + 60.0


def test_a_command_that_never_returns_is_cut_soon_after_the_longest_command_timeout() -> None:
    fired_at = _run(result_at=None, horizon=4000.0)
    assert fired_at is not None
    assert fired_at <= 300.0 + 300.0 + 60.0


def test_only_command_running_mcp_tools_count_as_an_open_wait() -> None:
    from ralph.agents.idle_watchdog.command_tools import runs_a_command

    for name in ("ralph.exec", "ctx_execute", "ctx_batch_execute", "ctx_execute_file"):
        assert runs_a_command(name), name
    for name in ("ralph_submit_md_artifact", "ctx_search", "read_file", "git.status_read", ""):
        assert not runs_a_command(name), name
