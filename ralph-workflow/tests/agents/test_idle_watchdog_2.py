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
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(hard_stop_max)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stops) == 1
    diag = hard_stops[0].diagnostic
    assert diag.get("scoped_child_active") is True
    assert diag.get("oldest_child_seconds") == _HARD_STOP_OLDEST_CHILD_SECS


def test_corroboration_snapshot_has_alive_by_field() -> None:
    snap = CorroborationSnapshot()
    assert snap.alive_by is None

    snap_with = CorroborationSnapshot(alive_by="fresh_progress")
    assert snap_with.alive_by == "fresh_progress"


def test_build_corroboration_diag_includes_alive_by_when_set() -> None:
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
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_waiting)

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert progress_events, "expected at least one PROGRESS event"
    diag = progress_events[0].diagnostic
    assert diag.get("alive_by") == "fresh_heartbeat_only"


def test_build_corroboration_diag_omits_alive_by_when_none() -> None:
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
    watchdog.evaluate(classify_quiet=_waiting)
    clock.advance(2.0)
    watchdog.evaluate(classify_quiet=_waiting)

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert progress_events
    diag = progress_events[0].diagnostic
    assert "alive_by" not in diag


def test_no_progress_ceiling_fires_on_fresh_heartbeat_only() -> None:
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog.config, clock, corroborator=_corroborator)

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


def test_no_progress_ceiling_fires_on_stale_label_only() -> None:
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="stale_label_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog.config, clock, corroborator=_corroborator)

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
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING
    )

    def _corroborator() -> CorroborationSnapshot:
        snap = CorroborationSnapshot(
            alive_by="os_descendant_only_stale_progress", scoped_child_active=True
        )
        return snap

    watchdog = IdleWatchdog(watchdog.config, clock, corroborator=_corroborator)

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
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING,
        full_ceiling=100.0,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_progress", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog.config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(89.9)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_full_ceiling_preserved_when_no_progress_ceiling_disabled() -> None:
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=None,
        full_ceiling=100.0,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog.config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING - 0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(100.0 - _NO_PROGRESS_CEILING + 1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_full_ceiling_preserved_when_alive_by_is_none() -> None:
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING,
        full_ceiling=100.0,
    )

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(scoped_child_active=True)

    watchdog = IdleWatchdog(watchdog.config, clock, corroborator=_corroborator)

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(_NO_PROGRESS_CEILING + 10.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(79.9)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_hard_stop_diagnostic_includes_effective_ceiling_classification() -> None:
    watchdog, clock = _make_watchdog_with_no_progress_ceiling(
        no_progress_ceiling=_NO_PROGRESS_CEILING,
    )
    events: list[WaitingStatusEvent] = []

    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by="fresh_heartbeat_only", scoped_child_active=True)

    watchdog = IdleWatchdog(
        watchdog.config, clock, listener=events.append, corroborator=_corroborator
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

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(18.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    phase[0] = "degraded"

    clock.advance(3.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_validation_rejects_no_progress_ceiling_above_max_waiting() -> None:
    with pytest.raises(ValueError, match="max_waiting_on_child_no_progress_seconds must be <="):
        TimeoutPolicy(
            idle_timeout_seconds=10.0,
            max_waiting_on_child_seconds=100.0,
            max_waiting_on_child_no_progress_seconds=200.0,
            suspect_waiting_on_child_seconds=None,
        )


def test_single_tick_corroboration_snapshot_reused_for_all_decisions_and_diagnostics() -> None:
    call_count = 0
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

    clock.advance(1.5)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    clock.advance(5.0)
    call_count = 0
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD
    assert call_count == 1

    call_count = 0
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

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

    assert sf_diag_t2.get("alive_by") == pr_diag_t2.get("alive_by"), (
        f"T2: SUSPECTED_FROZEN alive_by={sf_diag_t2.get('alive_by')} != "
        f"PROGRESS alive_by={pr_diag_t2.get('alive_by')}. Same tick must use same snapshot."
    )


def test_validation_rejects_no_progress_ceiling_equal_to_max() -> None:
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


def test_safe_corroborate_bypasses_cache_outside_evaluate() -> None:
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

    first = watchdog.safe_corroborate()
    second = watchdog.safe_corroborate()
    assert call_count == 2, (
        f"Outside evaluate(), safe_corroborate() must call the corroborator "
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

    third = watchdog.safe_corroborate()
    assert call_count == 3
    assert third.alive_by == "os_descendant_only_stale_progress", (
        f"Third bypass-path call: expected "
        f"'os_descendant_only_stale_progress', got {third.alive_by!r}"
    )

    assert watchdog.tick_corroboration is None, (
        "Outside evaluate(), tick_corroboration must remain None. A non-None "
        "value here means the bypass path poisoned the cache, and the next "
        "safe_corroborate() call (still outside evaluate()) would return the "
        "stale snapshot."
    )

    call_count = 0
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert call_count == 1, (
        f"A single evaluate() tick must call the corroborator exactly once "
        f"(tick-scoped reuse), got call_count={call_count}."
    )

    assert watchdog.evaluate_tick_active is False, (
        "evaluate() must reset evaluate_tick_active to False in its finally "
        "block. A True value here means the tick cache is still 'active' "
        "after evaluate() returned, which would poison the next bypass-path "
        "call with the tick's stale snapshot."
    )
    assert watchdog.tick_corroboration is None, (
        "evaluate() must reset tick_corroboration to None in its finally "
        "block so the next bypass-path call cannot hit a stale snapshot."
    )

    fourth = watchdog.safe_corroborate()
    assert call_count == 2, (
        f"Bypass-path call AFTER evaluate() must invoke the corroborator "
        f"(call_count=2), got call_count={call_count}."
    )
    assert fourth.alive_by == "log_stale_while_alive", (
        f"Bypass-path call after evaluate() must return the corroborator's "
        f"NEXT fresh value, not a stale tick snapshot or a duplicate of the "
        f"evaluate() tick's snapshot. Got alive_by={fourth.alive_by!r}."
    )
