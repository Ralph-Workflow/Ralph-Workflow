"""Black-box tests for OS-descendant-only escalation.

Tests the new short ceiling, earlier suspect event, CPU idle override,
log-growth override, and FRESH_PROGRESS regression.

All tests use FakeClock and assert only on public WatchdogVerdict,
WaitingStatusEvent.kind, WaitingStatusEvent.diagnostic, and
CorroborationSnapshot.alive_by. No real subprocess, no real file I/O.
"""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process._alive_by import AliveBy


def _make_watchdog(
    idle_timeout: float | None = 10.0,
    max_waiting: float | None = None,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
    os_descendant_only_ceiling: float | None = None,
    os_descendant_only_suspect: float | None = None,
    cpu_idle_seconds: float | None = None,
    log_growth_seconds: float | None = None,
    start: float = 0.0,
    status_interval: float = 30.0,
    corroborator: WaitingCorroborator | None = None,
    no_progress_quiet_seconds: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    # Default ``no_progress_quiet_seconds`` to ``no_progress_ceiling`` so
    # the no_progress_quiet_seconds <= max_waiting_on_child_no_progress_seconds
    # cross-field validator passes regardless of caller-supplied
    # ``no_progress_ceiling``.
    if no_progress_quiet_seconds is None:
        no_progress_quiet_seconds = (
            no_progress_ceiling if no_progress_ceiling is not None else 240.0
        )
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=max_waiting,
        suspect_waiting_on_child_seconds=suspect,
        waiting_status_interval_seconds=status_interval,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        os_descendant_only_ceiling_seconds=os_descendant_only_ceiling,
        os_descendant_only_suspect_seconds=os_descendant_only_suspect,
        cpu_idle_seconds=cpu_idle_seconds,
        log_growth_seconds=log_growth_seconds,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        no_progress_quiet_heartbeat_ceiling_seconds=no_progress_quiet_seconds,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock, corroborator=corroborator), clock


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _make_os_descendant_only_corroborator() -> WaitingCorroborator:
    def _corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    return _corr


def test_short_ceiling_fires_at_os_descendant_only_ceiling() -> None:
    """Watchdog fires FIRE with os_descendant_only ceiling at 120s.

    Setup: idle_timeout=10.0, max_waiting=600.0,
    os_descendant_only_ceiling=120.0, alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS.

    Advance to 130s (past 120s short ceiling) -> FIRE with
    effective_ceiling_label='os_descendant_only' and effective_ceiling=120.0.
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        os_descendant_only_ceiling=120.0,
        corroborator=_make_os_descendant_only_corroborator(),
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(130.0)
    result = watchdog.evaluate(classify_quiet=_waiting)

    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stop_events = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stop_events) == 1
    diag = hard_stop_events[0].diagnostic
    assert diag is not None
    assert diag.get("effective_ceiling_label") == "os_descendant_only"
    assert diag.get("effective_ceiling") == 120.0


def test_suspect_event_fires_at_os_descendant_only_suspect_seconds() -> None:
    """SUSPECTED_FROZEN fires at 60s (os_descendant_only_suspect) not 500s.

    Setup: idle_timeout=10.0, max_waiting=600.0,
    os_descendant_only_suspect=60.0, suspect=500.0 (standard),
    alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS.

    Advance to 70s -> one SUSPECTED_FROZEN with
    suspect_reason='os_descendant_only' and suspect_threshold=60.0.
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        suspect=500.0,
        os_descendant_only_ceiling=120.0,
        os_descendant_only_suspect=60.0,
        status_interval=100.0,
        corroborator=_make_os_descendant_only_corroborator(),
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(70.0)
    watchdog.evaluate(classify_quiet=_waiting)

    suspect_events = [e for e in events if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspect_events) == 1
    diag = suspect_events[0].diagnostic
    assert diag is not None
    assert diag.get("suspect_reason") == "os_descendant_only"
    assert diag.get("suspect_threshold") == 60.0
    assert diag.get("effective_ceiling_label") == "os_descendant_only"


def test_cpu_idle_override_picks_no_progress_ceiling() -> None:
    """CPU_IDLE_WHILE_ALIVE short-circuits to no_progress ceiling (180s).

    Setup: max_waiting_on_child_no_progress_seconds=180.0,
    cpu_idle_seconds=60.0, alive_by=CPU_IDLE_WHILE_ALIVE.

    Advance to 190s -> FIRE with effective_ceiling_label='no_progress'
    and effective_ceiling=180.0.
    """

    def _cpu_idle_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.CPU_IDLE_WHILE_ALIVE,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        no_progress_ceiling=180.0,
        cpu_idle_seconds=60.0,
        corroborator=_cpu_idle_corr,
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(190.0)
    result = watchdog.evaluate(classify_quiet=_waiting)

    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stop_events = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stop_events) == 1
    diag = hard_stop_events[0].diagnostic
    assert diag is not None
    assert diag.get("effective_ceiling_label") == "no_progress"
    assert diag.get("effective_ceiling") == 180.0


def test_log_growth_override_picks_no_progress_ceiling() -> None:
    """LOG_STALE_WHILE_ALIVE short-circuits to no_progress ceiling (180s).

    Setup: max_waiting_on_child_no_progress_seconds=180.0,
    log_growth_seconds=30.0, alive_by=LOG_STALE_WHILE_ALIVE.

    Advance to 190s -> FIRE with effective_ceiling_label='no_progress'
    and effective_ceiling=180.0.
    """

    def _log_stale_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.LOG_STALE_WHILE_ALIVE,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        no_progress_ceiling=180.0,
        log_growth_seconds=30.0,
        corroborator=_log_stale_corr,
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(190.0)
    result = watchdog.evaluate(classify_quiet=_waiting)

    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stop_events = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert len(hard_stop_events) == 1
    diag = hard_stop_events[0].diagnostic
    assert diag is not None
    assert diag.get("effective_ceiling_label") == "no_progress"
    assert diag.get("effective_ceiling") == 180.0


def test_fresh_progress_path_unchanged() -> None:
    """FRESH_PROGRESS alive_by uses standard ceiling (600.0).

    Regression test: alive_by=FRESH_PROGRESS at 310s cumulative
    -> CONTINUE (standard ceiling 600.0, not yet reached).
    Any PROGRESS event has effective_ceiling_label='standard'.
    """

    def _fresh_progress_corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    watchdog, clock = _make_watchdog(
        idle_timeout=10.0,
        max_waiting=600.0,
        status_interval=100.0,
        corroborator=_fresh_progress_corr,
    )
    events: list[WaitingStatusEvent] = []

    def _listener(evt: WaitingStatusEvent) -> None:
        events.append(evt)

    watchdog._listener = _listener

    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)

    clock.advance(310.0)
    result = watchdog.evaluate(classify_quiet=_waiting)

    assert result == WatchdogVerdict.WAITING_ON_CHILD

    progress_events = [e for e in events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) >= 1
    for prog_ev in progress_events:
        diag = prog_ev.diagnostic
        assert diag is not None
        assert diag.get("effective_ceiling_label") == "standard"
        assert diag.get("effective_ceiling") == 600.0
