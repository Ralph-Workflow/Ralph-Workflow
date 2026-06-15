"""Tests for the fast no-progress quiet watchdog fire path."""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    AliveBy,
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
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
