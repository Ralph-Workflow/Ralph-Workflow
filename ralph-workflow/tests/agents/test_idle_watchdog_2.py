"""Black-box tests for IdleWatchdog policy using FakeClock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

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


_HARD_STOP_OLDEST_CHILD_SECS = 42.5
_NO_PROGRESS_CEILING = 10.0
_FULL_CEILING = 100.0


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
        # Disable suspicion by default so tests that use small max_waiting values
        # (e.g. 20.0) don't conflict with the 600.0 default suspect threshold.
        suspect_waiting_on_child_seconds=suspect,
        waiting_status_interval_seconds=status_interval if status_interval is not None else 30.0,
        # Explicitly disable no-progress ceiling by default to avoid validation errors
        # when max_waiting is small (e.g. 20.0). Tests that specifically test the
        # no-progress ceiling should pass no_progress_ceiling explicitly.
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
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


def _make_watchdog_with_no_progress_ceiling(
    no_progress_ceiling: float | None,
    full_ceiling: float = _FULL_CEILING,
) -> tuple[IdleWatchdog, FakeClock]:
    return _make_watchdog(
        idle_timeout=1.0,
        max_waiting=full_ceiling,
        no_progress_ceiling=no_progress_ceiling,
    )


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def test_hard_stop_diag_includes_corroboration() -> None:
    """HARD_STOP diagnostic contains scoped_child_active and oldest_child_seconds."""

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            scoped_child_active=True, oldest_child_seconds=_HARD_STOP_OLDEST_CHILD_SECS
        )

    hard_stop_max = 5.0
    watchdog, clock, events = _make_watchdog_with_listener(
        idle_timeout=1.0,
        max_waiting=hard_stop_max,
        status_interval=100.0,
        suspect=None,
        corroborator=_corroborator,
    )
    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)  # ENTERED
    clock.advance(hard_stop_max)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    diag = hard_stops[0].diagnostic
    assert diag.get("scoped_child_active") is True
    assert diag.get("oldest_child_seconds") == _HARD_STOP_OLDEST_CHILD_SECS


def test_corroboration_snapshot_has_alive_by_field() -> None:
    """CorroborationSnapshot accepts alive_by and defaults to None."""
    snap = CorroborationSnapshot()
    assert snap.alive_by is None

    snap_with = CorroborationSnapshot(alive_by="fresh_progress")
    assert snap_with.alive_by == "fresh_progress"


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
    watchdog.evaluate(classify_quiet=_waiting)  # ENTERED
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_waiting)  # PROGRESS (interval=1s)

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert progress_events, "expected at least one PROGRESS event"
    diag = progress_events[0].diagnostic
    assert diag.get("alive_by") == "fresh_heartbeat_only"


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
    watchdog.evaluate(classify_quiet=_waiting)  # ENTERED
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_waiting)  # PROGRESS

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert progress_events
    diag = progress_events[0].diagnostic
    assert "alive_by" not in diag


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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the no-progress ceiling — still waiting.
    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the no-progress ceiling — must FIRE.
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_no_progress_ceiling_fires_on_stale_label_only() -> None:
    """WAITING_ON_CHILD with alive_by=stale_label_only fires on no-progress ceiling."""
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="stale_label_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog._config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the no-progress ceiling — should still be WAITING.
    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the full ceiling (100s) - we've used ~10s so far, need 89.9s more.
    clock.advance(89.9)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the full ceiling — FIRE.
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the no-progress ceiling — still WAITING (ceiling disabled).
    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the full ceiling — FIRE.
    clock.advance(100.0 - _NO_PROGRESS_CEILING + 1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the no-progress ceiling — still WAITING (alive_by=None uses full ceiling).
    clock.advance(_NO_PROGRESS_CEILING + 10.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance to just under the full ceiling (100s) - we've used ~20s so far, need ~79.9s more.
    clock.advance(79.9)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance past the full ceiling — FIRE.
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


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
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(_NO_PROGRESS_CEILING + 1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE

    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    diag = hard_stops[0].diagnostic
    assert diag.get("effective_ceiling_label") == "no_progress"


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
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    entered = [e for e in events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered) == 1
    assert entered[0].ceiling_seconds == _NO_PROGRESS_CEILING

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    progress = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress) == 1
    assert progress[0].ceiling_seconds == _NO_PROGRESS_CEILING


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
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(config, clock, corroborator=_corroborator)

    # T1: enter WAITING at t=1.5s (cumulative=0s), fresh_progress → full ceiling.
    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # T2: cumulative=18s, still fresh_progress → ceiling=100s → WAITING.
    clock.advance(18.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Corroboration degrades to OS-descendant-only (no scoped progress any more).
    phase[0] = "degraded"

    # T3: cumulative=21s >= no_progress_ceiling=20s → FIRE (not waiting for full ceiling=100s).
    clock.advance(3.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_validation_rejects_no_progress_ceiling_above_max_waiting() -> None:
    """TimeoutPolicy rejects no_progress_ceiling > max_waiting_on_child_seconds."""
    with pytest.raises(ValueError, match="max_waiting_on_child_no_progress_seconds must be <="):
        TimeoutPolicy(
            idle_timeout_seconds=10.0,
            max_waiting_on_child_seconds=100.0,
            max_waiting_on_child_no_progress_seconds=200.0,
            suspect_waiting_on_child_seconds=None,  # Disable to avoid conflict
        )


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
        # suspect_waiting_on_child_seconds=3.0 < no_progress_ceiling=10.0 so
        # SUSPECTED_FROZEN fires on T2 before HARD_STOP fires on T3.
        suspect_waiting_on_child_seconds=3.0,
        # status_interval=0.001 ensures PROGRESS also fires on T2.
        waiting_status_interval_seconds=0.001,
    )
    clock = FakeClock(start=0.0)
    events: list[WaitingStatusEvent] = []
    watchdog = IdleWatchdog(config, clock, listener=events.append, corroborator=_flaky_corroborator)

    # T1: ENTER WAITING at t=1.5
    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    # T1 makes 2 calls: _entry_corroboration + effective_ceiling

    # T2: SUSPECTED_FROZEN + PROGRESS at t=6.5 (candidate_total=5.0, suspect=3.0)
    clock.advance(5.0)
    call_count = 0  # reset to isolate T2 calls
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    # T2 makes 1 call: only effective_ceiling (not entering WAITING)
    assert call_count == 1

    # T3: HARD_STOP at t=11.5 (candidate_total=10.0 >= no_progress_ceiling=10.0)
    call_count = 0  # reset to isolate T3 calls
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
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
        suspect_waiting_on_child_seconds=None,
    )
    assert policy.max_waiting_on_child_no_progress_seconds == equal_ceiling
