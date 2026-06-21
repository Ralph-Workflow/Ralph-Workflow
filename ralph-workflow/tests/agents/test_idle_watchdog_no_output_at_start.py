"""Black-box tests for NO_OUTPUT_AT_START watchdog fire path."""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock


def _make_watchdog(
    idle_timeout: float | None,
    no_output_at_start_seconds: float | None = 60.0,
    start: float = 0.0,
    **kwargs: object,
) -> tuple[IdleWatchdog, FakeClock]:
    max_waiting_on_child_seconds = kwargs.pop("max_waiting_on_child_seconds", 1800.0)
    max_waiting_on_child_no_progress_seconds = kwargs.pop(
        "max_waiting_on_child_no_progress_seconds", 600.0
    )
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        no_output_at_start_seconds=no_output_at_start_seconds,
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=max_waiting_on_child_no_progress_seconds,
        **kwargs,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


def _no_activity_corroborator() -> WaitingCorroborator:
    def mock() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=True,
            oldest_child_seconds=0.0,
        )

    return mock


class TestNoOutputAtStart:
    """Tests for NO_OUTPUT_AT_START fire path."""

    def test_fires_after_no_output_at_start_seconds_with_zero_activity(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_does_not_fire_when_record_activity_called(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()
        watchdog.record_activity()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START

    def test_does_not_fire_when_opted_out(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=None,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_does_not_fire_before_threshold(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(59)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_does_not_fire_with_fresh_channel_evidence(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
            activity_evidence_ttl_seconds=100.0,
        )
        watchdog.record_invocation_start()
        watchdog.record_mcp_tool_call()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_fires_before_children_persist_too_long(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_no_progress_seconds=10000.0,
            max_waiting_on_child_seconds=20000.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_defers_in_waiting_on_child_state(self) -> None:
        """When the execution strategy reports WAITING_ON_CHILD, the 30s/60s
        NO_OUTPUT_AT_START short kill is deferred so a legitimately-starting
        agent that just dispatched a subagent is not killed.

        The cumulative CHILDREN_PERSIST_TOO_LONG ceiling (default 1800s in
        this test's config) remains the upper bound for live-child stalls.
        """
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None

    def test_does_not_fire_in_active_state_with_recorded_activity(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()
        watchdog.record_activity()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_fire_sets_last_fire_reason(self) -> None:
        watchdog, clock = _make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START
