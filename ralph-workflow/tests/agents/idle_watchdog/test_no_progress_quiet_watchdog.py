"""Tests for the fast no-progress quiet watchdog fire path."""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    AliveBy,
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock


def test_watchdog_fires_no_progress_quiet_on_prompt_signature() -> None:
    """Watchdog fires NO_PROGRESS_QUIET on stale-progress descendants & idle stdout."""
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        no_progress_quiet_seconds=10.0,
        suspect_waiting_on_child_seconds=None,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=12.0,
        )

    watchdog = IdleWatchdog(policy, clock, corroborator=_corroborator)
    watchdog.record_invocation_start()

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    # At start, not enough time elapsed
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE

    # Advance clock past 10s
    clock.advance(12.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET


def test_watchdog_does_not_fire_no_progress_quiet_when_post_tool_result_fresh() -> None:
    """Watchdog does not fire NO_PROGRESS_QUIET when tool results or activity is fresh."""
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        no_progress_quiet_seconds=10.0,
        activity_evidence_ttl_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
        )

    watchdog = IdleWatchdog(policy, clock, corroborator=_corroborator)
    watchdog.record_invocation_start()

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    clock.advance(12.0)

    # Record post-tool-result activity at current time (12s)
    watchdog.record_tool_result_activity()

    # Evaluate: should not fire because tool result activity resets idle baseline
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE


def test_no_progress_quiet_diagnostic_payload_contains_required_fields() -> None:
    """NO_PROGRESS_QUIET HARD_STOP diagnostic contains operator-facing fields.

    Verifies that when NO_PROGRESS_QUIET fires, the emitted WaitingStatusEvent
    carries the required diagnostic fields: invocation_elapsed, idle_elapsed,
    alive_by, ceiling, effective_ceiling, and per-channel evidence summary.
    """
    clock = FakeClock()
    policy = TimeoutPolicy(
        idle_timeout_seconds=300.0,
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        no_progress_quiet_seconds=10.0,
        activity_evidence_ttl_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
    )

    captured_events: list[WaitingStatusEvent] = []

    def listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=12.0,
        )

    watchdog = IdleWatchdog(policy, clock, listener=listener, corroborator=_corroborator)
    watchdog.record_invocation_start()

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    clock.advance(12.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET

    assert len(captured_events) == 1
    evt = captured_events[0]
    assert evt.kind.value == "hard_stop"

    diag = evt.diagnostic
    assert "invocation_elapsed" in diag, "diagnostic must contain invocation_elapsed"
    assert "idle_elapsed" in diag, "diagnostic must contain idle_elapsed"
    assert "alive_by" in diag, "diagnostic must contain alive_by"
    assert diag["alive_by"] == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS.value
    assert "ceiling" in diag, "diagnostic must contain ceiling"
    assert diag["ceiling"] == 10.0
    assert "effective_ceiling" in diag, "diagnostic must contain effective_ceiling"
    assert diag["effective_ceiling"] == "no_progress_quiet"
    assert "evidence_summary" in diag, "diagnostic must contain evidence_summary"
