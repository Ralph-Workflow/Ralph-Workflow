"""Black-box tests for IdleWatchdog policy using FakeClock."""

from __future__ import annotations

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock


def _make_watchdog(
    idle_timeout: float | None,
    drain_window: float = 0.5,
    max_waiting: float | None = None,
    start: float = 0.0,
    max_session: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


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

    # At deadline, classify_quiet=ACTIVE -> enter drain window
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain window entered

    # Still inside drain window
    clock.advance(0.4)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # Drain window exhausted
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_drain_window_aborted_by_late_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    # Enter drain window
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # Activity arrives during drain
    clock.advance(0.2)
    watchdog.record_activity()

    # Should continue without firing; advance well under idle timeout
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_waiting_on_child_defers_without_resetting_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=1800.0)

    # Past idle deadline with children present
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # More time passes; children gone but no new output
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    # Still past idle deadline (16s > 10s), no new activity -> drain window opens
    assert result == WatchdogVerdict.CONTINUE

    # Drain exhausted
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE


def test_waiting_on_child_hard_ceiling_fires() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=20.0)

    # Advance past idle deadline
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance in a single jump past the hard ceiling
    clock.advance(20.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_record_activity_clears_drain_state() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    # Enter drain window
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_active)

    # Reset by activity
    watchdog.record_activity()

    # Advance less than idle timeout from the activity point
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
        TimeoutPolicy(idle_timeout_seconds=100, max_waiting_on_child_seconds=50)


def test_session_ceiling_validation_rejects_value_lower_than_idle_timeout() -> None:
    """TimeoutPolicy rejects max_session_seconds < idle_timeout_seconds."""
    with pytest.raises(ValueError, match="max_session_seconds"):
        TimeoutPolicy(idle_timeout_seconds=100, max_session_seconds=50)


def test_session_ceiling_fires_despite_heartbeats() -> None:
    """Session ceiling fires even when record_activity() is called continuously.

    This tests that the session ceiling cannot be defeated by heartbeat activity —
    a process that produces output continuously must still be killed when the
    absolute session wall-clock ceiling is reached.
    """
    max_session = 30.0
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=max_session
    )

    # Simulate continuous heartbeat activity every second for 29s — no fire yet.
    for _ in range(29):
        clock.advance(1.0)
        watchdog.record_activity()
        result = watchdog.evaluate(classify_quiet=_active)
        assert result == WatchdogVerdict.CONTINUE, f"Expected CONTINUE at t={clock.monotonic()}"

    # At t=30s the session ceiling is reached — FIRE regardless of recent activity.
    clock.advance(1.0)
    watchdog.record_activity()  # heartbeat fires just before evaluation
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_waiting_on_child_cumulative_survives_active_oscillation() -> None:
    """Cumulative WAITING time is preserved across WAITING->ACTIVE->WAITING oscillation.

    This tests the false-negative fix: a process that alternates between producing
    output (WAITING->ACTIVE) and waiting on children cannot defeat the
    max_waiting_on_child_seconds ceiling by resetting the counter on each active interval.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5, max_waiting=20.0)

    # (a) Advance 11s -> past idle deadline. classify=WAITING_ON_CHILD -> start run1.
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # (b) Advance 5s -> still past deadline. classify=ACTIVE -> accumulate run1 (5s), enter drain.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered
    # At this point cumulative should have 5s from run1.
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(5.0, abs=0.01)

    # (c) Advance 0.1s -> still in drain window. classify=WAITING -> abandon drain, start run2.
    clock.advance(0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # drain abandoned

    # (d) Advance 6s -> run2 elapsed 6s, cumulative_candidate = 5 + 6 = 11s < 20 ceiling.
    clock.advance(6.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # (e) Advance 10s -> run2 elapsed 16s, cumulative_candidate = 5 + 16 = 21s >= 20 ceiling.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_consecutive_waiting_does_not_double_count() -> None:
    """Consecutive WAITING evaluations must not double-count the same elapsed time."""
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=100.0)

    # Past idle deadline
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)  # starts run at t=11

    clock.advance(5.0)  # t=16
    watchdog.evaluate(classify_quiet=_waiting)  # 5s elapsed in run

    clock.advance(5.0)  # t=21
    watchdog.evaluate(classify_quiet=_waiting)  # 10s elapsed in run

    # cumulative is still 0 (only added on transition out of WAITING)
    # candidate_total should be 10s, well under ceiling
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


def test_drain_window_defers_when_children_reappear() -> None:
    """When children appear during the drain window, drain is abandoned and WAITING resumes.

    This tests the false-positive fix: children that appear during the drain window
    must prevent the timeout from firing.
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10, drain_window=0.5, max_waiting=1800.0
    )

    # (a) Advance 10s -> at idle deadline. classify=ACTIVE -> enter drain window.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered
    assert watchdog._in_drain_window is True

    # (b) Advance 0.2s -> inside drain window. classify=WAITING -> abandon drain.
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # not FIRE
    assert watchdog._in_drain_window is False  # drain abandoned

    # (c) Advance 5s -> back to ACTIVE -> re-enter drain.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # new drain entered

    # (d) Advance 0.6s -> drain exhausted (0.5s + 0.1s overshoot). Fires NO_OUTPUT_DEADLINE.
    clock.advance(0.6)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


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
        watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.0, max_waiting=1800.0)

        # Advance past idle deadline and fire immediately (drain_window=0)
        clock.advance(10.0)
        result = watchdog.evaluate(classify_quiet=_active)
        assert result == WatchdogVerdict.FIRE  # drain_window=0 fires immediately
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    finally:
        logger.remove(sink_id)

    assert any("FIRE" in msg or "no_output_deadline" in msg for msg in captured_messages), (
        f"Expected WARNING with fire reason, got: {captured_messages}"
    )


def test_session_ceiling_fires_even_if_waiting_on_child() -> None:
    """Session ceiling fires even when classify_quiet=WAITING_ON_CHILD.

    Proves ceiling outranks WAITING deferral — max_session takes precedence
    over any child-wait state.
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=20.0
    )

    # Advance to t=21 (past max_session=20) with children present.
    clock.advance(21.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_session_ceiling_fires_during_drain_window() -> None:
    """Session ceiling fires during drain window (ceiling outranks drain deferral).

    When max_session elapses while in the drain window, SESSION_CEILING_EXCEEDED
    fires regardless of drain state.
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=2.0, max_waiting=1800.0, max_session=15.0
    )

    # Enter drain window at t=10.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered

    # Advance to t=15 (session ceiling hit during drain).
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_drain_window_zero_fires_immediately_with_active_classification() -> None:
    """drain_window=0 with ACTIVE classification fires immediately at idle deadline.

    Regression for the active-branch zero-drain shortcut where drain_window=0
    must fire immediately without entering a non-existent drain window.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.0, max_waiting=1800.0)

    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_record_activity_during_waiting_does_not_reset_cumulative() -> None:
    """record_activity() preserves cumulative_waiting_on_child_seconds.

    Cumulative is an absolute ceiling that survives heartbeats. Heartbeat activity
    alone cannot defeat the max_waiting_on_child_seconds ceiling.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10.0, max_waiting=20.0)

    # 4s WAITING run starting at t=11.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)  # enters WAITING at t=11

    # Heartbeat at t=15: accumulates 4s to cumulative, does NOT reset to 0.
    clock.advance(4.0)
    watchdog.record_activity()

    # Cumulative preserved at 4s (not reset by record_activity).
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Advance past idle deadline again (11s from last activity at t=15).
    clock.advance(11.0)  # t=26, idle_elapsed=11
    result = watchdog.evaluate(classify_quiet=_waiting)  # enters new WAITING run at t=26
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # candidate=4+0=4 < 20

    # Advance 11s within the second WAITING run: candidate = 4 + 11 = 15 < 20.
    clock.advance(11.0)  # t=37
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)


def test_record_activity_during_waiting_cumulative_causes_fire() -> None:
    """Preserved cumulative causes CHILDREN_PERSIST_TOO_LONG when ceiling reached.

    When max_waiting_on_child_seconds is small enough, the preserved 4s from the
    first WAITING run causes the second run to fire the ceiling.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10.0, max_waiting=10.0)

    # 4s WAITING run starting at t=11.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)  # enters WAITING at t=11

    # Heartbeat at t=15: cumulative = 4.0.
    clock.advance(4.0)
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Advance past idle deadline; enters new WAITING run at t=26.
    clock.advance(11.0)  # t=26, idle_elapsed=11
    result = watchdog.evaluate(classify_quiet=_waiting)  # candidate=4+0=4 < 10
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance 7s in the second run: candidate = 4 + 7 = 11 >= 10 -> FIRE.
    clock.advance(7.0)  # t=33
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_cumulative_does_not_decay_under_long_active_period() -> None:
    """Cumulative is absolute across the session: any duration of in-deadline ACTIVE
    evaluations does NOT reset it. This guards against the previous bug where a single
    heartbeat plus drain_window quiet wiped the ceiling.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=20.0)

    # 4s WAITING run starting at t=11.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(4.0)  # t=15
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Stay within idle deadline for > drain_window_seconds (0.5) without WAITING.
    clock.advance(0.6)  # t=15.6, idle_elapsed=0.6 < 10
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # Cumulative must NOT decay; it remains at 4s.
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # Sustained active period — still no decay.
    clock.advance(5.0)  # t=20.6, idle_elapsed=5.6 < 10
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(4.0, abs=0.01)

    # A fresh WAITING run starts with cumulative=4, not 0.
    # 4 + 17 = 21 >= 20 -> FIRE proves cumulative was never reset.
    clock.advance(11.0)  # t=31.6, idle_elapsed=11 -> past idle deadline
    watchdog.evaluate(classify_quiet=_waiting)  # new WAITING run starts
    clock.advance(17.0)  # run elapsed=17; candidate=4+17=21>=20
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_waiting_ceiling_survives_intermittent_heartbeats() -> None:
    """Cumulative WAITING ceiling fires despite intermittent heartbeat activity.

    Previously record_activity() reset cumulative to 0, so a child that emits one
    heartbeat just after the idle deadline could defeat max_waiting_on_child_seconds
    forever. The fix preserves cumulative across heartbeats.
    """
    idle_timeout = 10.0
    max_waiting = 20.0
    watchdog, clock = _make_watchdog(idle_timeout=idle_timeout, max_waiting=max_waiting)

    last_result = WatchdogVerdict.WAITING_ON_CHILD
    for _ in range(100):  # upper bound; test fails if FIRE never arrives
        clock.advance(idle_timeout + 1.0)  # advance past idle deadline
        result = watchdog.evaluate(classify_quiet=_waiting)
        if result == WatchdogVerdict.FIRE:
            last_result = result
            break
        assert result == WatchdogVerdict.WAITING_ON_CHILD
        clock.advance(0.5)  # heartbeat arrives shortly after
        watchdog.record_activity()

    assert last_result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=20.0)

    # t=11: past idle deadline
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # t=19: still in run1 (8s elapsed)
    clock.advance(8.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Heartbeat: record_activity accumulates run1 (8s) -> cumulative=8
    watchdog.record_activity()
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(8.0, abs=0.01)

    # t=21: 2s quiet > drain_window=0.5; previously this would zero cumulative
    clock.advance(2.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # cumulative must remain 8, not be reset to 0
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(8.0, abs=0.01)

    # t=32: past idle deadline again (11s from record_activity at t=19)
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)  # new WAITING run2 starts
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # t=45: run2 elapsed=13; candidate=8+13=21>=20 -> FIRE
    clock.advance(13.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_session_ceiling_precedence_over_waiting_branch() -> None:
    """SESSION_CEILING_EXCEEDED takes precedence over CHILDREN_PERSIST_TOO_LONG.

    When both session and cumulative-waiting ceilings are exceeded simultaneously,
    the session ceiling fires first because it is checked first in evaluate().
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=1800.0,
        max_session=15.0,
    )

    # t=11: past idle deadline; children present.
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # t=16: past session ceiling (15s) AND still past idle deadline with children.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_evaluate_is_idempotent_when_clock_does_not_advance() -> None:
    """Two consecutive evaluate() calls with no clock advance return CONTINUE.

    Regression for double-counting: calling evaluate twice at the same clock time
    must not accumulate extra waiting time.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10.0, max_waiting=20.0)

    # Advance to deadline and into WAITING.
    clock.advance(11.0)
    result1 = watchdog.evaluate(classify_quiet=_waiting)
    assert result1 == WatchdogVerdict.WAITING_ON_CHILD

    # Call evaluate again with no clock advance.
    result2 = watchdog.evaluate(classify_quiet=_waiting)
    assert result2 == WatchdogVerdict.WAITING_ON_CHILD
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


def test_consecutive_active_in_drain_does_not_extend_window() -> None:
    """evaluate(ACTIVE) called twice with small clock advances does not extend drain window.

    The drain window has a fixed duration measured from when it starts. Calling
    evaluate(ACTIVE) multiple times during the drain must not reset or extend the
    window — it should fire after drain_window_seconds regardless.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0)

    # Enter drain at t=10.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # Two evaluate calls with small advances totalling 0.4s — still in drain.
    clock.advance(0.2)
    watchdog.evaluate(classify_quiet=_active)
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # One more 0.2s advance = 0.6s > 0.5s drain -> FIRE.
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
