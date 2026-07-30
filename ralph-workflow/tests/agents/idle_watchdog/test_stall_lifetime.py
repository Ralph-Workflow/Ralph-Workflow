"""Regression coverage for watchdog-owned stall lifetime (S-1)."""

from __future__ import annotations

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
)
from ralph.agents.timeout_clock import FakeClock


def _watchdog(
    clock: FakeClock,
    events: list[WaitingStatusEvent],
) -> IdleWatchdog:
    return IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=60.0),
        clock,
        listener=events.append,
    )


def test_stall_lifetime_regression_invocation_end_clears_active_stall() -> None:
    """S-1: ending the stalled invocation publishes one authoritative clear."""
    events: list[WaitingStatusEvent] = []
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock, events)
    watchdog.record_invocation_start()
    watchdog._set_stall(active=True, now=1.0, idle_elapsed=1.0)

    clock.advance(2.0)
    watchdog.record_invocation_end()

    assert [event.kind for event in events] == [
        WaitingStatusKind.STALLED,
        WaitingStatusKind.STALL_RESUMED,
    ]
    assert events[-1].stall_active is False


def test_stall_assessment_regression_fresh_watchdog_event_reports_not_stalled() -> None:
    """S-1: a fresh watchdog event re-synchronizes a previously latched host."""
    events: list[WaitingStatusEvent] = []
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock, events)

    watchdog._emit(WaitingStatusKind.PROGRESS, current_run_seconds=0.0, idle_elapsed=0.0)

    assert events[-1].stall_active is False
