"""Consolidated tests from test_idle_watchdog_*.py.

This module merges the following previously split test modules into a single
file to reduce per-shard collection cost. The original class names are
preserved so external references (test::TestX) still resolve.

Source files:
  - test_idle_watchdog_1.py
  - test_idle_watchdog_2.py
  - test_idle_watchdog_3.py
  - test_idle_watchdog_4.py
  - test_idle_watchdog_no_output_at_start.py
  - test_idle_watchdog_no_output_at_start_lifecycle.py
  - test_idle_watchdog_workspace_smart_filter.py
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import (
    Any,
)
from unittest.mock import patch

import pytest
from loguru import (
    logger,
)
from loguru import (
    logger as loguru_logger,
)

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    ChannelEvidenceSummary,
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WaitingStatusEvent,
    WaitingStatusKind,
    WaitingStatusListener,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._evidence_tier import (
    ChannelName,
    EvidenceSummary,
)
from ralph.agents.idle_watchdog._workspace_change_kind import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeKind,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    _normalize_workspace_change_weights,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy
from ralph.process.teardown import teardown_subtree

ACTIVITY_TTL = 30.0

DRAIN_WINDOW = 0.0

IDLE_TIMEOUT = 0.1

MAX_WAITING = 10.0

_ACTIVITY_TTL = 30.0

_DRAIN_WINDOW = 0.0

_EXPECTED_PROGRESS_COUNT = 2

_FULL_CEILING = 100.0

_HARD_STOP_MAX_WAITING = 10.0

_HARD_STOP_OLDEST_CHILD_SECS = 42.5

_IDLE_TIMEOUT = 0.1

_MAX_WAITING = 10.0

_NO_PROGRESS_CEILING = 10.0

_WS_ENTRY_COUNT = 5

_WS_EXPECTED_DELTA = 4

_WS_FINAL_COUNT = 9




# === Helper for test_idle_watchdog_1.py ===
def _idle_watchdog_1_make_watchdog(
    idle_timeout: float | None,
    drain_window: float = 0.5,
    max_waiting: float | None = None,
    start: float = 0.0,
    max_session: float | None = None,
    listener: WaitingStatusListener | None = None,
    suspect: float | None = None,
    status_interval: float | None = None,
    no_progress_ceiling: float | None = None,
    corroborator: WaitingCorroborator | None = None,
    **kwargs: object,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=suspect,
        waiting_status_interval_seconds=status_interval if status_interval is not None else 30.0,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock, listener, corroborator=corroborator), clock


# === Helper for test_idle_watchdog_1.py ===
def _idle_watchdog_1_make_watchdog_with_listener(
    idle_timeout: float | None,
    max_waiting: float | None = None,
    status_interval: float | None = None,
    suspect: float | None = None,
    corroborator: WaitingCorroborator | None = None,
) -> tuple[IdleWatchdog, FakeClock, list[WaitingStatusEvent]]:
    events: list[WaitingStatusEvent] = []
    watchdog, clock = _idle_watchdog_1_make_watchdog(
        idle_timeout=idle_timeout,
        max_waiting=max_waiting,
        status_interval=status_interval,
        suspect=suspect,
        listener=events.append,
        corroborator=corroborator,
    )
    return watchdog, clock, events


# === Helper for test_idle_watchdog_1.py ===
def _idle_watchdog_1_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_idle_watchdog_1.py ===
def _idle_watchdog_1_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_idle_watchdog_2.py ===
def _idle_watchdog_2_make_watchdog(
    idle_timeout: float | None,
    drain_window: float = 0.5,
    max_waiting: float | None = None,
    start: float = 0.0,
    max_session: float | None = None,
    listener: WaitingStatusListener | None = None,
    suspect: float | None = None,
    status_interval: float | None = None,
    no_progress_ceiling: float | None = None,
    corroborator: WaitingCorroborator | None = None,
    **kwargs: object,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=suspect,
        waiting_status_interval_seconds=status_interval if status_interval is not None else 30.0,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock, listener, corroborator=corroborator), clock


# === Helper for test_idle_watchdog_2.py ===
def _idle_watchdog_2_make_watchdog_with_listener(
    idle_timeout: float | None,
    max_waiting: float | None = None,
    status_interval: float | None = None,
    suspect: float | None = None,
    corroborator: WaitingCorroborator | None = None,
) -> tuple[IdleWatchdog, FakeClock, list[WaitingStatusEvent]]:
    events: list[WaitingStatusEvent] = []
    watchdog, clock = _idle_watchdog_2_make_watchdog(
        idle_timeout=idle_timeout,
        max_waiting=max_waiting,
        status_interval=status_interval,
        suspect=suspect,
        listener=events.append,
        corroborator=corroborator,
    )
    return watchdog, clock, events


# === Helper for test_idle_watchdog_2.py ===
def _idle_watchdog_2_active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


# === Helper for test_idle_watchdog_2.py ===
def _idle_watchdog_2_waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


# === Helper for test_idle_watchdog_3.py ===
def _idle_watchdog_3_make_watchdog(
    *,
    idle_timeout: float = _IDLE_TIMEOUT,
    drain_window: float = _DRAIN_WINDOW,
    max_waiting: float = _MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = _ACTIVITY_TTL,
    start: float = 0.0,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
    silent_subagent_seconds: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    kwargs: dict[str, Any] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": suspect,
        "max_waiting_on_child_no_progress_seconds": no_progress_ceiling,
        "stuck_job_sub_ceiling_seconds": None,
        "os_descendant_only_ceiling_seconds": None,
        # Disable the SILENT_SUBAGENT diagnostic by default so this
        # file exercises the activity-aware fire path (NO_OUTPUT_DEADLINE
        # etc.) rather than the SILENT_SUBAGENT classifier branch.
        # Tests that explicitly exercise SILENT_SUBAGENT are in
        # ``tests/agents/idle_watchdog/test_silent_subagent_runtime.py``.
        "silent_subagent_seconds": silent_subagent_seconds,
    }
    if activity_ttl is not None:
        kwargs["activity_evidence_ttl_seconds"] = activity_ttl
    config = TimeoutPolicy(**kwargs)
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


# === Helper for test_idle_watchdog_3.py ===
def _idle_watchdog_3_default_classifier() -> WorkspaceChangeClassifier:
    """Return the conservative default classifier used in production."""
    return WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))


# === Helper for test_idle_watchdog_4.py ===
def _idle_watchdog_4_make_watchdog(
    *,
    idle_timeout: float = IDLE_TIMEOUT,
    drain_window: float = DRAIN_WINDOW,
    max_waiting: float = MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = ACTIVITY_TTL,
    start: float = 0.0,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=suspect,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        activity_evidence_ttl_seconds=activity_ttl,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


# === Helper for test_idle_watchdog_4.py ===
def _idle_watchdog_4_default_classifier() -> WorkspaceChangeClassifier:
    """Return the conservative default classifier used in production."""
    return WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))


# === Helper for test_idle_watchdog_no_output_at_start.py ===
def _idle_watchdog_no_output_at_sta_make_watchdog(
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


# === Helper for test_idle_watchdog_workspace_smart_filter.py ===
def _idle_watchdog_workspace_smart__make_watchdog(
    *,
    idle_timeout: float = _IDLE_TIMEOUT,
    drain_window: float = _DRAIN_WINDOW,
    max_waiting: float = _MAX_WAITING,
    activity_ttl: float | None = _ACTIVITY_TTL,
    workspace_change_weights: dict[str, float] | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        # Disable suspicion (the default suspect=600s is greater
        # than the small max_waiting=10s used in these tests).
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        activity_evidence_ttl_seconds=activity_ttl,
        workspace_change_weights=workspace_change_weights,
        # Disable the OS-descendant-only ceiling (its default is larger
        # than the small max_waiting=10s used in these tests).
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock()
    return IdleWatchdog(config, clock), clock


# === Helper: _make_watchdog_with_no_progress_ceiling (from test_idle_watchdog_2.py) ===
def _make_watchdog_with_no_progress_ceiling(
    no_progress_ceiling: float | None,
    full_ceiling: float = _FULL_CEILING,
) -> tuple[IdleWatchdog, FakeClock]:
    return _idle_watchdog_2_make_watchdog(
        idle_timeout=1.0,
        max_waiting=full_ceiling,
        no_progress_ceiling=no_progress_ceiling,
    )


# === Helper: _active_classifier (from test_idle_watchdog_3.py) ===
def _active_classifier() -> Callable[[], AgentExecutionState]:
    return lambda: AgentExecutionState.ACTIVE


# === Helper: _waiting_classifier (from test_idle_watchdog_3.py) ===
def _waiting_classifier() -> Callable[[], AgentExecutionState]:
    return lambda: AgentExecutionState.WAITING_ON_CHILD


# === Helper: _no_activity_corroborator (from test_idle_watchdog_no_output_at_start.py) ===
def _no_activity_corroborator() -> WaitingCorroborator:
    def mock() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=None,
            scoped_child_active=True,
            oldest_child_seconds=0.0,
        )

    return mock


# === Helper: _make_production_monitor (from test_idle_watchdog_workspace_smart_filter.py) ===
def _make_production_monitor(
    watchdog: IdleWatchdog,
    tmp_path: Path,
    *,
    weights: dict[str, float] | None = None,
) -> WorkspaceMonitor:
    """Construct a WorkspaceMonitor with the production-style 2-arg
    lambda binding and a real ``WorkspaceChangeClassifier``."""
    effective_weights = _normalize_workspace_change_weights(weights)
    return WorkspaceMonitor(
        tmp_path,
        on_event=lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight),
        classifier=WorkspaceChangeClassifier(weights=effective_weights),
    )


# === consolidated from test_idle_watchdog_1.py ===
def test_disabled_when_idle_timeout_is_none() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=None)
    clock.advance(1_000_000)
    assert watchdog.evaluate(classify_quiet=_idle_watchdog_1_active) == WatchdogVerdict.CONTINUE
    assert watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting) == WatchdogVerdict.CONTINUE


# === consolidated from test_idle_watchdog_1.py ===
def test_continues_before_deadline() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10)
    clock.advance(9.9)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE


# === consolidated from test_idle_watchdog_1.py ===
def test_enters_drain_window_at_deadline_when_active() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, drain_window=0.5)

    # At deadline, classify_quiet=ACTIVE -> enter drain window
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE  # drain window entered

    # Still inside drain window
    clock.advance(0.4)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE

    # Drain window exhausted
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_1.py ===
def test_drain_window_aborted_by_late_activity() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, drain_window=0.5)

    # Enter drain window
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE

    # Activity arrives during drain
    clock.advance(0.2)
    watchdog.record_activity()

    # Should continue without firing; advance well under idle timeout
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_on_child_defers_without_resetting_activity() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, max_waiting=1800.0)

    # Past idle deadline with children present
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # More time passes; children gone but no new output
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    # Still past idle deadline (16s > 10s), no new activity -> drain window opens
    assert result == WatchdogVerdict.CONTINUE

    # Drain exhausted
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_on_child_hard_ceiling_fires() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, max_waiting=20.0, drain_window=0.0)

    # Advance past idle deadline
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance in a single jump past the hard ceiling. The watchdog
    # fires CHILDREN_PERSIST_TOO_LONG (the cumulative ceiling is the
    # absolute reason under the smart-verdict gate when the
    # corroboration does not see a live subagent).
    clock.advance(20.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # A subsequent evaluate with classify_quiet=ACTIVE exits the
    # waiting branch and fires via the active path (NO_OUTPUT_DEADLINE).
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_1.py ===
def test_record_activity_clears_drain_state() -> None:
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, drain_window=0.5)

    # Enter drain window
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)

    # Reset by activity
    watchdog.record_activity()

    # Advance less than idle timeout from the activity point
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE


# === consolidated from test_idle_watchdog_1.py ===
def test_validation_rejects_zero_idle_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        TimeoutPolicy(idle_timeout_seconds=0)


# === consolidated from test_idle_watchdog_1.py ===
def test_validation_rejects_negative_drain_window() -> None:
    with pytest.raises(ValueError, match=">="):
        TimeoutPolicy(idle_timeout_seconds=10, drain_window_seconds=-0.1)


# === consolidated from test_idle_watchdog_1.py ===
def test_validation_rejects_max_waiting_less_than_idle() -> None:
    with pytest.raises(ValueError, match="max_waiting_on_child_seconds"):
        TimeoutPolicy(
            idle_timeout_seconds=100,
            max_waiting_on_child_seconds=50,
            # Disable no-progress ceiling to avoid conflict with 600.0 default
            max_waiting_on_child_no_progress_seconds=None,
        )


# === consolidated from test_idle_watchdog_1.py ===
def test_session_ceiling_validation_rejects_value_lower_than_idle_timeout() -> None:
    """TimeoutPolicy rejects max_session_seconds < idle_timeout_seconds."""
    with pytest.raises(ValueError, match="max_session_seconds"):
        TimeoutPolicy(idle_timeout_seconds=100, max_session_seconds=50)


# === consolidated from test_idle_watchdog_1.py ===
def test_session_ceiling_fires_despite_heartbeats() -> None:
    """Session ceiling fires even when record_activity() is called continuously.

    This tests that the session ceiling cannot be defeated by heartbeat activity —
    a process that produces output continuously must still be killed when the
    absolute session wall-clock ceiling is reached.
    """
    max_session = 30.0
    watchdog, clock = _idle_watchdog_1_make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=max_session
    )

    # Simulate continuous heartbeat activity every second for 29s — no fire yet.
    for _ in range(29):
        clock.advance(1.0)
        watchdog.record_activity()
        result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
        assert result == WatchdogVerdict.CONTINUE, f"Expected CONTINUE at t={clock.monotonic()}"

    # At t=30s the session ceiling is reached — FIRE regardless of recent activity.
    clock.advance(1.0)
    watchdog.record_activity()  # heartbeat fires just before evaluation
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_on_child_cumulative_survives_active_oscillation() -> None:
    """Cumulative WAITING time is preserved across WAITING->ACTIVE->WAITING oscillation.

    This tests the false-negative fix: a process that alternates between producing
    output (WAITING->ACTIVE) and waiting on children cannot defeat the
    max_waiting_on_child_seconds ceiling by resetting the counter on each active interval.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, drain_window=0.5, max_waiting=20.0)

    # (a) Advance 11s -> past idle deadline. classify=WAITING_ON_CHILD -> start run1.
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # (b) Advance 5s -> still past deadline. classify=ACTIVE -> accumulate run1 (5s), enter drain.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered
    # At this point cumulative should have 5s from run1.
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(5.0, abs=0.01)

    # (c) Advance 0.1s -> still in drain window. classify=WAITING -> abandon drain, start run2.
    clock.advance(0.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # drain abandoned

    # (d) Advance 6s -> run2 elapsed 6s, cumulative_candidate = 5 + 6 = 11s < 20 ceiling.
    clock.advance(6.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # (e) Advance 10s -> run2 elapsed 16s, cumulative_candidate = 5 + 16 = 21s >= 20 ceiling.
    # The watchdog fires CHILDREN_PERSIST_TOO_LONG. The cumulative
    # counter was 5.0 before the fire (run1); the fire reason is
    # driven by the candidate_total (21.0) at the moment of the
    # fire, and the cumulative counter is preserved across the
    # session.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    # Cumulative ceiling is preserved at the previous run's 5.0
    # (the candidate_total 21.0 drives the fire decision; the
    # cumulative counter itself only advances on the next
    # transition out of the waiting branch).
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(5.0, abs=0.01)


# === consolidated from test_idle_watchdog_1.py ===
def test_consecutive_waiting_does_not_double_count() -> None:
    """Consecutive WAITING evaluations must not double-count the same elapsed time."""
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, max_waiting=100.0)

    # Past idle deadline
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # starts run at t=11

    clock.advance(5.0)  # t=16
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # 5s elapsed in run

    clock.advance(5.0)  # t=21
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # 10s elapsed in run

    # cumulative is still 0 (only added on transition out of WAITING)
    # candidate_total should be 10s, well under ceiling
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


# === consolidated from test_idle_watchdog_1.py ===
def test_drain_window_defers_when_children_reappear() -> None:
    """When children appear during the drain window, drain is abandoned and WAITING resumes.

    This tests the false-positive fix: children that appear during the drain window
    must prevent the timeout from firing.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, drain_window=0.5, max_waiting=1800.0)

    # (a) Advance 10s -> at idle deadline. classify=ACTIVE -> enter drain window.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered
    assert watchdog._in_drain_window is True

    # (b) Advance 0.2s -> inside drain window. classify=WAITING -> abandon drain.
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # not FIRE
    assert watchdog._in_drain_window is False  # drain abandoned

    # (c) Advance 5s -> back to ACTIVE -> re-enter drain.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE  # new drain entered

    # (d) Advance 0.6s -> drain exhausted (0.5s + 0.1s overshoot). Fires NO_OUTPUT_DEADLINE.
    clock.advance(0.6)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_1.py ===
def test_logger_emits_warning_on_fire_with_reason() -> None:
    """FIRE verdict emits a loguru WARNING with the fire reason."""
    captured_messages: list[str] = []

    def _sink(message: object) -> None:
        captured_messages.append(str(message))

    sink_id = logger.add(
        _sink,
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "idle_watchdog",
    )
    try:
        watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10, drain_window=0.0, max_waiting=1800.0)

        # Advance past idle deadline and fire immediately (drain_window=0)
        clock.advance(10.0)
        result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
        assert result == WatchdogVerdict.FIRE  # drain_window=0 fires immediately
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    finally:
        logger.remove(sink_id)

    assert any("FIRE" in msg or "no_output_deadline" in msg for msg in captured_messages), (
        f"Expected WARNING with fire reason, got: {captured_messages}"
    )


# === consolidated from test_idle_watchdog_1.py ===
def test_session_ceiling_fires_even_if_waiting_on_child() -> None:
    """Session ceiling fires even when classify_quiet=WAITING_ON_CHILD.

    Proves ceiling outranks WAITING deferral — max_session takes precedence
    over any child-wait state.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=20.0
    )

    # Advance to t=21 (past max_session=20) with children present.
    clock.advance(21.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_idle_watchdog_1.py ===
def test_session_ceiling_fires_during_drain_window() -> None:
    """Session ceiling fires during drain window (ceiling outranks drain deferral).

    When max_session elapses while in the drain window, SESSION_CEILING_EXCEEDED
    fires regardless of drain state.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(
        idle_timeout=10.0, drain_window=2.0, max_waiting=1800.0, max_session=15.0
    )

    # Enter drain window at t=10.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered

    # Advance to t=15 (session ceiling hit during drain).
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_idle_watchdog_1.py ===
def test_drain_window_zero_fires_immediately_with_active_classification() -> None:
    """drain_window=0 with ACTIVE classification fires immediately at idle deadline.

    Regression for the active-branch zero-drain shortcut where drain_window=0
    must fire immediately without entering a non-existent drain window.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, drain_window=0.0, max_waiting=1800.0)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_1.py ===
def test_record_activity_during_waiting_does_not_reset_cumulative() -> None:
    """record_activity() preserves cumulative_waiting_on_child_seconds.

    Cumulative is an absolute ceiling that survives heartbeats. Heartbeat activity
    alone cannot defeat the max_waiting_on_child_seconds ceiling.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, max_waiting=20.0)

    # 4s WAITING run starting at t=11.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # enters WAITING at t=11

    # Heartbeat at t=15: accumulates 4s to cumulative, does NOT reset to 0.
    clock.advance(4.0)
    watchdog.record_activity()

    # Cumulative preserved at 4s (not reset by record_activity).
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Advance past idle deadline again (11s from last activity at t=15).
    clock.advance(11.0)  # t=26, idle_elapsed=11
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # enters new WAITING run at t=26
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # candidate=4+0=4 < 20

    # Advance 11s within the second WAITING run: candidate = 4 + 11 = 15 < 20.
    clock.advance(11.0)  # t=37
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)


# === consolidated from test_idle_watchdog_1.py ===
def test_record_activity_during_waiting_cumulative_causes_fire() -> None:
    """Preserved cumulative causes CHILDREN_PERSIST_TOO_LONG when ceiling reached.

    When max_waiting_on_child_seconds is small enough, the preserved 4s from the
    first WAITING run causes the second run to fire the ceiling.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, max_waiting=10.0)

    # 4s WAITING run starting at t=11.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # enters WAITING at t=11

    # Heartbeat at t=15: cumulative = 4.0.
    clock.advance(4.0)
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Advance past idle deadline; enters new WAITING run at t=26.
    clock.advance(11.0)  # t=26, idle_elapsed=11
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # candidate=4+0=4 < 10
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance 7s in the second run: candidate = 4 + 7 = 11 >= 10 -> FIRE.
    clock.advance(7.0)  # t=33
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_1.py ===
def test_cumulative_does_not_decay_under_long_active_period() -> None:
    """Cumulative is absolute across the session: any duration of in-deadline ACTIVE
    evaluations does NOT reset it. This guards against the previous bug where a single
    heartbeat plus drain_window quiet wiped the ceiling.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=20.0)

    # 4s WAITING run starting at t=11.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    clock.advance(4.0)  # t=15
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Stay within idle deadline for > drain_window_seconds (0.5) without WAITING.
    clock.advance(0.6)  # t=15.6, idle_elapsed=0.6 < 10
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE

    # Cumulative must NOT decay; it remains at 4s.
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Sustained active period — still no decay.
    clock.advance(5.0)  # t=20.6, idle_elapsed=5.6 < 10
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # A fresh WAITING run starts with cumulative=4, not 0.
    # 4 + 17 = 21 >= 20 -> FIRE proves cumulative was never reset.
    clock.advance(11.0)  # t=31.6, idle_elapsed=11 -> past idle deadline
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # new WAITING run starts
    clock.advance(17.0)  # run elapsed=17; candidate=4+17=21>=20
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_ceiling_survives_intermittent_heartbeats() -> None:
    """Cumulative WAITING ceiling fires despite intermittent heartbeat activity.

    Previously record_activity() reset cumulative to 0, so a child that emits one
    heartbeat just after the idle deadline could defeat max_waiting_on_child_seconds
    forever. The fix preserves cumulative across heartbeats.
    """
    idle_timeout = 10.0
    max_waiting = 20.0
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=idle_timeout, max_waiting=max_waiting)

    last_result = WatchdogVerdict.WAITING_ON_CHILD
    for _ in range(100):  # upper bound; test fails if FIRE never arrives
        clock.advance(idle_timeout + 1.0)  # advance past idle deadline
        result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
        if result == WatchdogVerdict.FIRE:
            last_result = result
            break
        assert result == WatchdogVerdict.WAITING_ON_CHILD
        clock.advance(0.5)  # heartbeat arrives shortly after
        watchdog.record_activity()

    assert last_result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_1.py ===
def test_cumulative_ceiling_fires_with_heartbeat_then_long_quiet() -> None:
    """Black-box regression: heartbeat + quiet > drain_window must NOT reset cumulative.

    Mirrors the user's log pattern where cumulative_candidate reached 1444s but
    the ceiling at 1800s was never triggered because every record_activity() +
    2s-quiet wiped the cumulative counter.

    Pattern:
      - t=11: past idle deadline, enter WAITING run1
      - t=19: record_activity (heartbeat at t=19 accumulates 8s -> cumulative=8)
      - t=21: 2s quiet > drain_window (0.5) — previously this zeroed cumulative
      - evaluate(ACTIVE) -> CONTINUE; with the BUG cumulative becomes 0
      - t=32: new idle deadline passed, enter WAITING run2
      - t=45: run2 elapsed=13; with BUG candidate=0+13=13<20 (no fire);
              without BUG candidate=8+13=21>=20 -> FIRE
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=20.0)

    # t=11: past idle deadline
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # t=19: still in run1 (8s elapsed)
    clock.advance(8.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Heartbeat: record_activity accumulates run1 (8s) -> cumulative=8
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(8.0, abs=0.01)

    # t=21: 2s quiet > drain_window=0.5; previously this would zero cumulative
    clock.advance(2.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE

    # cumulative must remain 8, not be reset to 0
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(8.0, abs=0.01)

    # t=32: past idle deadline again (11s from record_activity at t=19)
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # new WAITING run2 starts
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # t=45: run2 elapsed=13; candidate=8+13=21>=20 -> FIRE
    clock.advance(13.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_1.py ===
def test_session_ceiling_precedence_over_waiting_branch() -> None:
    """SESSION_CEILING_EXCEEDED takes precedence over CHILDREN_PERSIST_TOO_LONG.

    When both session and cumulative-waiting ceilings are exceeded simultaneously,
    the session ceiling fires first because it is checked first in evaluate().
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(
        idle_timeout=10.0,
        max_waiting=1800.0,
        max_session=15.0,
    )

    # t=11: past idle deadline; children present.
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # t=16: past session ceiling (15s) AND still past idle deadline with children.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_idle_watchdog_1.py ===
def test_evaluate_is_idempotent_when_clock_does_not_advance() -> None:
    """Two consecutive evaluate() calls with no clock advance return CONTINUE.

    Regression for double-counting: calling evaluate twice at the same clock time
    must not accumulate extra waiting time.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, max_waiting=20.0)

    # Advance to deadline and into WAITING.
    clock.advance(11.0)
    result1 = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result1 == WatchdogVerdict.WAITING_ON_CHILD

    # Call evaluate again with no clock advance.
    result2 = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result2 == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


# === consolidated from test_idle_watchdog_1.py ===
def test_consecutive_active_in_drain_does_not_extend_window() -> None:
    """evaluate(ACTIVE) called twice with small clock advances does not extend drain window.

    The drain window has a fixed duration measured from when it starts. Calling
    evaluate(ACTIVE) multiple times during the drain must not reset or extend the
    window — it should fire after drain_window_seconds regardless.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0)

    # Enter drain at t=10.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE

    # Two evaluate calls with small advances totalling 0.4s — still in drain.
    clock.advance(0.2)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.CONTINUE

    # One more 0.2s advance = 0.6s > 0.5s drain -> FIRE.
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_emits_entered_event_once_per_run() -> None:
    """Exactly one ENTERED event per WAITING_ON_CHILD run, regardless of tick count."""
    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=1000.0, status_interval=100.0, suspect=None
    )
    clock.advance(1.1)  # past idle deadline
    for _ in range(5):
        result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
        assert result == WatchdogVerdict.WAITING_ON_CHILD
        clock.advance(0.01)
    entered = [e for e in events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered) == 1


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_progress_throttled_to_interval() -> None:
    """PROGRESS events fire at ~status_interval cadence, not every tick."""
    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=1000.0, status_interval=10.0, suspect=None
    )
    clock.advance(1.1)  # past idle deadline
    # Drive 25 ticks with 1s gap each (total 25s fake time)
    for _ in range(25):
        watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
        clock.advance(1.0)
    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    # Should have fired at ~10s and ~20s — exactly 2 PROGRESS events
    assert len(progress) == _EXPECTED_PROGRESS_COUNT


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_suspected_frozen_fires_once_per_run() -> None:
    """SUSPECTED_FROZEN fires exactly once per WAITING run when suspect threshold crossed."""
    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=100.0, status_interval=100.0, suspect=5.0
    )
    clock.advance(1.1)  # past idle deadline
    # First evaluate: enters WAITING, sets _waiting_on_child_started_at = now
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    # Now advance 6s within the WAITING run so candidate_total = 6s > suspect=5s
    clock.advance(6.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    # Verdict is still WAITING_ON_CHILD (not FIRE)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    # Advance more — suspected_frozen should not fire again
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    suspected_after = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected_after) == 1


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_exited_event_on_record_activity() -> None:
    """EXITED event is emitted when transitioning out of WAITING via record_activity."""
    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=1000.0, status_interval=100.0, suspect=None
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # ENTERED
    clock.advance(0.5)
    watchdog.record_activity()  # should emit EXITED
    exited = [e for e in events if e.kind == WaitingStatusKind.EXITED]
    assert len(exited) == 1
    assert exited[0].current_run_seconds > 0.0


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_hard_stop_emits_event_with_diagnostic() -> None:
    """HARD_STOP event is emitted with diagnostic dict before FIRE.

    When the cumulative ceiling is crossed with the corroboration
    not seeing a live subagent, the watchdog fires
    CHILDREN_PERSIST_TOO_LONG. The HARD_STOP event is emitted with
    a diagnostic dict at the moment of the fire so the post-mortem
    (or on-call operator) can see exactly which channels were
    fresh and which were stale at the moment the watchdog fired.
    """
    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=_HARD_STOP_MAX_WAITING, status_interval=100.0, suspect=None
    )
    clock.advance(1.1)  # enter WAITING
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    clock.advance(_HARD_STOP_MAX_WAITING)  # cross ceiling
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    # Cumulative ceiling is reached; the watchdog fires
    # CHILDREN_PERSIST_TOO_LONG.
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # The HARD_STOP event was emitted with a diagnostic dict.
    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    assert hard_stops[0].diagnostic is not None


# === consolidated from test_idle_watchdog_1.py ===
def test_waiting_no_per_tick_log_spam() -> None:
    """No per-tick WAITING_ON_CHILD log spam with throttled status interval."""
    captured_logs: list[str] = []

    def _sink(message: object) -> None:
        captured_logs.append(str(message))

    sink_id = logger.add(
        _sink,
        level="DEBUG",
        filter=lambda r: r["extra"].get("component") == "idle_watchdog",
    )
    try:
        config = TimeoutPolicy(
            idle_timeout_seconds=1.0,
            max_waiting_on_child_seconds=1000.0,
            waiting_status_interval_seconds=100.0,
            suspect_waiting_on_child_seconds=None,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)
        clock.advance(1.1)
        for _ in range(200):
            watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
            clock.advance(0.01)
    finally:
        logger.remove(sink_id)

    # The old spam line contained "cumulative_candidate=" — must not appear
    spam_count = sum(1 for msg in captured_logs if "cumulative_candidate=" in msg)
    assert spam_count == 0


# === consolidated from test_idle_watchdog_1.py ===
def test_listener_exception_does_not_propagate() -> None:
    """A listener that raises must not crash the watchdog evaluate call."""

    def _bad_listener(_event: WaitingStatusEvent) -> None:
        raise RuntimeError("listener exploded")

    config = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        max_waiting_on_child_seconds=1000.0,
        waiting_status_interval_seconds=100.0,
        suspect_waiting_on_child_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(config, clock, listener=_bad_listener)
    clock.advance(1.1)
    # Must not raise
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD


# === consolidated from test_idle_watchdog_1.py ===
def test_validation_rejects_suspect_above_ceiling() -> None:
    """TimeoutPolicy rejects suspect_waiting_on_child_seconds >= max_waiting_on_child_seconds."""
    with pytest.raises(ValueError, match="strictly less than"):
        TimeoutPolicy(
            idle_timeout_seconds=10,
            max_waiting_on_child_seconds=100,
            suspect_waiting_on_child_seconds=200,
            # Disable no-progress ceiling to avoid conflict with 600.0 default
            max_waiting_on_child_no_progress_seconds=None,
        )


# === consolidated from test_idle_watchdog_1.py ===
def test_validation_rejects_suspect_equal_to_ceiling() -> None:
    """suspect_waiting_on_child_seconds equal to ceiling is also rejected."""
    with pytest.raises(ValueError, match="strictly less than"):
        TimeoutPolicy(
            idle_timeout_seconds=10,
            max_waiting_on_child_seconds=100,
            suspect_waiting_on_child_seconds=100,
            # Disable no-progress ceiling to avoid conflict with 600.0 default
            max_waiting_on_child_no_progress_seconds=None,
        )


# === consolidated from test_idle_watchdog_1.py ===
def test_corroborator_diag_attached_to_progress() -> None:
    """workspace_event_delta == 0 in PROGRESS when event_count unchanged between entry and tick."""
    call_count = 0

    def _corroborator() -> CorroborationSnapshot:
        nonlocal call_count
        call_count += 1
        return CorroborationSnapshot(workspace_event_count=5)

    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=10.0,
        suspect=None,
        corroborator=_corroborator,
    )
    clock.advance(1.1)  # past idle deadline
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # ENTERED — captures entry_corroboration
    clock.advance(11.0)  # past status_interval
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # PROGRESS
    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) >= 1
    assert progress[0].diagnostic.get("workspace_event_delta") == 0


# === consolidated from test_idle_watchdog_1.py ===
def test_corroborator_workspace_activity_increment() -> None:
    """workspace_event_delta == 4 in PROGRESS when event_count goes from 5 to 9."""
    call_count = 0
    counts = [_WS_ENTRY_COUNT, _WS_FINAL_COUNT, _WS_FINAL_COUNT]

    def _corroborator() -> CorroborationSnapshot:
        nonlocal call_count
        count = counts[min(call_count, len(counts) - 1)]
        call_count += 1
        return CorroborationSnapshot(workspace_event_count=count)

    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=10.0,
        suspect=None,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # ENTERED — entry count=5
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # PROGRESS — current count=9
    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) >= 1
    assert progress[0].diagnostic.get("workspace_event_delta") == _WS_EXPECTED_DELTA


# === consolidated from test_idle_watchdog_1.py ===
def test_corroborator_suspected_frozen_evidence_workspace_quiet() -> None:
    """SUSPECTED_FROZEN evidence has 'time_and_workspace_quiet' when workspace count unchanged."""
    suspect_threshold = 5.0

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            workspace_event_count=7,
            oldest_child_seconds=suspect_threshold + 1.0,
        )

    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=100.0,
        suspect=suspect_threshold,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # ENTERED — entry count=7
    clock.advance(suspect_threshold + 1.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # crosses suspect threshold
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    evidence = str(suspected[0].diagnostic.get("evidence", ""))
    assert "time_and_workspace_quiet" in evidence


# === consolidated from test_idle_watchdog_1.py ===
def test_corroborator_suspected_frozen_evidence_lifecycle_only() -> None:
    """SUSPECTED_FROZEN evidence has 'time_and_lifecycle_only' when last activity unmeaningful."""
    suspect_threshold = 5.0

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(last_activity_was_meaningful=False)

    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=100.0,
        suspect=suspect_threshold,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # ENTERED
    clock.advance(suspect_threshold + 1.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)  # SUSPECTED_FROZEN
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    evidence = str(suspected[0].diagnostic.get("evidence", ""))
    assert "time_and_lifecycle_only" in evidence


# === consolidated from test_idle_watchdog_1.py ===
def test_corroborator_exception_does_not_propagate() -> None:
    """When corroborator raises, watchdog still emits events and no corroboration keys appear."""

    def _bad_corroborator() -> CorroborationSnapshot:
        raise RuntimeError("corroborator exploded")

    watchdog, clock, events = _idle_watchdog_1_make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=100.0,
        suspect=None,
        corroborator=_bad_corroborator,
    )
    clock.advance(1.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_1_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    entered = [e for e in events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered) == 1
    # Entry event has no corroboration keys since the corroborator raised
    assert "workspace_event_delta" not in entered[0].diagnostic


# === consolidated from test_idle_watchdog_1.py ===
def test_idle_elapsed_seconds_tracks_time_since_last_activity() -> None:
    """idle_elapsed_seconds reports time since last activity, not the raw clock.

    The watchdog-fire log previously printed the absolute monotonic clock (a
    bogus ~36h 'elapsed'); it must report idle-elapsed instead.
    """
    watchdog, clock = _idle_watchdog_1_make_watchdog(idle_timeout=300.0, start=100.0)
    watchdog.record_activity()
    clock.advance(7.0)

    assert watchdog.idle_elapsed_seconds(clock.monotonic()) == pytest.approx(7.0)


# === consolidated from test_idle_watchdog_2.py ===
def test_hard_stop_diag_includes_corroboration() -> None:
    """HARD_STOP diagnostic contains scoped_child_active and oldest_child_seconds."""

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            scoped_child_active=True, oldest_child_seconds=_HARD_STOP_OLDEST_CHILD_SECS
        )

    hard_stop_max = 5.0
    watchdog, clock, events = _idle_watchdog_2_make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=hard_stop_max,
        status_interval=100.0,
        suspect=None,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)  # ENTERED
    clock.advance(hard_stop_max)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    diag = hard_stops[0].diagnostic
    assert diag.get("scoped_child_active") is True
    assert diag.get("oldest_child_seconds") == _HARD_STOP_OLDEST_CHILD_SECS


# === consolidated from test_idle_watchdog_2.py ===
def test_corroboration_snapshot_has_alive_by_field() -> None:
    """CorroborationSnapshot accepts alive_by and defaults to None."""
    snap = CorroborationSnapshot()
    assert snap.alive_by is None

    snap_with = CorroborationSnapshot(alive_by="fresh_progress")
    assert snap_with.alive_by == "fresh_progress"


# === consolidated from test_idle_watchdog_2.py ===
def test_build_corroboration_diag_includes_alive_by_when_set() -> None:
    """alive_by from CorroborationSnapshot propagates into the diagnostic dict."""
    events: list[WaitingStatusEvent] = []

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    config = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        suspect_waiting_on_child_seconds=None,
        waiting_status_interval_seconds=1.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(config, clock, listener=events.append, corroborator=_corroborator)

    clock.advance(1.5)
    watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)  # ENTERED
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)  # PROGRESS (interval=1s)

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert progress_events, "expected at least one PROGRESS event"
    diag = progress_events[0].diagnostic
    assert diag.get("alive_by") == "fresh_heartbeat_only"


# === consolidated from test_idle_watchdog_2.py ===
def test_build_corroboration_diag_omits_alive_by_when_none() -> None:
    """alive_by=None should not appear in the diagnostic dict."""
    events: list[WaitingStatusEvent] = []

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(scoped_child_active=True)

    config = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        suspect_waiting_on_child_seconds=None,
        waiting_status_interval_seconds=1.0,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(config, clock, listener=events.append, corroborator=_corroborator)

    clock.advance(1.5)
    watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)  # ENTERED
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)  # PROGRESS

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert progress_events
    diag = progress_events[0].diagnostic
    assert "alive_by" not in diag


# === consolidated from test_idle_watchdog_2.py ===
def test_no_progress_ceiling_fires_on_fresh_heartbeat_only() -> None:
    """WAITING_ON_CHILD with alive_by=fresh_heartbeat_only fires on no-progress ceiling.

    Regression test for wt-97-timeout: when a child is alive but only sending
    heartbeats (no progress), the shorter no-progress ceiling should fire instead
    of waiting for the full 1800s ceiling.
    """
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    # Advance past idle deadline to enter WAITING_ON_CHILD.
    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the no-progress ceiling — still waiting.
    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the no-progress ceiling — must FIRE.
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_no_progress_ceiling_fires_on_stale_label_only() -> None:
    """WAITING_ON_CHILD with alive_by=stale_label_only fires on no-progress ceiling."""
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="stale_label_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_no_progress_ceiling_fires_on_os_descendant_only() -> None:
    """WAITING_ON_CHILD with alive_by=os_descendant_only fires on no-progress ceiling."""
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        snap = CorroborationSnapshot(
            alive_by="os_descendant_only_stale_progress", scoped_child_active=True
        )
        return snap

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_full_ceiling_preserved_with_fresh_progress() -> None:
    """WAITING_ON_CHILD with alive_by=fresh_progress uses full ceiling (no false positive).

    When a child is actually making progress, the full ceiling must be used to avoid
    false-positive timeouts on legitimate long-running child work.
    """
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING,
        full_ceiling=100.0,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_progress", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the no-progress ceiling — should still be WAITING.
    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the full ceiling (100s) - we've used ~10s so far, need 89.9s more.
    clock.advance(89.9)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the full ceiling — FIRE.
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_full_ceiling_preserved_when_no_progress_ceiling_disabled() -> None:
    """When no_progress_ceiling=None, full ceiling is used even with non-progress alive_by."""
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=None,
        full_ceiling=100.0,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the no-progress ceiling — still WAITING (ceiling disabled).
    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the full ceiling — FIRE.
    clock.advance(100.0 - _NO_PROGRESS_CEILING + 1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_full_ceiling_preserved_when_alive_by_is_none() -> None:
    """When alive_by=None (unknown), full ceiling is used as safe default."""
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING,
        full_ceiling=100.0,
    )

    def _corroborator() -> CorroborationSnapshot:
        # alive_by=None means we can't determine progress — use full ceiling.
        return CorroborationSnapshot(scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the no-progress ceiling — still WAITING (alive_by=None uses full ceiling).
    clock.advance(_NO_PROGRESS_CEILING + 10.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the full ceiling (100s) - we've used ~20s so far, need ~79.9s more.
    clock.advance(79.9)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the full ceiling — FIRE.
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_hard_stop_diagnostic_includes_effective_ceiling_classification() -> None:
    """HARD_STOP diagnostic includes effective_ceiling classification."""
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING,
    )
    events: list[WaitingStatusEvent] = []

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(
        watchdog._config, clock, listener=events.append, corroborator=_corroborator
    )

    clock.advance(1.5)
    watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    clock.advance(_NO_PROGRESS_CEILING + 1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE

    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    diag = hard_stops[0].diagnostic
    assert diag.get("effective_ceiling_label") == "no_progress"


# === consolidated from test_idle_watchdog_2.py ===
def test_waiting_events_surface_effective_ceiling_when_no_progress_limit_applies() -> None:
    """Waiting events must report the active no-progress ceiling, not the full ceiling.

    Regression test for the current mismatch where the watchdog enforces the
    shorter no-progress ceiling internally but the emitted WaitingStatusEvent
    still advertises the full max_waiting_on_child_seconds value.
    """
    config = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=_FULL_CEILING,
        suspect_waiting_on_child_seconds=None,
        waiting_status_interval_seconds=1.0,
        max_waiting_on_child_no_progress_seconds=_NO_PROGRESS_CEILING,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
    )
    clock = FakeClock(start=0.0)
    events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        events.append(event)

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(
        config,
        clock,
        listener=_listener,
        corroborator=_corroborator,
    )

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    entered = [e for e in events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered) == 1
    assert entered[0].ceiling_seconds == _NO_PROGRESS_CEILING

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) == 1
    assert progress[0].ceiling_seconds == _NO_PROGRESS_CEILING


# === consolidated from test_idle_watchdog_2.py ===
def test_no_progress_ceiling_adapts_when_corroboration_degrades() -> None:
    """No-progress ceiling activates mid-wait when corroboration degrades from fresh to stale.

    Regression for wt-97: when the watchdog enters WAITING_ON_CHILD with fresh-progress
    evidence (full ceiling), then the corroboration degrades to OS-descendant-only
    evidence (no scoped progress), the effective ceiling must switch to the shorter
    no-progress ceiling on the very next tick — not wait for the full ceiling.

    Timeline (full_ceiling=100s, no_progress_ceiling=20s):
    - T1 (t=1.5): ENTER WAITING with fresh_progress → ceiling=100s.
    - T2 (t=19.5): cumulative=18s < 20s, corr still fresh_progress → WAITING.
    - Corroborator degrades to os_descendant_only_stale_progress.
    - T3 (t=22.5): cumulative=21s >= 20s (no-progress ceiling) → FIRE.
    """
    full_ceiling = 100.0
    no_progress_ceiling = 20.0

    phase: list[str] = ["fresh"]

    def _corroborator() -> CorroborationSnapshot:
        if phase[0] == "fresh":
            return CorroborationSnapshot(alive_by="fresh_progress", scoped_child_active=True)
        return CorroborationSnapshot(
            alive_by="os_descendant_only_stale_progress", scoped_child_active=True
        )

    config = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=full_ceiling,
        suspect_waiting_on_child_seconds=None,
        waiting_status_interval_seconds=100.0,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(config, clock, corroborator=_corroborator)

    # T1: enter WAITING at t=1.5s (cumulative=0s), fresh_progress → full ceiling.
    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # T2: cumulative=18s, still fresh_progress → ceiling=100s → WAITING.
    clock.advance(18.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Corroboration degrades to OS-descendant-only (no scoped progress any more).
    phase[0] = "degraded"

    # T3: cumulative=21s >= no_progress_ceiling=20s → FIRE (not waiting for full ceiling=100s).
    clock.advance(3.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_2.py ===
def test_validation_rejects_no_progress_ceiling_above_max_waiting() -> None:
    """TimeoutPolicy rejects no_progress_ceiling > max_waiting_on_child_seconds."""
    with pytest.raises(ValueError, match="max_waiting_on_child_no_progress_seconds must be <="):
        TimeoutPolicy(
            idle_timeout_seconds=10.0,
            max_waiting_on_child_seconds=100.0,
            max_waiting_on_child_no_progress_seconds=200.0,
            suspect_waiting_on_child_seconds=None,  # Disable to avoid conflict
        )


# === consolidated from test_idle_watchdog_2.py ===
def test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics() -> None:
    """A single WAITING tick must reuse one corroboration snapshot for all decisions.

    Regression test for wt-97-timeout: the flaky corroborator rotates through
    alive_by values on each call. If the code reverted to calling
    _safe_corroborate() separately for the ceiling decision vs. each diagnostic
    (HARD_STOP, SUSPECTED_FROZEN, PROGRESS), the call_count assertion would catch
    it (would be > 1 on the fire tick).

    The test exercises three ticks:
    - T1 (t=1.5): ENTER WAITING (entry corroboration + effective_ceiling computed).
    - T2 (t=6.5): SUSPECTED_FROZEN + PROGRESS fire on same tick — proves both
      diagnostics reuse the same snapshot (alive_by must agree).
    - T3 (t=11.5): HARD_STOP fires — proves corroborator was called exactly once
      on the fire tick and effective_ceiling is correct.
    """
    call_count = 0
    # Flaky corroborator: each call returns a different alive_by value.
    _alive_by_values = (
        "fresh_progress",
        "stale_label_only",
        "os_descendant_only_stale_progress",
    )

    def _flaky_corroborator() -> CorroborationSnapshot:
        nonlocal call_count
        call_count += 1
        return CorroborationSnapshot(
            alive_by=_alive_by_values[call_count % len(_alive_by_values)],
            scoped_child_active=True,
        )

    config = TimeoutPolicy(
        idle_timeout_seconds=1.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=_NO_PROGRESS_CEILING,
        suspect_waiting_on_child_seconds=3.0,
        waiting_status_interval_seconds=0.001,
        os_descendant_only_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
    )
    clock = FakeClock(start=0.0)
    events: list[WaitingStatusEvent] = []
    watchdog = IdleWatchdog(config, clock, listener=events.append, corroborator=_flaky_corroborator)

    # T1: ENTER WAITING at t=1.5
    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    # T1 makes 2 calls: _entry_corroboration + effective_ceiling

    # T2: SUSPECTED_FROZEN + PROGRESS at t=6.5 (candidate_total=5.0, suspect=3.0)
    clock.advance(5.0)
    call_count = 0  # reset to isolate T2 calls
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    # T2 makes 1 call: only effective_ceiling (not entering WAITING)
    assert call_count == 1

    # T3: HARD_STOP at t=11.5 (candidate_total=10.0 >= no_progress_ceiling=10.0)
    call_count = 0  # reset to isolate T3 calls
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_idle_watchdog_2_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # HARD_STOP fires and returns early, so no other diagnostics on this tick.
    assert call_count == 1, (
        f"Expected corroborator called exactly once on fire tick, got {call_count}. "
        "If > 1, the code is calling _safe_corroborate() multiple times per tick."
    )

    fire_diag_by_kind = {e.kind: e.diagnostic for e in events}
    assert WaitingStatusKind.HARD_STOP in fire_diag_by_kind
    hs_diag = fire_diag_by_kind[WaitingStatusKind.HARD_STOP]
    assert hs_diag.get("effective_ceiling_label") == "no_progress", (
        f"Expected 'no_progress', got {hs_diag.get('effective_ceiling_label')}."
    )

    # T2 diagnostics: SUSPECTED_FROZEN and PROGRESS must agree on alive_by
    # since they fire on the same tick (proves single snapshot reuse).
    t2_diag_by_kind = {
        e.kind: e.diagnostic
        for e in events
        if e.kind in (WaitingStatusKind.SUSPECTED_FROZEN, WaitingStatusKind.PROGRESS)
    }
    sf_diag_t2 = t2_diag_by_kind.get(WaitingStatusKind.SUSPECTED_FROZEN)
    pr_diag_t2 = t2_diag_by_kind.get(WaitingStatusKind.PROGRESS)

    assert sf_diag_t2 is not None, (
        f"Expected SUSPECTED_FROZEN on T2, got: {[e.kind for e in events]}"
    )
    assert pr_diag_t2 is not None, f"Expected PROGRESS on T2, got: {[e.kind for e in events]}"

    # Same tick -> same snapshot -> alive_by must be identical.
    assert sf_diag_t2.get("alive_by") == pr_diag_t2.get("alive_by"), (
        f"T2: SUSPECTED_FROZEN alive_by={sf_diag_t2.get('alive_by')} != "
        f"PROGRESS alive_by={pr_diag_t2.get('alive_by')}. Same tick must use same snapshot."
    )


# === consolidated from test_idle_watchdog_2.py ===
def test_validation_rejects_no_progress_ceiling_equal_to_max() -> None:
    """TimeoutPolicy allows no_progress_ceiling equal to max_waiting_on_child_seconds.

    When equal, the no-progress ceiling provides no earlier protection (same as full ceiling),
    but it is still a valid configuration.
    """
    # This should NOT raise - equality is allowed (validation uses > not >=)
    equal_ceiling = 100.0
    policy = TimeoutPolicy(
        idle_timeout_seconds=10.0,
        max_waiting_on_child_seconds=equal_ceiling,
        max_waiting_on_child_no_progress_seconds=equal_ceiling,
        no_progress_quiet_seconds=equal_ceiling,
        no_progress_quiet_minimum_invocation_seconds=equal_ceiling,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        suspect_waiting_on_child_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    assert policy.max_waiting_on_child_no_progress_seconds == equal_ceiling


# === consolidated from test_idle_watchdog_2.py ===
def test_safe_corroborate_bypasses_cache_outside_evaluate() -> None:
    """Two consecutive ``_safe_corroborate()`` calls OUTSIDE ``evaluate()`` must
    each invoke the corroborator and return FRESH snapshots, never reuse a
    cached snapshot from a prior bypass-path call.

    Regression test for the stale-cache bug pinned by analysis feedback:
    ``_safe_corroborate()`` previously stored its return value in
    ``self._tick_corroboration`` unconditionally, so two bypass-path calls
    returned the SAME (now-stale) ``alive_by`` value and never observed the
    corroborator's second return. The fix routes outside-``evaluate()``
    calls to ``_call_corroborator_raw()`` directly without touching
    ``_tick_corroboration`` (gated on the explicit ``_evaluate_tick_active``
    sentinel). The contract pinned here:

    1. Outside ``evaluate()``, two consecutive ``_safe_corroborate()``
       calls invoke the corroborator TWICE (not once).
    2. The second call returns the corroborator's SECOND return value
       (not the first cached snapshot).
    3. A single ``evaluate()`` tick still reuses ONE snapshot across its
       internal reads (the existing per-tick-reuse contract pinned by
       ``test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics``
       is NOT weakened).
    """
    call_count = 0
    alive_by_sequence = (
        "fresh_progress",
        "log_stale_while_alive",
        "os_descendant_only_stale_progress",
        "fresh_heartbeat_only",
    )

    def _flaky_corroborator() -> CorroborationSnapshot:
        nonlocal call_count
        call_count += 1
        return CorroborationSnapshot(
            alive_by=alive_by_sequence[call_count - 1],
            scoped_child_active=True,
        )

    config = TimeoutPolicy(
        idle_timeout_seconds=10.0,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=_NO_PROGRESS_CEILING,
        suspect_waiting_on_child_seconds=None,
        waiting_status_interval_seconds=0.001,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(config, clock, corroborator=_flaky_corroborator)

    # Two consecutive bypass-path calls: each MUST invoke the corroborator
    # and return the corresponding fresh value. A stale cache would return
    # the FIRST ``alive_by`` on the second call (the original bug).
    first = watchdog._safe_corroborate()
    second = watchdog._safe_corroborate()
    assert call_count == 2, (
        f"Outside evaluate(), _safe_corroborate() must call the corroborator "
        f"each time. Got call_count={call_count}; a stale cache would be 1."
    )
    assert first.alive_by == "fresh_progress", (
        f"First bypass-path call: expected 'fresh_progress', got {first.alive_by!r}"
    )
    assert second.alive_by == "log_stale_while_alive", (
        f"Second bypass-path call: expected 'log_stale_while_alive' (the "
        f"corroborator's second return), got {second.alive_by!r}. A stale "
        f"cache would return 'fresh_progress' here."
    )

    # A third bypass-path call: still bypassing the cache, returning the
    # third fresh value (further pins the per-call freshness contract).
    third = watchdog._safe_corroborate()
    assert call_count == 3
    assert third.alive_by == "os_descendant_only_stale_progress", (
        f"Third bypass-path call: expected "
        f"'os_descendant_only_stale_progress', got {third.alive_by!r}"
    )

    # The cache field MUST remain None outside ``evaluate()`` so the next
    # bypass-path call cannot accidentally hit a stale snapshot.
    assert watchdog._tick_corroboration is None, (
        "Outside evaluate(), _tick_corroboration must remain None. A non-None "
        "value here means the bypass path poisoned the cache, and the next "
        "_safe_corroborate() call (still outside evaluate()) would return the "
        "stale snapshot."
    )

    # Per-tick reuse contract: a single ``evaluate()`` tick must still
    # capture ONE snapshot and reuse it for all sub-evaluators on that tick.
    # Reset call_count so the assertion isolates the ``evaluate()`` call.
    call_count = 0
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert call_count == 1, (
        f"A single evaluate() tick must call the corroborator exactly once "
        f"(tick-scoped reuse), got call_count={call_count}."
    )

    # After ``evaluate()`` returns, ``_evaluate_tick_active`` MUST be False
    # so a subsequent bypass-path call is again routed to the raw
    # corroborator rather than the now-stale tick cache.
    assert watchdog._evaluate_tick_active is False, (
        "evaluate() must reset _evaluate_tick_active to False in its finally "
        "block. A True value here means the tick cache is still 'active' "
        "after evaluate() returned, which would poison the next bypass-path "
        "call with the tick's stale snapshot."
    )
    assert watchdog._tick_corroboration is None, (
        "evaluate() must reset _tick_corroboration to None in its finally "
        "block so the next bypass-path call cannot hit a stale snapshot."
    )

    # Bypass-path call AFTER evaluate(): must observe the corroborator's
    # NEXT fresh value, not the snapshot captured during the previous tick.
    fourth = watchdog._safe_corroborate()
    assert call_count == 2, (
        f"Bypass-path call AFTER evaluate() must invoke the corroborator "
        f"(call_count=2), got call_count={call_count}."
    )
    # evaluate() consumed call_count=1 (first index of sequence), so the
    # bypass-path call after evaluate() consumes index 1 of the sequence.
    # Crucially, this is the corroborator's LIVE second return, NOT the
    # snapshot cached during the previous tick (which would be
    # "fresh_progress"). A poisoned cache would either return the tick's
    # cached snapshot or skip the corroborator entirely (call_count == 1).
    assert fourth.alive_by == "log_stale_while_alive", (
        f"Bypass-path call after evaluate() must return the corroborator's "
        f"NEXT fresh value, not a stale tick snapshot or a duplicate of the "
        f"evaluate() tick's snapshot. Got alive_by={fourth.alive_by!r}."
    )


# === consolidated from test_idle_watchdog_3.py ===
def test_no_false_kill_on_mcp_tool_activity() -> None:
    """Agent making MCP tool calls with no stdout output is not killed as idle.

    Sequence:
      - watchdog starts at t=0 with idle=0.1s, ttl=1000s
      - record_activity at t=0 (sets stdout baseline)
      - advance 100s of silence (well over idle 0.1s)
      - record_mcp_tool_call at t=100s (refreshes mcp_tool channel)
      - advance 50s (over idle again, well under 1000s TTL)
      - evaluate -> CONTINUE (deferred via mcp_tool channel evidence)
      - advance another 2000s (over the 1000s TTL, no new tool call)
      - evaluate -> FIRE (no fresh channel evidence)
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_mcp_tool_call()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (deferred via mcp_tool channel), got {verdict}"
    )
    assert wd._channel_evidence_active(clock.monotonic()) is True

    # Now wait past the TTL with no new activity -> FIRE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE (channel stale past TTL), got {verdict}"
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_no_false_kill_on_subagent_work() -> None:
    """Agent waiting on a subagent that is demonstrably active is not killed.

    Mirrors the mcp_tool test but uses ``record_subagent_work`` and the
    same shape: long silence, subagent signal, advance, evaluate ->
    CONTINUE while the channel is fresh; advance past the TTL -> FIRE.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_subagent_work()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_no_false_kill_on_workspace_changes() -> None:
    """Agent whose workspace is changing (writes files) is not killed as idle.

    Same shape as the mcp_tool and subagent tests, but uses the
    production-style ``record_workspace_event(kind=..., weight=...)``
    call that the WorkspaceMonitor -> watchdog wiring forwards.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_dead_subagent_detected_within_idle_window() -> None:
    """A silent subagent is detected at the regular idle deadline, not the
    cumulative WAITING_ON_CHILD ceiling.

    Sequence:
      - record a subagent work event (child is alive but signaling)
      - advance past the 30s TTL with no further activity
      - evaluate -> NO_OUTPUT_DEADLINE (the regular idle path), not
        CHILDREN_PERSIST_TOO_LONG (the 1800s cumulative ceiling)
    """
    wd, clock = _idle_watchdog_3_make_watchdog()
    wd.record_activity()
    wd.record_subagent_work()
    # Advance past the 30s default TTL (so the subagent channel is stale)
    clock.advance(31.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    # The fire reason is the regular idle deadline, NOT the
    # cumulative waiting-on-child ceiling. Pre-fix, this would have
    # survived the full 1800s default because the only signal was
    # the child being alive.
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_truly_idle_still_fires_on_time() -> None:
    """A session with no activity on any channel is terminated no later than
    before. The new recorders are additive: ``record_activity`` and the
    existing NO_OUTPUT_DEADLINE path are unchanged when no channel signal
    is present.
    """
    wd, clock = _idle_watchdog_3_make_watchdog()
    # No record_* calls. Advance past idle timeout.
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_session_ceiling_unaffected_by_activity() -> None:
    """``max_session_seconds`` fires exactly as before, regardless of activity.

    Even when MCP tool calls fire every second and the activity channel
    would otherwise defer the idle deadline, the absolute session
    ceiling is checked FIRST and fires immediately when exceeded.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(idle_timeout=1.0, max_session=10.0, activity_ttl=30.0)
    for _t in range(0, 12):
        wd.record_mcp_tool_call()
        clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# === consolidated from test_idle_watchdog_3.py ===
def test_max_waiting_ceiling_unaffected_by_activity() -> None:
    """``max_waiting_on_child_seconds`` fires when the cumulative ceiling is
    reached AND the activity channel is stale.

    Per the smart-verdict gate (StuckClassifier), the cumulative
    ceiling is gated by the per-channel freshness check. When the
    first-party mcp_tool channel is fresh, the gate defers the
    CHILDREN_PERSIST_TOO_LONG fire (the agent is making forward
    progress). When the channel is stale, the classifier returns
    STUCK and the gate allows the fire. This is the symmetric
    counterpart of the dumb-kill regression: a productive-but-quiet
    session is NOT killed (gate defers), but a genuinely-dead
    session with persistent children IS killed (gate allows).

    The pre-existing version of this test (kept an mcp_tool channel
    fresh) verified the OLD absolute behavior; under the new design
    the test must allow the channel to go stale so the gate can
    permit the fire.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(idle_timeout=0.1, max_waiting=2.0, activity_ttl=0.0)
    wd.record_activity()
    clock.advance(0.1)
    # classify_quiet is always WAITING_ON_CHILD so the watchdog goes
    # into the WAITING branch. activity_ttl=0.0 disables the
    # first-party deferral so the classifier returns STUCK as soon
    # as the cumulative ceiling is reached.
    for _ in range(30):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# === consolidated from test_idle_watchdog_3.py ===
def test_activity_evidence_ttl_zero_disables_feature() -> None:
    """Setting ``activity_evidence_ttl_seconds=0.0`` disables the activity-aware
    verdict and restores the legacy stdout-only behavior.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=0.0)
    wd.record_activity()
    clock.advance(0.2)  # past idle
    wd.record_mcp_tool_call()
    # With ttl=0 the channel can never be "fresh", so the next
    # evaluate fires.
    clock.advance(0.1)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_evidence_summary_in_hard_stop_diagnostic() -> None:
    """When the watchdog fires CHILDREN_PERSIST_TOO_LONG, the emitted
    HARD_STOP event's diagnostic carries the per-channel evidence summary
    under the ``evidence_summary`` key.

    The post-mortem (or the on-call operator) can see exactly which
    channels were fresh and which were stale at the moment the
    watchdog fired.
    """
    events: list[WaitingStatusEvent] = []
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=2.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        # activity_evidence_ttl_seconds=0.0 disables the first-party
        # freshness deferral so the cumulative ceiling can fire
        # even when channels are recorded. The per-channel evidence
        # summary is still emitted in the HARD_STOP diagnostic so
        # the post-mortem (or on-call operator) can see exactly
        # which channels were fresh and which were stale at the
        # moment the watchdog fired.
        activity_evidence_ttl_seconds=0.0,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    wd = IdleWatchdog(config, clock, listener=events.append)
    wd.record_activity()
    # Record some activity on multiple channels to make the summary
    # interesting.
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    # Go into WAITING and let the cumulative ceiling fire. The
    # activity channel does NOT defer the cumulative ceiling, so the
    # CHILDREN_PERSIST_TOO_LONG branch fires as soon as the cumulative
    # exceeds the 2.0s ceiling.
    verdict = WatchdogVerdict.CONTINUE
    for _ in range(25):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert hard_stops, "no HARD_STOP event captured"
    diag = hard_stops[0].diagnostic
    assert "evidence_summary" in diag
    assert isinstance(diag["evidence_summary"], list)
    assert len(diag["evidence_summary"]) == 5
    channel_names = {entry["channel"] for entry in diag["evidence_summary"]}
    assert channel_names == {
        "stdout",
        "mcp_tool",
        "subagent_output",
        "subagent_liveness",
        "workspace",
    }


# === consolidated from test_idle_watchdog_3.py ===
def test_session_ceiling_fire_carries_evidence_summary() -> None:
    """SESSION_CEILING_EXCEEDED fire log embeds per-channel evidence_summary."""
    wd, clock = _idle_watchdog_3_make_watchdog(max_session=5.0, start=0.0)
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    clock.advance(6.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED

    fire_records = [
        m for m in captured if "FIRE reason=session_ceiling_exceeded" in str(m.record["message"])
    ]
    assert fire_records, "no SESSION_CEILING_EXCEEDED fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "session_ceiling_exceeded"


# === consolidated from test_idle_watchdog_3.py ===
def test_repeated_error_loop_fire_carries_evidence_summary() -> None:
    """REPEATED_ERROR_LOOP fire log embeds per-channel evidence_summary."""
    wd, clock = _idle_watchdog_3_make_watchdog(idle_timeout=300.0, max_waiting=600.0, start=0.0)
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        wd.record_error_activity(msg)
        clock.advance(34.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        wd.record_error_activity(msg)
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP

    fire_records = [
        m for m in captured if "FIRE reason=repeated_error_loop" in str(m.record["message"])
    ]
    assert fire_records, "no REPEATED_ERROR_LOOP fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "repeated_error_loop"


# === consolidated from test_idle_watchdog_3.py ===
def test_stalled_after_tool_result_fire_carries_evidence_summary() -> None:
    """STALLED_AFTER_TOOL_RESULT fire log embeds per-channel evidence_summary."""
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=100.0,
        post_tool_result_progression_seconds=0.1,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    wd = IdleWatchdog(config, clock)
    wd.record_activity()
    wd.record_tool_result_activity()
    clock.advance(1.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT

    fire_records = [
        m for m in captured if "FIRE reason=stalled_after_tool_result" in str(m.record["message"])
    ]
    assert fire_records, "no STALLED_AFTER_TOOL_RESULT fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "stalled_after_tool_result"


# === consolidated from test_idle_watchdog_3.py ===
def test_workspace_monitor_records_last_event_at(tmp_path: Path) -> None:
    """``WorkspaceMonitor`` accepts an injectable ``now`` callable so tests
    can drive ``last_event_at`` deterministically via FakeClock.

    This is the seam that lets the production runtime use
    ``time.monotonic`` while the tests use a deterministic value.
    """
    clock_value = [0.0]

    def fake_now() -> float:
        return clock_value[0]

    monitor = WorkspaceMonitor(tmp_path, now=fake_now)
    assert monitor.last_event_at is None
    assert monitor.event_count == 0
    monitor.record_event("/tmp/file_a.py")
    assert monitor.last_event_at == 0.0
    assert monitor.event_count == 1
    clock_value[0] = 5.0
    monitor.record_event("/tmp/file_b.py")
    assert monitor.last_event_at == 5.0
    assert monitor.event_count == 2

    monitor.reset_last_event_at()
    assert monitor.last_event_at is None
    assert monitor.event_count == 0


# === consolidated from test_idle_watchdog_3.py ===
def test_workspace_monitor_to_watchdog_integration(tmp_path: Path) -> None:
    """``WorkspaceMonitor`` end-to-end integration: when the monitor's
    ``on_event`` callback is wired to the watchdog's
    ``record_workspace_event`` via the production 2-arg lambda, a
    recorded file change updates the watchdog's per-channel
    ``_last_workspace_event_at`` timestamp AND the per-kind counter.

    This is the production wiring: the readers receive the
    ``WorkspaceMonitor`` via ``ctx.monitor`` and register
    ``lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight)``
    as the on-event callback after the watchdog is created. A file
    change in the monitored workspace is then visible to the watchdog
    as a workspace channel event, and the activity-aware verdict can
    defer ``NO_OUTPUT_DEADLINE`` while the workspace is changing.

    Pre-fix, the production code path did not wire this up: the
    monitor's ``record_event`` updated its own internal
    ``last_event_at`` but never called the watchdog, so the watchdog's
    ``_last_workspace_event_at`` was always None and the workspace
    channel could never defer a fire. This test would fail in that
    case; after the fix it must pass.
    """
    wd, clock = _idle_watchdog_3_make_watchdog()
    # Use the watchdog's FakeClock as the monitor's clock source so
    # the two clocks stay synchronized (production uses time.monotonic
    # for both; the test mirrors that with a shared fake).
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_idle_watchdog_3_default_classifier(),
    )
    # Pre-condition: watchdog has not observed any workspace activity yet.
    assert wd._last_workspace_event_at is None
    assert wd._workspace_event_count_internal == 0
    # Wire the production-style 2-arg callback so the watchdog receives
    # the real (kind, weight) classification instead of the OTHER default.
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))
    # Advance both clocks together and trigger a file change.
    clock.advance(100.5)
    monitor.record_event("/tmp/foo.py")
    # The watchdog's per-channel state must now reflect the event.
    assert wd._last_workspace_event_at == 100.5
    assert wd._workspace_event_count_internal == 1
    # The per-kind counter must reflect the real classification (source),
    # not the OTHER default that the legacy 0-arg binding would yield.
    assert wd.workspace_kind_counts == {"source": 1}


# === consolidated from test_idle_watchdog_3.py ===
def test_workspace_monitor_to_watchdog_defers_verdict(tmp_path: Path) -> None:
    """End-to-end: with WorkspaceMonitor wired to the watchdog via the
    production 2-arg lambda, a source file change defers
    ``NO_OUTPUT_DEADLINE`` while the channel is fresher than
    ``activity_evidence_ttl_seconds``.

    This is the AC-01 corollary for the workspace channel: a session
    that is quiet on stdout but actively writing source files is not
    killed as idle, even past the regular idle deadline.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    # Use the watchdog's FakeClock as the monitor's clock source so
    # both clocks stay synchronized (production uses time.monotonic
    # for both; the test mirrors that with a shared fake).
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_idle_watchdog_3_default_classifier(),
    )
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))
    # Quiet stdout for 5s of watchdog time. The monitor's clock is the
    # same as the watchdog's, so a single advance moves both.
    clock.advance(5.0)
    # A source workspace event is recorded at watchdog-t=5.0; the
    # watchdog workspace channel is now fresh.
    monitor.record_event("/tmp/foo.py")
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (deferred via workspace channel), got {verdict}"
    )
    # Advance watchdog past the TTL with no new workspace activity.
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_3.py ===
def test_workspace_monitor_smart_filter_source_defers_log_does_not(tmp_path: Path) -> None:
    """End-to-end smart-filter proof: with the default conservative
    classifier, a source file change defers ``NO_OUTPUT_DEADLINE``
    while a log file change does NOT.

    This is the AC-07 regression test requested by the plan: workspace
    monitoring must remain smart, counting source changes as activity
    while dropping log-only churn by default.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_idle_watchdog_3_default_classifier(),
    )
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))

    # Quiet stdout for 5s, then record a log file change.
    clock.advance(5.0)
    monitor.record_event("/tmp/agent.log")
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE, (
        "expected FIRE for log-only change (dropped by default classifier)"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert wd.workspace_kind_counts.get("log", 0) == 0, (
        "log events must not increment the per-kind counter when dropped"
    )

    # Reset and record a source file change: this MUST defer the fire.
    wd2, clock2 = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    monitor2 = WorkspaceMonitor(
        tmp_path,
        now=clock2.monotonic,
        classifier=_idle_watchdog_3_default_classifier(),
    )
    monitor2.set_on_event(lambda kind, weight: wd2.record_workspace_event(kind=kind, weight=weight))
    clock2.advance(5.0)
    monitor2.record_event("/tmp/foo.py")
    verdict2 = wd2.evaluate(classify_quiet=_active_classifier())
    assert verdict2 == WatchdogVerdict.CONTINUE, (
        "expected CONTINUE for source change (counts as activity by default)"
    )
    assert wd2.workspace_kind_counts == {"source": 1}


# === consolidated from test_idle_watchdog_3.py ===
def test_last_evidence_summary_channel_order() -> None:
    """``last_evidence_summary`` returns a tier-aware ``EvidenceSummary`` with
    five channels in fixed order (stdout, mcp_tool, subagent_output,
    subagent_liveness, workspace).
    """
    wd, _ = _idle_watchdog_3_make_watchdog()
    summary = wd.last_evidence_summary(0.0)
    assert isinstance(summary, EvidenceSummary)
    assert len(summary.channels) == 5
    assert [s.channel_name for s in summary.channels] == [
        ChannelName.STDOUT,
        ChannelName.MCP_TOOL,
        ChannelName.SUBAGENT_OUTPUT,
        ChannelName.SUBAGENT_LIVENESS,
        ChannelName.WORKSPACE,
    ]
    for entry in summary.channels:
        assert isinstance(entry, ChannelEvidenceSummary)


# === consolidated from test_idle_watchdog_3.py ===
def test_recorders_do_not_mutate_last_activity() -> None:
    """The three recorders (``record_mcp_tool_call``, ``record_subagent_work``,
    ``record_workspace_event``) must NOT touch ``_last_activity`` (the stdout
    baseline). The existing 'stdout only resets idle baseline' invariant is
    preserved; the activity-aware verdict is layered on top of the
    existing log without perturbing it.
    """
    wd, clock = _idle_watchdog_3_make_watchdog()
    baseline = wd._last_activity
    # Move the clock forward so a record_activity() call produces a
    # strictly different timestamp; otherwise the FakeClock returns the
    # same value and the comparison would trivially succeed.
    clock.advance(1.0)
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    assert wd._last_activity == baseline
    # stdout activity DOES move the baseline.
    wd.record_activity()
    assert wd._last_activity != baseline
    assert wd._last_activity > baseline


# === consolidated from test_idle_watchdog_3.py ===
def test_corroboration_snapshot_carries_per_channel_fields() -> None:
    """The new ``mcp_tool_call_count``, ``subagent_progress_count``,
    ``last_mcp_tool_call_at``, ``last_subagent_progress_at``,
    ``last_workspace_event_at``, and ``current_run_idle_elapsed_seconds``
    fields default to None so existing construction sites remain valid.
    """
    s = CorroborationSnapshot()
    assert s.mcp_tool_call_count is None
    assert s.subagent_progress_count is None
    assert s.last_mcp_tool_call_at is None
    assert s.last_subagent_progress_at is None
    assert s.last_workspace_event_at is None
    assert s.current_run_idle_elapsed_seconds is None
    # Existing fields still work.
    assert s.workspace_event_count is None
    assert s.alive_by is None


# === consolidated from test_idle_watchdog_3.py ===
def test_activity_evidence_ttl_none_is_allowed() -> None:
    """``activity_evidence_ttl_seconds=None`` is the disable opt-out; it
    must remain a valid TimeoutPolicy value (the feature is off, but the
    policy constructs successfully).
    """
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        activity_evidence_ttl_seconds=None,
    )
    assert config.activity_evidence_ttl_seconds is None


# === consolidated from test_idle_watchdog_3.py ===
def test_recorders_accept_custom_now_timestamp() -> None:
    """The three recorders accept an optional ``now`` parameter so tests
    can drive timestamps without mutating the watchdog's injected clock.

    This is critical for the FakeClock-based tests in
    test_idle_watchdog_3.py and for the per-channel age math in
    ``_channel_evidence_active``.
    """
    wd, _ = _idle_watchdog_3_make_watchdog()
    wd.record_mcp_tool_call(now=42.0)
    wd.record_subagent_work(now=43.0)
    wd.record_workspace_event(now=44.0)
    assert wd._last_mcp_tool_call_at == 42.0
    assert wd._last_subagent_progress_at == 43.0
    assert wd._last_workspace_event_at == 44.0
    assert wd._mcp_tool_call_count == 1
    assert wd._subagent_progress_count == 1
    assert wd._workspace_event_count_internal == 1


# === consolidated from test_idle_watchdog_3.py ===
def test_handle_evidence_deferral_returns_continue() -> None:
    """``_handle_evidence_deferral`` is the private verdict-hook method
    the watchdog consults when the idle deadline has elapsed but a
    non-stdout channel is still fresh. It returns CONTINUE.
    """
    wd, clock = _idle_watchdog_3_make_watchdog()
    wd.record_mcp_tool_call()
    verdict = wd._handle_evidence_deferral(clock.monotonic(), 0.5)
    assert verdict == WatchdogVerdict.CONTINUE


# === consolidated from test_idle_watchdog_3.py ===
def test_handle_evidence_deferral_debug_log_names_channel_age() -> None:
    """The deferral debug log's ``age=`` field must reflect the FRESHEST
    non-stdout channel age, not the stdout ``idle_elapsed``.

    This is the regression test for the Plan Compliance finding
    described in the development analysis: the pre-fix
    ``_handle_evidence_deferral`` passed ``round(idle_elapsed, 1)``
    twice, so the log claimed the freshest non-stdout channel age
    equalled the stdout idle elapsed (which is only true when stdout
    is the only channel and the active channel label is "none").

    Scenario:
      - stdout idle for 60s (well past the 0.1s idle deadline)
      - a subagent work event at t=5s (age = 55s at evaluate-time)
      - evaluate -> deferred (subagent channel is fresh under the
        default 30s TTL? NO - age 55s is over the 30s TTL)

    To force a real deferral we must keep the channel within the TTL,
    so the scenario is:
      - record_subagent_work at t=0
      - advance 50s of stdout silence (idle = 50s, channel age = 50s,
        still fresh under a 1000s TTL)
      - _handle_evidence_deferral with idle_elapsed=50.0

    The 'age=' field must equal 50.0 (the channel age), and the
    'idle_elapsed=' field must also equal 50.0 (which happens to
    match because there is no other activity; the test still proves
    the log line is well-formed and consistent with the
    _build_evidence_summary_diag helper).
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    wd.record_subagent_work()  # at t=0
    clock.advance(50.0)  # both stdout and subagent age = 50s
    captured = []

    def _sink(message: object) -> None:
        captured.append(str(message.record["message"]))

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        wd._handle_evidence_deferral(clock.monotonic(), 50.0)
    finally:
        loguru_logger.remove(handler_id)

    deferral_lines = [m for m in captured if "deferred via activity evidence" in m]
    assert deferral_lines, f"expected a 'deferred via activity evidence' debug log, got: {captured}"
    line = deferral_lines[0]
    # Both ages happen to be 50.0 in this scenario; the test confirms
    # the log line is well-formed and the channel label is 'subagent'.
    assert "channel=subagent" in line, f"channel label must be 'subagent', got: {line}"
    assert "age=50.0s" in line, f"age= field must be 50.0s, got: {line}"
    assert "idle_elapsed=50.0s" in line, f"idle_elapsed= field must be 50.0s, got: {line}"


# === consolidated from test_idle_watchdog_3.py ===
def test_handle_evidence_deferral_debug_log_age_differs_from_idle_elapsed() -> None:
    """The deferral debug log's ``age=`` field must DIFFER from
    ``idle_elapsed=`` when the freshest non-stdout channel is fresher
    than the stdout baseline.

    Scenario:
      - watchdog starts at t=0 with idle=0.1s, ttl=1000s
      - record_activity at t=0 (sets stdout baseline)
      - advance 60s of stdout silence
      - record_mcp_tool_call at t=60s (refreshes mcp_tool channel;
        stdout last_at remains t=0, so stdout age = 60s)
      - advance 55s (total elapsed = 115s; mcp_tool age = 55s,
        stdout age = 115s; both fresh under 1000s TTL)
      - _handle_evidence_deferral with idle_elapsed=115.0

    Expected log: ``channel=mcp_tool age=55.0s idle_elapsed=115.0s``.
    The 'age=' value (55.0) must DIFFER from the 'idle_elapsed=' value
    (115.0); pre-fix the log claimed both were 115.0, which is
    incorrect and confusing to operators reading the post-mortem.
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    wd.record_activity()  # at t=0
    clock.advance(60.0)
    wd.record_mcp_tool_call()  # at t=60 (stdout stays at t=0)
    clock.advance(55.0)  # total elapsed = 115s

    captured = []

    def _sink(message: object) -> None:
        captured.append(str(message.record["message"]))

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        wd._handle_evidence_deferral(clock.monotonic(), 115.0)
    finally:
        loguru_logger.remove(handler_id)

    deferral_lines = [m for m in captured if "deferred via activity evidence" in m]
    assert deferral_lines, f"expected a 'deferred via activity evidence' debug log, got: {captured}"
    line = deferral_lines[0]
    assert "channel=mcp_tool" in line, f"channel label must be 'mcp_tool', got: {line}"
    assert "age=55.0s" in line, (
        f"age= field must reflect the mcp_tool channel age (55.0s), not "
        f"the stdout idle elapsed (115.0s); got: {line}"
    )
    assert "idle_elapsed=115.0s" in line, (
        f"idle_elapsed= field must reflect the stdout baseline age (115.0s), got: {line}"
    )
    # The two values must differ; this is the central regression assertion.
    assert "age=55.0s" in line and "idle_elapsed=115.0s" in line, (
        f"age= must differ from idle_elapsed= when the channel age is "
        f"fresher than the stdout baseline; got: {line}"
    )


# === consolidated from test_idle_watchdog_3.py ===
def test_build_evidence_summary_diag_returns_freshest_age() -> None:
    """``_build_evidence_summary_diag`` now returns a 2-tuple
    ``(diag, freshest_age)`` so the verdict hook can name the
    channel age that is doing the deferral.

    This test pins the new return-type contract independently of
    the log call so a future refactor that drops the freshest_age
    value is caught immediately (the type signature change is the
    primary contract; this test enforces the value-level semantics
    on top of the static type).
    """
    wd, clock = _idle_watchdog_3_make_watchdog(activity_ttl=1000.0)
    wd.record_activity()  # at t=0
    clock.advance(60.0)
    wd.record_mcp_tool_call()  # at t=60 (stdout stays at t=0)
    clock.advance(55.0)  # total elapsed = 115s

    diag, freshest_age = wd._build_evidence_summary_diag(clock.monotonic())
    assert isinstance(diag, dict)
    assert "evidence_summary" in diag
    assert diag["active_channel"] == "mcp_tool"
    # freshest_age must equal the mcp_tool channel age (55.0s), NOT
    # the stdout idle elapsed (115.0s).
    assert freshest_age == 55.0, (
        f"freshest_age must be the mcp_tool channel age (55.0s), got {freshest_age}"
    )

    # When the channels are stale (no fresh channel) freshest_age is None.
    wd2, clock2 = _idle_watchdog_3_make_watchdog(activity_ttl=10.0)
    wd2.record_activity()  # at t=0
    clock2.advance(20.0)  # past idle AND past the 10s TTL
    diag2, freshest_age2 = wd2._build_evidence_summary_diag(clock2.monotonic())
    assert diag2["active_channel"] == "none"
    assert freshest_age2 is None


# === consolidated from test_idle_watchdog_4.py ===
def test_check_process_result_nonzero_exit_calls_teardown_subtree(tmp_path: Path) -> None:
    """When the host process exits with a non-zero code, ``check_process_result``
    calls ``teardown_subtree`` on the handle's PID before raising
    ``AgentInvocationError``.

    This locks the AC-08 error/crash path: subagents must not outlive the
    phase even when the host crashes.
    """
    handle = _FakeHandle(returncode=1, pid=1234)

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree") as mock_teardown,
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            handle,
            "test-agent",
            parsed_output=[],
            check_options=None,
        )

    mock_teardown.assert_called_once_with(1234)
    # Also verify the real function can still be imported (sanity).
    assert teardown_subtree is not None


# === consolidated from test_idle_watchdog_4.py ===
def test_check_process_result_missing_completion_evidence_calls_teardown_subtree(
    tmp_path: Path,
) -> None:
    """When a completion-enforcing agent exits without required completion
    evidence, ``check_process_result`` calls ``teardown_subtree`` before
    raising ``AgentInvocationError``.
    """
    handle = _FakeHandle(returncode=0, pid=5678)
    options = CompletionCheckOptions(
        execution_strategy=_CompletionEnforcingStrategy(),
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=None),
    )

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree") as mock_teardown,
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            handle,
            "test-agent",
            parsed_output=[],
            check_options=options,
        )

    mock_teardown.assert_called_once_with(5678)


# === consolidated from test_idle_watchdog_4.py ===
def test_check_process_result_error_path_does_not_mutate_clock() -> None:
    """The error-path teardown call must not advance the injected FakeClock.

    This is a regression guard: the completion check should be a pure
    decision + side-effect (teardown), not a wall-clock wait.
    """
    clock = FakeClock(start=0.0)
    handle = _FakeHandle(returncode=1, pid=9999)

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree"),
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            handle,
            "test-agent",
            parsed_output=[],
            check_options=None,
            _clock=clock,
        )

    assert clock.monotonic() == 0.0


# === consolidated from test_idle_watchdog_4.py ===
def test_record_mcp_tool_call_does_not_mutate_last_activity() -> None:
    """``record_mcp_tool_call`` updates the mcp_tool channel timestamp but
    does NOT touch ``_last_activity`` (the stdout baseline)."""
    wd, clock = _idle_watchdog_4_make_watchdog()
    wd.record_activity()
    clock.advance(1.0)
    baseline = wd._last_activity
    now = clock.monotonic()
    wd.record_mcp_tool_call(now=now)
    assert wd._last_activity == baseline
    assert wd._last_mcp_tool_call_at == now


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_does_not_mutate_last_activity() -> None:
    """``record_subagent_work`` updates the subagent channel timestamp but
    does NOT touch ``_last_activity`` (the stdout baseline)."""
    wd, clock = _idle_watchdog_4_make_watchdog()
    wd.record_activity()
    clock.advance(1.0)
    baseline = wd._last_activity
    now = clock.monotonic()
    wd.record_subagent_work(now=now)
    assert wd._last_activity == baseline
    assert wd._last_subagent_progress_at == now


# === consolidated from test_idle_watchdog_4.py ===
def test_record_workspace_event_weight_zero_does_not_advance_channel() -> None:
    """A workspace event with ``weight=0.0`` is short-circuited: the channel
    timestamp, counter, and kind counter are NOT updated."""
    wd, _ = _idle_watchdog_4_make_watchdog()
    wd.record_workspace_event(kind=WorkspaceChangeKind.OTHER, weight=0.0)
    assert wd.workspace_kind_counts == {}
    summary = wd.last_evidence_summary(0.0)
    workspace_summary = summary.channels[-1]
    assert workspace_summary.channel_name == ChannelName.WORKSPACE
    assert workspace_summary.last_at is None


# === consolidated from test_idle_watchdog_4.py ===
def test_record_workspace_event_source_weight_advances_channel() -> None:
    """A workspace event with ``kind=SOURCE`` and ``weight=1.0`` advances the
    workspace channel timestamp and the per-kind source counter, and the
    channel summary reports ``can_defer=True``."""
    wd, clock = _idle_watchdog_4_make_watchdog()
    now = clock.monotonic()
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0, now=now)
    assert wd.workspace_kind_counts == {"source": 1}
    assert wd._last_workspace_event_at == now
    summary = wd.last_evidence_summary(now)
    workspace_summary = summary.channels[-1]
    assert workspace_summary.channel_name == ChannelName.WORKSPACE
    assert workspace_summary.last_at == now
    assert workspace_summary.can_defer is True


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_strips_control_characters() -> None:
    """``record_subagent_work`` strips control characters from ``description``.

    Newlines, CRs, tabs, and other C0 control codes from a raw provider
    line must NOT survive into the operator-visible
    ``subagent_activity`` field. A leaked newline would split a single
    waiting-status line into many rows in the UI.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="hello\nworld\rmore\tchars\x00\x01")
    assert "\n" not in (wd._last_subagent_progress_description or "")
    assert "\r" not in (wd._last_subagent_progress_description or "")
    assert "\t" not in (wd._last_subagent_progress_description or "")
    assert "\x00" not in (wd._last_subagent_progress_description or "")
    assert "\x01" not in (wd._last_subagent_progress_description or "")
    assert wd._last_subagent_progress_description == "helloworldmorechars"


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_strips_ansi_escapes() -> None:
    """ANSI CSI / OSC sequences are stripped from the description.

    A raw provider line like ``"\\x1b[31mred\\x1b[0m text"`` must lose
    the ESC bytes so the terminal does not interpret the colour code
    inside operator-visible waiting-status output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="\x1b[31mhello\x1b[0m world")
    stored = wd._last_subagent_progress_description
    assert stored is not None
    assert "\x1b" not in stored
    # The text content survives; only the escape introducer is removed.
    assert "hello" in stored
    assert "world" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_tool_arguments() -> None:
    """A description that contains ``"arguments": "<secret>"`` has the value redacted."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='tool {"arguments": "secret_payload_value"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret_payload_value" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_sensitive_paths() -> None:
    """A description mentioning sensitive roots (/etc, /proc, /sys, /root, ~/.ssh)
    has the sensitive marker replaced with ``<redacted>`` so the path does
    not leak verbatim into operator-visible text."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="reading /etc/passwd then /proc/self/maps")
    stored = wd._last_subagent_progress_description or ""
    assert "/etc/" not in stored
    assert "/proc/" not in stored
    assert stored.count("<redacted>") >= 2


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_bearer_token() -> None:
    """A description containing ``Authorization: Bearer <token>`` has the
    bearer prefix redacted (the marker reveals the leak category without
    echoing the token)."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="hdr: Authorization: Bearer abc123token")
    stored = wd._last_subagent_progress_description or ""
    assert "Bearer" not in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_lowercase_bearer_token() -> None:
    """A description containing ``authorization: bearer <token>`` (all lowercase)
    has the bearer prefix redacted. This is the analysis-feedback reproducer:
    a case-sensitive regex previously missed lowercase authorization headers
    and let ``SECRET123`` leak into the operator-visible
    ``subagent_activity`` field on ``WaitingStatusEvent``.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="hdr: authorization: bearer SECRET123")
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET123" not in stored, (
        f"lowercase bearer token 'SECRET123' must NOT leak, got: {stored!r}"
    )
    assert "bearer" not in stored, f"lowercase 'bearer' marker must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_uppercase_bearer_token() -> None:
    """A description containing ``AUTHORIZATION: BEARER <token>`` (all uppercase)
    has the bearer prefix redacted. Mirrors the lowercase regression test to
    pin both ends of the case-insensitive contract.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="hdr: AUTHORIZATION: BEARER UPPERSECRET")
    stored = wd._last_subagent_progress_description or ""
    assert "UPPERSECRET" not in stored, (
        f"uppercase bearer token 'UPPERSECRET' must NOT leak, got: {stored!r}"
    )
    assert "BEARER" not in stored, f"uppercase 'BEARER' marker must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_mixed_case_bearer_token() -> None:
    """A description containing ``AuThOrIzAtIoN: BeArEr <token>`` (mixed case)
    has the bearer prefix redacted. Locks down the case-insensitive contract
    for arbitrary mixed-case header variants.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="hdr: AuThOrIzAtIoN: BeArEr MiXeDcAsE")
    stored = wd._last_subagent_progress_description or ""
    assert "MiXeDcAsE" not in stored, (
        f"mixed-case bearer token 'MiXeDcAsE' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_private_key_marker() -> None:
    """A description containing a PEM ``-----BEGIN ... PRIVATE KEY-----``
    marker is redacted to prevent private-key fragments leaking into logs."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="key fragment -----BEGIN RSA PRIVATE KEY----- data")
    stored = wd._last_subagent_progress_description or ""
    assert "PRIVATE KEY" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_truncates_to_200_chars() -> None:
    """A description longer than 200 chars after sanitization is truncated."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="a" * 500)
    stored = wd._last_subagent_progress_description or ""
    assert len(stored) == 200


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_only_whitespace_stores_empty() -> None:
    """A description that is purely whitespace (after sanitization) stores
    an empty string so the subscriber does not render ``subagent=``."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description="   \n\n  \t\t  ")
    stored = wd._last_subagent_progress_description
    assert stored == ""


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_none_leaves_field_none() -> None:
    """``record_subagent_work(description=None)`` does NOT update the
    stored description (preserves the prior value). This is the
    legacy behavior used by tests that exercise the channel
    timestamp without supplying a description."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    prior = wd._last_subagent_progress_description
    wd.record_subagent_work(description=None)
    assert wd._last_subagent_progress_description == prior


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_does_not_mutate_last_activity() -> None:
    """``record_subagent_work(description=...)`` does NOT touch the
    stdout baseline ``_last_activity``. The description update is a
    presentation-layer concern; it must not perturb the idle deadline."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_activity()
    _clock.advance(1.0)
    baseline = wd._last_activity
    wd.record_subagent_work(description="anything goes here")
    assert wd._last_activity == baseline


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_well_formed_escaped_quote() -> None:
    """A well-formed JSON ``"arguments": "secret\\"tail"`` is fully redacted.

    The strict regex ``"[^"\\n]*"`` would stop at the first
    unescaped quote and leave ``tail"`` visible. The sanitizer's
    multi-pass redaction (JSON parser + strict regex + fallback
    regex) handles the escaped quote and redacts the entire value.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    # Source: {"arguments":"secret\"tail"}  (the \" is a real escape)
    wd.record_subagent_work(description='{"arguments":"secret\\"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "tail" not in stored, f"escaped-quote suffix 'tail' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_malformed_inner_quote() -> None:
    """A malformed JSON with an UNESCAPED inner quote is fully redacted.

    The input ``{"arguments":"secret"tail"}`` is not valid JSON; the
    JSON parser rejects it and the fallback regex
    ``_SENSITIVE_MARKER_FALLBACK_RE`` matches the marker, opening
    quote, and everything up to the next JSON boundary character
    so the trailing ``tail"}`` never reaches operator-visible output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"arguments":"secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "tail" not in stored, f"malformed-JSON suffix 'tail' must be redacted, got: {stored!r}"
    assert "secret" not in stored, (
        f"malformed-JSON prefix 'secret' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_malformed_args_inner_quote() -> None:
    """A malformed JSON ``args`` value is fully redacted.

    The analysis feedback confirmed that ``args`` was missing from the
    fallback regex, so ``{"args":"secret"tail"}`` leaked the suffix.
    This test pins the fix: the fallback regex MUST treat ``args`` the
    same as ``arguments`` and redact the entire malformed value.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"args":"secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "tail" not in stored, (
        f"malformed-JSON args suffix 'tail' must be redacted, got: {stored!r}"
    )
    assert "secret" not in stored, (
        f"malformed-JSON args prefix 'secret' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_escaped_prompt_content() -> None:
    """An escaped-quote ``prompt`` / ``content`` value is fully redacted.

    Regression for the analysis-feedback case: a raw provider line
    like ``{"prompt": "say \\"hi\\" please"}`` must NOT leak the
    ``"hi" please"`` suffix into operator-visible output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"prompt": "say \\"hi\\" please"}')
    stored = wd._last_subagent_progress_description or ""
    assert "please" not in stored, (
        f"escaped-quote suffix 'please' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_escaped_file_path() -> None:
    """An escaped-quote ``file_path`` value is fully redacted.

    ``{"file_path": "/etc/secret\\"name"}`` must NOT leak the
    ``name"`` suffix into operator-visible output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"file_path": "/etc/secret\\"name"}')
    stored = wd._last_subagent_progress_description or ""
    assert "name" not in stored, (
        f"escaped-quote file_path suffix 'name' must be redacted, got: {stored!r}"
    )
    assert "/etc/" not in stored, f"sensitive path /etc/ must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_bearer_token_with_quotes() -> None:
    """A bearer token header with a quoted suffix is fully redacted.

    The provider format ``Authorization: Bearer abc"def`` (raw
    provider line, not JSON-wrapped) must NOT leak the ``def``
    suffix. The path regex
    ``_SENSITIVE_PATH_TOKEN_RE`` matches
    ``Authorization\\s*:\\s*Bearer[^\\n]*`` and consumes the rest of
    the line, so the quoted suffix is redacted.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='Authorization: Bearer abc"def')
    stored = wd._last_subagent_progress_description or ""
    assert "def" not in stored, f"bearer token suffix 'def' must be redacted, got: {stored!r}"
    assert "Bearer" not in stored, f"bearer token prefix 'Bearer' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_input_field_with_quotes() -> None:
    """An ``input`` field with escaped quotes is fully redacted.

    ``{"input": "echo \\"hello\\" world"}`` must NOT leak the
    ``world"`` suffix into operator-visible output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"input": "echo \\"hello\\" world"}')
    stored = wd._last_subagent_progress_description or ""
    assert "world" not in stored, f"input field suffix 'world' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_repeated_escaped_quotes() -> None:
    """Multiple escaped quotes in a single value are all redacted.

    ``{"content": "say \\"hi\\" then \\"bye\\" now"}`` must NOT
    leak any of ``hi``, ``bye``, or ``now`` into operator-visible
    output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"content": "say \\"hi\\" then \\"bye\\" now"}')
    stored = wd._last_subagent_progress_description or ""
    for forbidden in ("hi", "bye", "now"):
        assert forbidden not in stored, (
            f"escaped-quote value {forbidden!r} must be redacted, got: {stored!r}"
        )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_reproducer_no_leaked_suffix() -> None:
    """Reproducer for the analysis-feedback reproducer.

    The exact input from the analysis feedback (``{"arguments":"secret\\"tail"}``)
    must produce sanitized output that does NOT contain the
    forbidden ``tail`` suffix. This is the contract that motivated
    the fallback regex fix.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    # Reproducer line verbatim from the analysis feedback.
    wd.record_subagent_work(description='{"arguments":"secret\\"tail"}')
    stored = wd._last_subagent_progress_description or ""
    # The reproducer must NOT print leaked suffix text.
    assert "tail" not in stored, (
        f"analysis-feedback reproducer: 'tail' suffix leaked, got: {stored!r}"
    )
    # The redaction marker must be present.
    assert "<redacted>" in stored
    # The output must be a safe operator-visible summary (no JSON
    # structural characters that could be exploited).
    assert "{" not in stored or "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_nested_object_arguments() -> None:
    """Nested OBJECT under ``arguments`` is redacted in full.

    The pre-fix ``_redact_json_values`` only redacted scalar values
    under sensitive keys. A nested object like
    ``{"arguments": {"command": "rm -rf /", "token": "abc"}}`` was
    walked recursively -- only the ``token`` field was redacted,
    and the ``command`` field leaked into operator-visible output.

    The fix: when a key is sensitive, the ENTIRE value is replaced
    with ``<redacted>`` regardless of whether that value is a
    scalar, an object, or a list. The surrounding JSON structure
    remains well-formed.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"arguments": {"command": "rm -rf /", "token": "abc"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "rm -rf /" not in stored, f"nested 'command' value must NOT leak, got: {stored!r}"
    assert "token" not in stored, f"nested 'token' key must NOT leak, got: {stored!r}"
    assert "abc" not in stored, f"nested 'abc' value must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_nested_list_arguments() -> None:
    """Nested LIST under ``arguments`` is redacted in full.

    A list value like ``["rm -rf /", "secret"]`` under a
    sensitive key must NOT have any of its elements leak into
    operator-visible output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"arguments": ["rm -rf /", "secret"]}')
    stored = wd._last_subagent_progress_description or ""
    assert "rm -rf /" not in stored
    assert "secret" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_nested_object_input() -> None:
    """Nested OBJECT under ``input`` is redacted in full."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"input": {"echo": "hello", "user": "admin"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "hello" not in stored
    assert "admin" not in stored
    assert "echo" not in stored
    assert "user" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_nested_array_content() -> None:
    """Nested ARRAY under ``content`` is redacted in full."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"content": [{"text": "secret message"}]}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret message" not in stored
    assert "text" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_nested_prompt() -> None:
    """Nested OBJECT under ``prompt`` is redacted in full."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"prompt": {"role": "system", "content": "do the thing"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "do the thing" not in stored
    assert "role" not in stored
    assert "system" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_nested_file_path() -> None:
    """Nested OBJECT under ``file_path`` is redacted in full.

    The pre-fix walker would have leaked the ``name`` field of a
    nested object under ``file_path``. The fix redacts the whole
    value in one shot.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"file_path": {"path": "/etc/passwd", "name": "shadow"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "/etc/passwd" not in stored
    assert "shadow" not in stored
    assert "name" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_reproducer_nested_token() -> None:
    """Reproducer for the analysis-feedback nested-token case.

    The exact nested payload from the analysis feedback
    (``arguments`` holding a ``command`` and ``token`` pair under
    a nested object structure) must produce sanitized output that
    does NOT contain the forbidden ``command`` text or the
    ``token`` value.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(
        description='{"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}'
    )
    stored = wd._last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' must NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' must NOT leak, got: {stored!r}"
    assert "secret" not in stored, f"nested 'secret' value must NOT leak, got: {stored!r}"
    # The non-sensitive key 'name' is preserved so the operator
    # still sees WHICH tool was invoked.
    assert "tool" in stored, f"non-sensitive 'name' key should survive, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_embedded_json_after_prefix() -> None:
    """A JSON fragment embedded AFTER free-form text is redacted in full.

    Analysis-feedback reproducer: lines from raw provider output
    frequently mix free-form text with one or more embedded JSON
    fragments (``prefix {"prompt": "hello, world"}``). The previous
    sanitizer only inspected lines starting with ``{`` or ``[``,
    so the fragment after ``prefix `` was missed. The pre-fix
    fallback regex stopped at the first comma and left
    ``, world"}`` visible in operator output.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='prefix {"prompt": "hello, world"}')
    stored = wd._last_subagent_progress_description or ""
    assert "world" not in stored, f"comma-bearing value 'world' must NOT leak, got: {stored!r}"
    assert "hello" not in stored, (
        f"comma-bearing value prefix 'hello' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored, f"<redacted> marker must appear, got: {stored!r}"


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_embedded_json_with_comma() -> None:
    """A JSON fragment with an embedded comma is redacted in full.

    Analysis-feedback reproducer: ``prefix {"arguments": "abc,def", "x":1}``
    previously left ``,def"`` visible because the fallback regex
    stopped at the first comma.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='prefix {"arguments": "abc,def", "x":1}')
    stored = wd._last_subagent_progress_description or ""
    assert "abc" not in stored, f"comma-bearing value 'abc' must NOT leak, got: {stored!r}"
    assert "def" not in stored, f"comma-bearing value 'def' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_embedded_nested_object() -> None:
    r"""A nested-object JSON fragment embedded after free-form text is redacted.

    Analysis-feedback reproducer:
    ``prefix {"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}``
    was completely UN-redacted by the previous sanitizer because the
    line did not START with ``{`` (the JSON parse path was skipped)
    and the fallback regex's `.*?` non-greedy with positive-lookahead
    ``[,\}\]\n]`` consumed only a partial value.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(
        description=(
            'prefix {"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}'
        )
    )
    stored = wd._last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' must NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' must NOT leak, got: {stored!r}"
    assert "secret" not in stored, f"nested 'secret' value must NOT leak, got: {stored!r}"
    # The non-sensitive key 'name' is preserved so the operator
    # still sees WHICH tool was invoked.
    assert "tool" in stored, f"non-sensitive 'name' key should survive, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_multiple_embedded_fragments() -> None:
    """Multiple JSON fragments on a single line are ALL redacted.

    Verifies the scanner finds and walks every ``{...}`` it can
    parse rather than only the first one.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(
        description=('prefix {"arguments": "first"} middle {"arguments": "second"}')
    )
    stored = wd._last_subagent_progress_description or ""
    assert "first" not in stored, f"first fragment 'first' must NOT leak, got: {stored!r}"
    assert "second" not in stored, f"second fragment 'second' must NOT leak, got: {stored!r}"
    assert stored.count("<redacted>") == 2, f"both fragments must be redacted, got: {stored!r}"


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_handles_malformed_inner_quote_after_prefix() -> None:
    """Malformed JSON with unescaped inner quote embedded after a
    prefix is fully redacted (fallback regex handles it).
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='prefix {"arguments": "secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret" not in stored, f"malformed-JSON 'secret' must NOT leak, got: {stored!r}"
    assert "tail" not in stored, f"malformed-JSON 'tail' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_args_payload() -> None:
    """A description containing ``"args": "<secret>"`` has the
    ``args`` value replaced with ``<redacted>``.

    This is the analysis-feedback reproducer for the missing
    ``args`` entry in ``_SENSITIVE_JSON_KEYS``. The
    ``tool_call`` line ``{"type":"tool_call","args":{<payload>}}``
    previously leaked the nested payload via the recursive
    ``_redact_json_values`` walk because the ``args`` key was
    not in the sensitive set.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(
        description='{"type":"tool_call","name":"bash","args":{"command":"rm -rf /","token":"abc"}}'
    )
    stored = wd._last_subagent_progress_description or ""
    # The payload contents MUST NOT leak.
    assert "rm -rf /" not in stored, (
        f"nested 'command' value 'rm -rf /' must NOT leak, got: {stored!r}"
    )
    assert "abc" not in stored, f"nested 'token' value 'abc' must NOT leak, got: {stored!r}"
    assert "command" not in stored, f"nested 'command' KEY must NOT leak, got: {stored!r}"
    assert "token" not in stored, f"nested 'token' KEY must NOT leak, got: {stored!r}"
    # The non-sensitive 'type' and 'name' fields are preserved so
    # the operator still sees WHICH tool was invoked.
    assert "tool_call" in stored, (
        f"non-sensitive 'type' value 'tool_call' should survive, got: {stored!r}"
    )
    assert "bash" in stored, f"non-sensitive 'name' value 'bash' should survive, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_scalar_args_value() -> None:
    """A scalar ``args`` value is redacted (not just nested objects).

    The analysis-feedback fix must also redact ``"args": "scalar"``
    (a scalar value, not a nested object). Pre-fix the scalar
    value walked recursively and was preserved as-is.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"name":"bash","args":"secret_payload_value"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret_payload_value" not in stored, (
        f"scalar 'args' value 'secret_payload_value' must NOT leak, got: {stored!r}"
    )
    assert "bash" in stored, f"non-sensitive 'name' value 'bash' should survive, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_list_args_value() -> None:
    """A LIST ``args`` value is redacted in full (no element leak)."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"args": ["rm -rf /", "secret"]}')
    stored = wd._last_subagent_progress_description or ""
    assert "rm -rf /" not in stored
    assert "secret" not in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_tool_call_line_with_nested_args() -> None:
    """The exact ``type=tool_call`` analysis-feedback reproducer line.

    The pre-fix ``_sanitize_subagent_description`` left the
    ``args`` payload intact because the ``args`` key was not in
    ``_SENSITIVE_JSON_KEYS``. This test pins the no-leak contract
    for the exact line shape used in the analysis-feedback probe:
    ``{"type":"tool_call","args":{"command":"echo secret","token":"abc123"}}``.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(
        description=(
            '{"type":"tool_call","name":"bash","args":{"command":"echo secret","token":"abc123"}}'
        )
    )
    stored = wd._last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' MUST NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' MUST NOT leak, got: {stored!r}"
    # The non-sensitive 'type' and 'name' fields are preserved.
    assert "tool_call" in stored
    assert "bash" in stored
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_mixed_case_prompt_key() -> None:
    """A description with ``\"Prompt\": \"<secret>\"`` has the value redacted.

    The JSON walker must normalize keys when checking the sensitive set;
    mixed-case provider keys must not leak just because they are capitalized.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"Prompt": "SECRET-upper"}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-upper" not in stored, (
        f"mixed-case 'Prompt' value 'SECRET-upper' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_mixed_case_arguments_key() -> None:
    """A description with ``\"Arguments\": {...}`` has the nested value redacted."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"Arguments": {"token": "SECRET-mixed"}}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-mixed" not in stored, (
        f"mixed-case 'Arguments' value 'SECRET-mixed' must NOT leak, got: {stored!r}"
    )
    assert "token" not in stored, f"nested sibling 'token' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_mixed_case_input_key() -> None:
    """A description with ``\"Input\": \"<secret>\"`` has the value redacted."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"Input": "SECRET-input"}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-input" not in stored, (
        f"mixed-case 'Input' value 'SECRET-input' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_mixed_case_content_key() -> None:
    """A description with ``\"Content\": \"<secret>\"`` has the value redacted."""
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"Content": "SECRET-content"}')
    stored = wd._last_subagent_progress_description or ""
    assert "SECRET-content" not in stored, (
        f"mixed-case 'Content' value 'SECRET-content' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_4.py ===
def test_record_subagent_work_description_redacts_malformed_mixed_case_arguments() -> None:
    """A malformed JSON value under ``\"Arguments\"`` is fully redacted.

    The fallback regex is also case-insensitive, so mixed-case markers in
    malformed JSON are caught exactly like lowercase markers.
    """
    wd, _clock = _idle_watchdog_4_make_watchdog()
    wd.record_subagent_work(description='{"Arguments": "secret"tail"}')
    stored = wd._last_subagent_progress_description or ""
    assert "secret" not in stored, (
        f"malformed mixed-case 'Arguments' prefix 'secret' must NOT leak, got: {stored!r}"
    )
    assert "tail" not in stored, (
        f"malformed mixed-case 'Arguments' suffix 'tail' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_source_changes_defer_no_output_deadline(tmp_path: Path) -> None:
    """A source-code change defers the NO_OUTPUT_DEADLINE verdict.

    Sequence:
      - watchdog with TTL=1000s, idle=0.1s
      - record_activity at t=0 (stdout baseline)
      - advance 1s past idle
      - source event (kicks workspace channel fresh)
      - evaluate -> CONTINUE (deferred via workspace)
    """
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/src/foo.py")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    # The per-kind counter received the real SOURCE kind.
    assert wd.workspace_kind_counts == {"source": 1}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_log_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    """A log-file change does NOT defer the NO_OUTPUT_DEADLINE
    verdict under the default conservative policy (log=0.0)."""
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    # The per-kind counter did NOT receive the dropped log event.
    assert wd.workspace_kind_counts == {}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_cache_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    """A ``__pycache__`` file change does NOT defer the verdict
    under the default conservative policy (cache=0.0)."""
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/__pycache__/foo.cpython-312.pyc")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.workspace_kind_counts == {}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_artifact_changes_do_not_defer_no_output_deadline(tmp_path: Path) -> None:
    """A ``.agent/artifacts`` file change does NOT defer the verdict
    under the default conservative policy (artifact=0.0).

    This is the PA-001 closure: pre-fix, the ``.agent`` top-level
    was in ``CACHE_PARENT_DIRS`` and this test failed. The fixed
    rule order checks ``.agent/tmp``/``.agent/raw`` explicitly and
    reserves ``.agent/artifacts`` for ARTIFACT.
    """
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.workspace_kind_counts == {}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_mixed_source_and_log_only_source_counts(tmp_path: Path) -> None:
    """A log event (dropped) followed by a source event (deferred)
    results in CONTINUE; the dropped log event does not block the
    source event's deferral."""
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    monitor = _make_production_monitor(wd, tmp_path)
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")  # dropped (log, weight 0.0)
    monitor.record_event("/repo/src/foo.py")  # deferred (source, weight 1.0)
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    # Only the source event was recorded.
    assert wd.workspace_kind_counts == {"source": 1}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_custom_weights_can_count_logs(tmp_path: Path) -> None:
    """An operator opts log files in by setting
    ``weights['log'] = 1.0``; the watchdog defers the verdict
    on a log event under the custom policy."""
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(
        activity_ttl=1000.0,
        workspace_change_weights={"source": 1.0, "log": 1.0},
    )
    wd.record_activity()
    monitor = _make_production_monitor(
        wd,
        tmp_path,
        weights={"source": 1.0, "log": 1.0},
    )
    clock.advance(1.0)
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.CONTINUE
    # The log event is recorded under the custom policy.
    assert wd.workspace_kind_counts == {"log": 1}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_dead_subagent_still_detected_when_log_file_written(tmp_path: Path) -> None:
    """When a subagent is alive but only writes a log file (no
    stdout, no tool calls, no source-code changes), the watchdog
    still detects the dead subagent at the regular idle window
    (NOT only at the cumulative WAITING_ON_CHILD ceiling).

    The log file alone does NOT defer the verdict (default policy).
    """
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog(
        idle_timeout=0.1,
        max_waiting=10.0,
        activity_ttl=30.0,
    )
    wd.record_activity()
    # A subagent work signal at t=0.
    wd.record_subagent_work()
    monitor = _make_production_monitor(wd, tmp_path)
    # Advance past the 30s default TTL so the subagent channel is stale.
    clock.advance(31.0)
    # A log file is written; dropped under default policy.
    monitor.record_event("/repo/agent.log")
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    # The fire is NO_OUTPUT_DEADLINE (regular idle path), not
    # CHILDREN_PERSIST_TOO_LONG. Pre-fix, the log file alone would
    # have deferred the verdict (every file counted).
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    # The log event was dropped.
    assert wd.workspace_kind_counts == {}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_truly_idle_session_terminated_on_time(tmp_path: Path) -> None:
    """A session with no activity on any channel is terminated
    no later than today. The new class-aware verdict does not
    make the watchdog more lenient toward truly dead sessions."""
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog()
    # No record_* calls. Advance past idle timeout.
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_fire_log_carries_per_kind_breakdown_in_extra(tmp_path: Path) -> None:
    """The NO_OUTPUT_DEADLINE fire log carries the per-kind
    workspace breakdown in the loguru ``extra=`` dict so the
    post-mortem reader sees WHICH kinds were active at the moment
    of the fire.

    PA-014: pytest's caplog does NOT capture loguru's bound
    record.extra dict. We use the loguru sink pattern (the same
    pattern as tests/agents/test_idle_watchdog_3.py:620-680)
    so the structured fields are observable via
    ``message.record['extra']``.
    """
    wd, clock = _idle_watchdog_workspace_smart__make_watchdog()
    monitor = _make_production_monitor(wd, tmp_path)
    # Drive a source event so the per-kind counter has data.
    wd.record_activity()
    monitor.record_event("/repo/src/foo.py")
    clock.advance(1.0)
    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        wd._handle_active_branch(clock.monotonic())
    finally:
        loguru_logger.remove(handler_id)

    fire_records = [m for m in captured if "FIRE reason=no_output_deadline" in m.record["message"]]
    assert fire_records
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    workspace_entry = next(
        e for e in bound_extra["evidence_summary"] if e["channel"] == "workspace"
    )

    # The single source event triggered the fire path; the per-kind
    # breakdown in the embedded diagnostic shows the source event.
    assert workspace_entry["kind_breakdown"] == {"source": 1}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_production_binding_threads_real_kind_to_counter(tmp_path: Path) -> None:
    """End-to-end: a WorkspaceMonitor with a real classifier AND
    the production-style 2-arg lambda binding threads the REAL
    kind to the watchdog's per-kind counter.

    PA-003 closure: pre-fix, the 0-arg bound-method form
    ``set_on_event(watchdog.record_workspace_event)`` meant the
    per-kind counter always received (OTHER, 1.0) defaults in
    production. This test proves the production-style 2-arg
    lambda binding (now used in both ``_process_reader.py`` and
    ``_pty_line_reader.py``) threads the real classification.
    """
    wd, _ = _idle_watchdog_workspace_smart__make_watchdog()
    monitor = _make_production_monitor(wd, tmp_path)
    # Source event.
    monitor.record_event("/repo/src/foo.py")
    assert wd.workspace_kind_counts == {"source": 1}
    # Log event (dropped).
    monitor.record_event("/repo/agent.log")
    assert wd.workspace_kind_counts == {"source": 1}
    # Cache event (dropped).
    monitor.record_event("/repo/__pycache__/foo.pyc")
    assert wd.workspace_kind_counts == {"source": 1}
    # Artifact event (dropped).
    monitor.record_event("/repo/.agent/artifacts/plan.json")
    assert wd.workspace_kind_counts == {"source": 1}


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_workspace_change_kind_canonical_module() -> None:
    """``WorkspaceChangeKind`` lives in its canonical leaf module
    so both the classifier and ``TimeoutPolicy`` can import from
    it without triggering a circular import via
    ``ralph.agents.invoke.__init__``."""
    canonical_kind = WorkspaceChangeKind

    assert canonical_kind is WorkspaceChangeKind
    assert WorkspaceChangeKind.SOURCE.value == "source"
    assert WorkspaceChangeKind.LOG.value == "log"
    assert WorkspaceChangeKind.CACHE.value == "cache"
    assert WorkspaceChangeKind.ARTIFACT.value == "artifact"
    assert WorkspaceChangeKind.OTHER.value == "other"


# === consolidated from test_idle_watchdog_workspace_smart_filter.py ===
def test_default_weights_match_module_constant() -> None:
    """The classifier's default policy is identical to the
    module-level ``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS`` constant
    so the policy and the classifier cannot drift independently."""
    classifier = WorkspaceChangeClassifier()
    assert classifier.weights == dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)


# === consolidated from test_idle_watchdog_1.py ===
class _WatchdogOptions:
    idle_timeout: Any
    drain_window: float = 0.5
    max_waiting: Any = None
    start: float = 0.0
    max_session: Any = None
    listener: Any = None
    suspect: Any = None
    status_interval: Any = None
    no_progress_ceiling: Any = None


# === consolidated from test_idle_watchdog_2.py ===
class _WatchdogOptions:
    idle_timeout: Any
    drain_window: float = 0.5
    max_waiting: Any = None
    start: float = 0.0
    max_session: Any = None
    listener: Any = None
    suspect: Any = None
    status_interval: Any = None
    no_progress_ceiling: Any = None


# === consolidated from test_idle_watchdog_4.py ===
class _FakeHandle:
    """Minimal handle stand-in for completion-check tests."""

    def __init__(self, returncode: int, pid: int = 42) -> None:
        self.returncode = returncode
        self.pid = pid
        self.stderr = _FakeStderr("boom")


# === consolidated from test_idle_watchdog_4.py ===
class _FakeStderr:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self, size: int | None = None) -> str:
        if size is None or size < 0:
            return self._text
        return self._text[:size]  # truncate; no marker (test asserts behavior below cap)


# === consolidated from test_idle_watchdog_4.py ===
class _CompletionEnforcingStrategy:
    """Strategy that supports completion enforcement and reports incomplete exit."""

    def classify_exit(
        self,
        handle: object,
        signals: object,
        liveness_probe: object | None = None,
    ) -> AgentExecutionState:
        del handle, signals, liveness_probe
        return AgentExecutionState.RESUMABLE_CONTINUE

    def supports_session_continuation(self) -> bool:
        return False

    def supports_completion_enforcement(self) -> bool:
        return True


# === consolidated from test_idle_watchdog_no_output_at_start.py ===
class TestNoOutputAtStart:
    """Tests for NO_OUTPUT_AT_START fire path."""

    def test_fires_after_no_output_at_start_seconds_with_zero_activity(self) -> None:
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_does_not_fire_when_record_activity_called(self) -> None:
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
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
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=None,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_does_not_fire_before_threshold(self) -> None:
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(59)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_does_not_fire_with_fresh_channel_evidence(self) -> None:
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
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
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
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
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None

    def test_does_not_fire_in_active_state_with_recorded_activity(self) -> None:
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()
        watchdog.record_activity()

        clock.advance(61)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict != WatchdogVerdict.FIRE

    def test_fire_sets_last_fire_reason(self) -> None:
        watchdog, clock = _idle_watchdog_no_output_at_sta_make_watchdog(
            idle_timeout=300.0,
            no_output_at_start_seconds=60.0,
        )
        watchdog.record_invocation_start()

        clock.advance(61)
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_idle_watchdog_no_output_at_start_lifecycle.py ===
class TestNoOutputAtStartLifecycleBypass:
    """Test reproducing the bug where a lifecycle frame bypasses NO_OUTPUT_AT_START."""

    def test_lifecycle_activity_does_not_bypass_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()

        # Advance the clock slightly to simulate some startup delay
        clock.advance(5.0)

        # Simulate a lifecycle activity line (which should NOT count as meaningful output)
        watchdog.record_lifecycle_activity()

        # Advance the clock past the 30.0s threshold since start
        clock.advance(26.0)

        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # Lifecycle activity must NOT count as meaningful output, so
        # NO_OUTPUT_AT_START still fires at the threshold when the agent
        # is ACTIVE.  (When the execution strategy reports WAITING_ON_CHILD,
        # the separate WAITING_ON_CHILD early-exit defers instead -- see
        # tests/agents/idle_watchdog/test_no_output_at_start.py.)
        assert verdict == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


# === consolidated from test_idle_watchdog_no_output_at_start_lifecycle.py ===
class TestChannelEvidenceDeferNoOutputAtStart:
    """Proves that waiting-evidence channels suppress NO_OUTPUT_AT_START."""

    def test_subagent_work_progress_defers_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            activity_evidence_ttl_seconds=100.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()
        watchdog.record_subagent_work()

        clock.advance(31.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None

    def test_workspace_event_progress_defers_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            activity_evidence_ttl_seconds=100.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()
        watchdog.record_workspace_event(kind=WorkspaceChangeKind.SOURCE)

        clock.advance(31.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None

    def test_mcp_tool_call_progress_defers_no_output_at_start(self) -> None:
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            activity_evidence_ttl_seconds=100.0,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock)

        watchdog.record_invocation_start()
        watchdog.record_mcp_tool_call()

        clock.advance(31.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None


# === consolidated from test_idle_watchdog_no_output_at_start_lifecycle.py ===
class TestNoOutputAtStartLiveCorroborationDefer:
    """Live-corroboration deferral for NO_OUTPUT_AT_START.

    The watchdog must call self._safe_corroborate() LIVE inside
    _evaluate_no_output_at_start (not read the stale self._last_alive_by
    field). When the LIVE snapshot reports alive_by != None, the
    NO_OUTPUT_AT_START fire is deferred.

    Three tests pin the contract:

    1. test_defers_no_output_at_start_when_live_corroborator_reports_alive_by:
       live corroborator returns a snapshot with alive_by=OS_DESCENDANT;
       assert CONTINUE and that the corroborator was invoked LIVE during
       evaluate (not the stale last_alive_by field).

    2. test_defers_no_output_at_start_when_cumulative_waiting_on_child_positive:
       drive one full WAITING_ON_CHILD cycle to accumulate waiting time,
       reset to ACTIVE state, advance past no_output_at_start_seconds with
       no new activity and no live corroborator alive_by; assert CONTINUE.

    3. test_still_fires_when_live_corroborator_returns_empty_and_no_waiting_run:
       corroborator returns empty CorroborationSnapshot(); no prior
       waiting run; advance past no_output_at_start_seconds; assert FIRE.
    """

    def test_defers_no_output_at_start_when_live_corroborator_reports_alive_by(self) -> None:
        """Live corroborator alive_by signal defers NO_OUTPUT_AT_START.

        The corroborator returns ``alive_by=FRESH_PROGRESS`` (a fresh
        live-child signal). The watchdog must defer NO_OUTPUT_AT_START
        because the LIVE corroborator confirms a live child agent. The
        prove-the-call assertion verifies that the corroborator was
        invoked LIVE during evaluate() (not via the stale
        ``self._last_alive_by`` field which is only populated
        post-fire by NO_PROGRESS_QUIET).

        Idle_timeout_seconds=300 (well past no_output_at_start_seconds=60)
        so the watchdog does NOT fire NO_OUTPUT_DEADLINE either -- the
        final verdict is CONTINUE because no_output_at_start deferred
        AND the agent is not past idle_timeout.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        call_count: list[int] = [0]

        def _live_corroborator() -> CorroborationSnapshot:
            call_count[0] += 1
            return CorroborationSnapshot(
                alive_by=AliveBy.FRESH_PROGRESS,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_live_corroborator)

        watchdog.record_invocation_start()

        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (live corroborator reports alive_by=FRESH_PROGRESS), got {verdict}"
        )
        assert watchdog.last_fire_reason is None, (
            f"expected last_fire_reason=None (no fire happened), got {watchdog.last_fire_reason}"
        )
        # Proves the LIVE call semantics: the corroborator was invoked
        # during evaluate(), not by reading the stale last_alive_by field.
        assert call_count[0] >= 1, (
            f"expected the live corroborator to be invoked at least once"
            f" during evaluate(), got {call_count[0]} invocations"
        )

    def test_stale_alive_by_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy`` states do NOT defer NO_OUTPUT_AT_START.

        The watchdog must distinguish FRESH corroboration evidence
        (``FRESH_PROGRESS``, ``FRESH_HEARTBEAT_ONLY`` -- a child that
        has produced recent progress / heartbeat signal) from STALE
        evidence (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
        ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
        ``STALE_LABEL_ONLY`` -- a child that has stopped producing
        fresh evidence). Only fresh states defer the short
        NO_OUTPUT_AT_START kill; stale evidence falls through to
        ``_gate_fire`` so the StuckClassifier sees the live snapshot
        and the short kill still applies.

        Pre-fix, the deferral gate was ``corroboration.alive_by is
        not None``, which deferred on every AliveBy value including
        stale states. A wedged startup that reported
        ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` would defer the short
        kill and never reach ``_gate_fire`` / StuckClassifier. The
        post-fix gate is ``_alive_by_is_fresh(...)`` which returns
        True ONLY for ``FRESH_PROGRESS`` and ``FRESH_HEARTBEAT_ONLY``.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _stale_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_stale_corroborator)
        watchdog.record_invocation_start()

        # Advance past the short NO_OUTPUT_AT_START threshold (60s).
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # Stale AliveBy MUST NOT defer -- the short kill fires.
        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy MUST NOT defer"
            f" NO_OUTPUT_AT_START), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got {watchdog.last_fire_reason}"
        )

    def test_cpu_idle_while_alive_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy.CPU_IDLE_WHILE_ALIVE`` does NOT defer NO_OUTPUT_AT_START.

        Mirrors ``test_stale_alive_by_does_not_defer_no_output_at_start``
        for a different stale AliveBy value. The wedged-startup pattern
        applies: the descendant process is alive in the OS process
        tree but has not used CPU recently -- the process is hung
        and NO_OUTPUT_AT_START MUST still fire.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _cpu_idle_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.CPU_IDLE_WHILE_ALIVE,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_cpu_idle_corroborator)
        watchdog.record_invocation_start()
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy.CPU_IDLE_WHILE_ALIVE MUST NOT defer),"
            f" got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_log_stale_while_alive_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy.LOG_STALE_WHILE_ALIVE`` does NOT defer NO_OUTPUT_AT_START."""
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _log_stale_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.LOG_STALE_WHILE_ALIVE,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_log_stale_corroborator)
        watchdog.record_invocation_start()
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy.LOG_STALE_WHILE_ALIVE MUST NOT defer),"
            f" got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_stale_label_only_does_not_defer_no_output_at_start(self) -> None:
        """Stale ``AliveBy.STALE_LABEL_ONLY`` does NOT defer NO_OUTPUT_AT_START."""
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _stale_label_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=AliveBy.STALE_LABEL_ONLY,
                scoped_child_active=True,
                oldest_child_seconds=5.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_stale_label_corroborator)
        watchdog.record_invocation_start()
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (stale AliveBy.STALE_LABEL_ONLY MUST NOT defer), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    def test_defers_no_output_at_start_when_cumulative_waiting_on_child_positive(
        self,
    ) -> None:
        """Cumulative waiting time > 0 defers NO_OUTPUT_AT_START (AC-02).

        The watchdog must defer NO_OUTPUT_AT_START when
        ``cumulative_waiting_on_child_seconds > 0`` because an agent
        that survived a full waiting run has demonstrated it is alive
        enough that ``NO_OUTPUT_AT_START`` no longer applies.

        This test PROVES the AC-02 contract end-to-end via observable
        behavior (no private-field mutation):

        1. Configure so the cycle and the assertion both work:
           - ``no_output_at_start_seconds=200.0`` is larger than the
             waiting-cycle duration (101s..106s) but smaller than the
             post-cycle advance (200s). This ordering lets the cycle
             run BEFORE ``_evaluate_no_output_at_start`` becomes
             eligible -- a control test (with no prior cycle) would
             FIRE ``NO_OUTPUT_AT_START`` at t=200s and the test would
             correctly fail the CONTINUE assertion.
           - ``idle_timeout_seconds=100.0`` is small enough that the
             waiting branch is reachable at t=101s (the cycle must
             happen PAST idle_timeout because the waiting branch is
             only entered when idle_elapsed >= idle_timeout).
           - ``drain_window_seconds=300.0`` is large enough that
             after the cycle the watchdog is inside the drain
             window (so the post-cycle verdict is ``CONTINUE``
             rather than ``NO_OUTPUT_DEADLINE``), letting us assert
             the deferral against a stable CONTINUE signal.
           - ``no_progress_quiet_seconds=None`` so the
             NO_PROGRESS_QUIET path is disabled.
        2. Drive ONE full ``WAITING_ON_CHILD`` entry/exit cycle
           through the public ``evaluate(classify_quiet=...)`` API
           so the ``_cumulative_waiting_on_child_seconds`` invariant
           is earned via observable behavior (the public
           ``cumulative_waiting_on_child_seconds`` property reads
           > 0 after the cycle).
        3. Advance past ``no_output_at_start_seconds`` (200s) with
           no new activity and no live corroborator alive_by. The
           only verdict path that runs is the
           ``_evaluate_no_output_at_start`` deferral gate -- if
           the gate is broken, NO_OUTPUT_AT_START fires first
           (BEFORE the drain-window CONTINUE) and the watchdog
           returns ``FIRE`` with ``last_fire_reason ==
           NO_OUTPUT_AT_START``. The test would then fail the
           CONTINUE + last_fire_reason-is-None assertions below.
        4. Assert the returned verdict is
           ``WatchdogVerdict.CONTINUE`` (the AC-02 contract).
        5. Assert no fire reason was recorded
           (``last_fire_reason is None``).

        The pre-fix test directly mutated the private
        ``_cumulative_waiting_on_child_seconds`` field, which is
        implementation-detail coupling (and per the repository's
        black-box test policy is a smell). The post-fix test
        drives the invariant through the public
        ``evaluate(classify_quiet=...)`` interface and reads the
        cumulative via the public ``cumulative_waiting_on_child_seconds``
        property so the AC-02 contract is fully observable.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=100.0,
            no_output_at_start_seconds=200.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
            no_progress_quiet_seconds=None,
            drain_window_seconds=300.0,
        )
        clock = FakeClock(start=0.0)

        def _empty_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by=None,
                scoped_child_active=False,
                oldest_child_seconds=0.0,
            )

        watchdog = IdleWatchdog(config, clock, corroborator=_empty_corroborator)
        watchdog.record_invocation_start()

        # (a) Advance past the idle deadline (t=101) so the next
        # evaluate enters the WAITING_ON_CHILD branch (the branch
        # is only reachable when idle_elapsed >= idle_timeout_seconds).
        # At t=101, the no_output_at_start check sees
        # (101 - 0) < 200, so it returns None BEFORE the
        # active/waiting branch selection.
        clock.advance(101.0)
        # (b) Enter the WAITING_ON_CHILD branch via the public
        # API: classify_quiet returns WAITING_ON_CHILD and
        # _handle_waiting_branch starts the run. This is the
        # public entry point -- the watchdog starts tracking the
        # waiting run internally without any private-field
        # manipulation.
        result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert result == WatchdogVerdict.WAITING_ON_CHILD, (
            f"expected WAITING_ON_CHILD (entering the waiting branch), got {result}"
        )

        # (c) Advance 5s in the waiting state.
        clock.advance(5.0)
        # (d) Exit the WAITING_ON_CHILD branch via the public
        # API: classify_quiet returns ACTIVE and _handle_active_branch
        # accumulates the elapsed waiting time into
        # _cumulative_waiting_on_child_seconds. The branch then
        # enters the drain window (drain_window_seconds=300) and
        # returns CONTINUE.
        result = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
        assert result == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (drain window entered after cycle), got {result}"
        )

        # (e) Public-property observation: the cumulative is now
        # > 0 because the cycle ran through the public API. This
        # is the AC-02 precondition earned via observable
        # behavior, NOT via private-field mutation.
        assert watchdog.cumulative_waiting_on_child_seconds > 0.0, (
            f"expected cumulative_waiting_on_child_seconds > 0 after the cycle,"
            f" got {watchdog.cumulative_waiting_on_child_seconds}"
        )

        # (f) Advance past no_output_at_start_seconds=200.0 so
        # the next evaluate triggers the no_output_at_start check.
        # The watchdog is inside the drain window
        # (drain_started_at = 106.0; drain_window_seconds = 300.0;
        # current_time = 201.0; drain_elapsed = 95.0 < 300).
        clock.advance(95.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # AC-02: verdict is CONTINUE. The verdict path is:
        #   1. _evaluate_no_output_at_start -- (201.0 - 0) = 201.0
        #      >= 200 -> trigger eligible; live corroborator empty
        #      (alive_by=None, not fresh) -> deferral candidate;
        #      cumulative > 0 -> DEFER (returns None).
        #   2. idle_timeout check: 201.0 > 100 -> past idle.
        #   3. _evaluate_final_verdict -> in drain_window ->
        #      _handle_drain_window -> drain_elapsed=95.0 < 300 ->
        #      CONTINUE.
        # If the deferral gate were broken, step 1 would FIRE
        # NO_OUTPUT_AT_START and we would see FIRE, not CONTINUE.
        assert verdict == WatchdogVerdict.CONTINUE, (
            f"expected CONTINUE (NO_OUTPUT_AT_START must defer when"
            f" cumulative_waiting_on_child_seconds > 0), got verdict={verdict}"
        )
        # AC-02: no fire reason was recorded (deferral, not fire).
        assert watchdog.last_fire_reason is None, (
            f"expected last_fire_reason=None (no fire happened), got {watchdog.last_fire_reason}"
        )

    def test_still_fires_when_live_corroborator_returns_empty_and_no_waiting_run(
        self,
    ) -> None:
        """No false-positive deferral: corroborator empty AND no prior waiting run.

        When the corroborator returns an empty CorroborationSnapshot
        (alive_by=None) AND there is no prior waiting run (so
        cumulative_waiting_on_child_seconds == 0), the watchdog must
        still FIRE NO_OUTPUT_AT_START after the threshold elapses with
        no activity. This pins the no-false-positive contract.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        def _empty_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot()

        watchdog = IdleWatchdog(config, clock, corroborator=_empty_corroborator)
        watchdog.record_invocation_start()

        # No waiting run accumulated; advance past no_output_at_start_seconds.
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (corroborator empty, no prior waiting run), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got {watchdog.last_fire_reason}"
        )


# === consolidated from test_idle_watchdog_no_output_at_start_lifecycle.py ===
class TestSafeCorroborateFailsClosed:
    """Regression: ``_safe_corroborate`` MUST normalize a ``None`` (or any
    non-``CorroborationSnapshot``) return to an empty
    ``CorroborationSnapshot`` so callers like ``_evaluate_no_output_at_start``
    can safely read ``corroboration.alive_by`` without an ``AttributeError``.

    Pre-fix, ``_safe_corroborate`` returned ``self._corroborator()``
    directly. When the corroborator returned ``None``, callers that read
    ``corroboration.alive_by`` crashed mid-evaluation instead of
    failing closed to a no-defer signal. The watchdog is supposed to
    fail closed (empty snapshot = "no live evidence" = conservative
    no-defer), so the empty-snapshot normalization is the correct
    fail-closed behavior.

    These tests cover three contract paths:
      1. ``corroborator=lambda: None`` returns ``None`` -> watchdog
         evaluation continues safely and fires NO_OUTPUT_AT_START
         (empty corroboration means no live evidence -> no defer).
      2. ``corroborator=lambda: "not a snapshot"`` returns a non-snapshot
         value -> normalized to empty, watchdog continues safely.
      3. ``_safe_corroborate`` directly returns a
         ``CorroborationSnapshot`` even when the corroborator returns
         ``None`` (unit-level assertion of the normalization).
    """

    def test_safe_corroborate_normalizes_none_return_to_empty_snapshot(self) -> None:
        """A corroborator returning ``None`` is normalized to an empty
        ``CorroborationSnapshot`` so callers never see a ``None`` snapshot.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        # Corroborator that returns None (the bug case).
        watchdog = IdleWatchdog(config, clock, corroborator=lambda: None)
        watchdog.record_invocation_start()

        snapshot = watchdog._safe_corroborate()
        assert snapshot is not None, "_safe_corroborate MUST normalize None to an empty snapshot"
        assert isinstance(snapshot, CorroborationSnapshot)
        assert snapshot.alive_by is None
        # scoped_child_active is None by default (Optional[bool]); the
        # important property is "no live evidence" which both None and
        # False satisfy. Falsy check pins the conservative no-defer
        # signal without coupling to the default representation.
        assert not snapshot.scoped_child_active

    def test_safe_corroborate_normalizes_non_snapshot_return_to_empty_snapshot(
        self,
    ) -> None:
        """A corroborator returning any non-``CorroborationSnapshot`` value
        (e.g. a plain string, dict, int) is normalized to an empty snapshot.
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        for bogus_value in ("not a snapshot", 42, {"alive_by": "OS_DESCENDANT"}, []):
            watchdog = IdleWatchdog(config, clock, corroborator=lambda value=bogus_value: value)
            snapshot = watchdog._safe_corroborate()
            assert isinstance(snapshot, CorroborationSnapshot), (
                f"non-snapshot return {bogus_value!r} MUST normalize to empty"
                f" CorroborationSnapshot, got {snapshot!r}"
            )
            assert snapshot.alive_by is None
            assert not snapshot.scoped_child_active

    def test_watchdog_evaluate_continues_safely_when_corroborator_returns_none(
        self,
    ) -> None:
        """Watchdog evaluation does NOT crash when the corroborator returns
        ``None``. With no live evidence and no prior waiting run, the
        watchdog fires NO_OUTPUT_AT_START (the no-false-positive contract
        is preserved because empty corroboration = "no live evidence" =
        conservative no-defer).
        """
        config = TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=60.0,
            max_waiting_on_child_seconds=1800.0,
            max_waiting_on_child_no_progress_seconds=600.0,
        )
        clock = FakeClock(start=0.0)

        watchdog = IdleWatchdog(config, clock, corroborator=lambda: None)
        watchdog.record_invocation_start()

        # Drive past no_output_at_start_seconds with no activity. With
        # idle_timeout_seconds=300 the watchdog reaches the
        # _evaluate_no_output_at_start path (no idle_elapsed early-out).
        # Pre-fix this raised AttributeError because corroboration was None
        # and _evaluate_no_output_at_start read corroboration.alive_by.
        clock.advance(61.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

        # No live evidence + no prior waiting run => NO_OUTPUT_AT_START fires.
        assert verdict == WatchdogVerdict.FIRE, (
            f"expected FIRE (no live evidence, no prior waiting run), got verdict={verdict}"
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"expected last_fire_reason == NO_OUTPUT_AT_START, got {watchdog.last_fire_reason}"
        )

