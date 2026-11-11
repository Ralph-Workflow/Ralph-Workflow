from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
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
from ralph.agents.timeout_clock import FakeClock


@dataclass
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


_EXPECTED_PROGRESS_COUNT = 2
_HARD_STOP_MAX_WAITING = 10.0
_WS_ENTRY_COUNT = 5
_WS_FINAL_COUNT = 9
_WS_EXPECTED_DELTA = 4


def _make_watchdog(
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


def _make_watchdog_with_listener(
    idle_timeout: float | None,
    max_waiting: float | None = None,
    status_interval: float | None = None,
    suspect: float | None = None,
    corroborator: WaitingCorroborator | None = None,
) -> tuple[IdleWatchdog, FakeClock, list[WaitingStatusEvent]]:
    events: list[WaitingStatusEvent] = []
    watchdog, clock = _make_watchdog(
        idle_timeout=idle_timeout,
        max_waiting=max_waiting,
        status_interval=status_interval,
        suspect=suspect,
        listener=events.append,
        corroborator=corroborator,
    )
    return watchdog, clock, events


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def test_disabled_when_idle_timeout_is_none() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=None)
    clock.advance(1_000_000)
    assert watchdog.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE
    assert watchdog.evaluate(classify_quiet=_waiting) == WatchdogVerdict.CONTINUE


def test_continues_before_deadline() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10)
    clock.advance(9.9)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_enters_drain_window_at_deadline_when_active() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(0.4)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_drain_window_aborted_by_late_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(0.2)
    watchdog.record_activity()

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_waiting_on_child_defers_without_resetting_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=1800.0)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE


def test_waiting_on_child_hard_ceiling_fires() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=20.0, drain_window=0.0)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(20.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_record_activity_clears_drain_state() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_active)

    watchdog.record_activity()

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_validation_rejects_zero_idle_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        TimeoutPolicy(idle_timeout_seconds=0)


def test_validation_rejects_negative_drain_window() -> None:
    with pytest.raises(ValueError, match=">="):
        TimeoutPolicy(idle_timeout_seconds=10, drain_window_seconds=-0.1)


def test_validation_rejects_max_waiting_less_than_idle() -> None:
    with pytest.raises(ValueError, match="max_waiting_on_child_seconds"):
        TimeoutPolicy(
            idle_timeout_seconds=100,
            max_waiting_on_child_seconds=50,
            max_waiting_on_child_no_progress_seconds=None,
        )


def test_session_ceiling_validation_rejects_value_lower_than_idle_timeout() -> None:
    with pytest.raises(ValueError, match="max_session_seconds"):
        TimeoutPolicy(idle_timeout_seconds=100, max_session_seconds=50)


def test_session_ceiling_fires_despite_heartbeats() -> None:
    max_session = 30.0
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=max_session
    )

    for _ in range(29):
        clock.advance(1.0)
        watchdog.record_activity()
        result = watchdog.evaluate(classify_quiet=_active)
        assert result == WatchdogVerdict.CONTINUE, f"Expected CONTINUE at t={clock.monotonic()}"

    clock.advance(1.0)
    watchdog.record_activity()
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_waiting_on_child_cumulative_survives_active_oscillation() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5, max_waiting=20.0)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(5.0, abs=0.01)

    clock.advance(0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(6.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(5.0, abs=0.01)


def test_consecutive_waiting_does_not_double_count() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=100.0)

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting)

    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


def test_drain_window_defers_when_children_reappear() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5, max_waiting=1800.0)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE
    assert watchdog.in_drain_window is True

    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.in_drain_window is False

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(0.6)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_logger_emits_warning_on_fire_with_reason() -> None:
    captured_messages: list[str] = []

    def _sink(message: object) -> None:
        captured_messages.append(str(message))

    sink_id = logger.add(
        _sink,
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "idle_watchdog",
    )
    try:
        watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.0, max_waiting=1800.0)

        clock.advance(10.0)
        result = watchdog.evaluate(classify_quiet=_active)
        assert result == WatchdogVerdict.FIRE
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    finally:
        logger.remove(sink_id)

    assert any("FIRE" in msg or "no_output_deadline" in msg for msg in captured_messages), (
        f"Expected WARNING with fire reason, got: {captured_messages}"
    )


def test_session_ceiling_fires_even_if_waiting_on_child() -> None:
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=20.0
    )

    clock.advance(21.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_session_ceiling_fires_during_drain_window() -> None:
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=2.0, max_waiting=1800.0, max_session=15.0
    )

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_drain_window_zero_fires_immediately_with_active_classification() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.0, max_waiting=1800.0)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_record_activity_during_waiting_does_not_reset_cumulative() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, max_waiting=20.0)

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(4.0)
    watchdog.record_activity()

    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)


def test_record_activity_during_waiting_cumulative_causes_fire() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, max_waiting=10.0)

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(4.0)
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(7.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_cumulative_does_not_decay_under_long_active_period() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=20.0)

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(4.0)
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    clock.advance(0.6)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(17.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_waiting_ceiling_survives_intermittent_heartbeats() -> None:
    idle_timeout = 10.0
    max_waiting = 20.0
    watchdog, clock = _make_watchdog(idle_timeout=idle_timeout, max_waiting=max_waiting)

    last_result = WatchdogVerdict.WAITING_ON_CHILD
    for _ in range(100):
        clock.advance(idle_timeout + 1.0)
        result = watchdog.evaluate(classify_quiet=_waiting)
        if result == WatchdogVerdict.FIRE:
            last_result = result
            break
        assert result == WatchdogVerdict.WAITING_ON_CHILD
        clock.advance(0.5)
        watchdog.record_activity()

    assert last_result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_cumulative_ceiling_fires_with_heartbeat_then_long_quiet() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=20.0)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(8.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(8.0, abs=0.01)

    clock.advance(2.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(8.0, abs=0.01)

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(13.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_session_ceiling_precedence_over_waiting_branch() -> None:
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=1800.0,
        max_session=15.0,
    )

    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_evaluate_is_idempotent_when_clock_does_not_advance() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, max_waiting=20.0)

    clock.advance(11.0)
    result1 = watchdog.evaluate(classify_quiet=_waiting)
    assert result1 == WatchdogVerdict.WAITING_ON_CHILD

    result2 = watchdog.evaluate(classify_quiet=_waiting)
    assert result2 == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


def test_consecutive_active_in_drain_does_not_extend_window() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(0.2)
    watchdog.evaluate(classify_quiet=_active)
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_waiting_emits_entered_event_once_per_run() -> None:
    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=1000.0, status_interval=100.0, suspect=None
    )
    clock.advance(1.1)
    for _ in range(5):
        result = watchdog.evaluate(classify_quiet=_waiting)
        assert result == WatchdogVerdict.WAITING_ON_CHILD
        clock.advance(0.01)
    entered = [e for e in events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered) == 1


def test_waiting_progress_throttled_to_interval() -> None:
    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=1000.0, status_interval=10.0, suspect=None
    )
    clock.advance(1.1)
    for _ in range(25):
        watchdog.evaluate(classify_quiet=_waiting)
        clock.advance(1.0)
    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) == _EXPECTED_PROGRESS_COUNT


def test_waiting_suspected_frozen_fires_once_per_run() -> None:
    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=100.0, status_interval=100.0, suspect=5.0
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(6.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_waiting)
    suspected_after = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected_after) == 1


def test_waiting_exited_event_on_record_activity() -> None:
    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=1000.0, status_interval=100.0, suspect=None
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(0.5)
    watchdog.record_activity()
    exited = [e for e in events if e.kind == WaitingStatusKind.EXITED]
    assert len(exited) == 1
    assert exited[0].current_run_seconds > 0.0


def test_waiting_hard_stop_emits_event_with_diagnostic() -> None:
    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0, max_waiting=_HARD_STOP_MAX_WAITING, status_interval=100.0, suspect=None
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(_HARD_STOP_MAX_WAITING)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    assert hard_stops[0].diagnostic is not None


def test_waiting_no_per_tick_log_spam() -> None:
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
            watchdog.evaluate(classify_quiet=_waiting)
            clock.advance(0.01)
    finally:
        logger.remove(sink_id)

    spam_count = sum(1 for msg in captured_logs if "cumulative_candidate=" in msg)
    assert spam_count == 0


def test_listener_exception_does_not_propagate() -> None:

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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD


def test_validation_rejects_suspect_above_ceiling() -> None:
    with pytest.raises(ValueError, match="strictly less than"):
        TimeoutPolicy(
            idle_timeout_seconds=10,
            max_waiting_on_child_seconds=100,
            suspect_waiting_on_child_seconds=200,
            max_waiting_on_child_no_progress_seconds=None,
        )


def test_validation_rejects_suspect_equal_to_ceiling() -> None:
    with pytest.raises(ValueError, match="strictly less than"):
        TimeoutPolicy(
            idle_timeout_seconds=10,
            max_waiting_on_child_seconds=100,
            suspect_waiting_on_child_seconds=100,
            max_waiting_on_child_no_progress_seconds=None,
        )


def test_corroborator_diag_attached_to_progress() -> None:
    call_count = 0

    def _corroborator() -> CorroborationSnapshot:
        nonlocal call_count
        call_count += 1
        return CorroborationSnapshot(workspace_event_count=5)

    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=10.0,
        suspect=None,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) >= 1
    assert progress[0].diagnostic.get("workspace_event_delta") == 0


def test_corroborator_workspace_activity_increment() -> None:
    call_count = 0
    counts = [_WS_ENTRY_COUNT, _WS_FINAL_COUNT, _WS_FINAL_COUNT]

    def _corroborator() -> CorroborationSnapshot:
        nonlocal call_count
        count = counts[min(call_count, len(counts) - 1)]
        call_count += 1
        return CorroborationSnapshot(workspace_event_count=count)

    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=10.0,
        suspect=None,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) >= 1
    assert progress[0].diagnostic.get("workspace_event_delta") == _WS_EXPECTED_DELTA


def test_corroborator_suspected_frozen_evidence_workspace_quiet() -> None:
    suspect_threshold = 5.0

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            workspace_event_count=7,
            oldest_child_seconds=suspect_threshold + 1.0,
        )

    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=100.0,
        suspect=suspect_threshold,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(suspect_threshold + 1.0)
    watchdog.evaluate(classify_quiet=_waiting)
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    evidence = str(suspected[0].diagnostic.get("evidence", ""))
    assert "time_and_workspace_quiet" in evidence


def test_corroborator_suspected_frozen_evidence_lifecycle_only() -> None:
    suspect_threshold = 5.0

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(last_activity_was_meaningful=False)

    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=100.0,
        suspect=suspect_threshold,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(suspect_threshold + 1.0)
    watchdog.evaluate(classify_quiet=_waiting)
    suspected = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspected) == 1
    evidence = str(suspected[0].diagnostic.get("evidence", ""))
    assert "time_and_lifecycle_only" in evidence


def test_corroborator_exception_does_not_propagate() -> None:

    def _bad_corroborator() -> CorroborationSnapshot:
        raise RuntimeError("corroborator exploded")

    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=1000.0,
        status_interval=100.0,
        suspect=None,
        corroborator=_bad_corroborator,
    )
    clock.advance(1.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    entered = [e for e in events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered) == 1
    assert "workspace_event_delta" not in entered[0].diagnostic


def test_idle_elapsed_seconds_tracks_time_since_last_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=300.0, start=100.0)
    watchdog.record_activity()
    clock.advance(7.0)

    assert watchdog.idle_elapsed_seconds(clock.monotonic()) == pytest.approx(7.0)
