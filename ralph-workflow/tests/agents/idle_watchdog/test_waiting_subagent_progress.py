"""Pin: SUBAGENT_PROGRESS waiting-status event surfaces in WAITING_ON_CHILD.

The PROMPT log shows the waiting-status stream emits only
``ENTERED`` / ``PROGRESS`` / ``SUSPECTED_FROZEN`` / ``HARD_STOP`` /
``EXITED`` events. Operators have no real-time view of what the
dispatched subagent is doing while the parent is in WAITING_ON_CHILD
deferral.

The fix: a new ``WaitingStatusKind.SUBAGENT_PROGRESS`` event that
reuses the existing parser-layer subagent progress surface
(``record_subagent_work`` populates
``_last_subagent_progress_description`` and the process monitor's
``live_subagent_count()`` provides the live-count signal -- both
agent-agnostic). The event is rate-limited by
``TimeoutPolicy.watchdog_subagent_progress_interval_seconds``
(default 30 s, matching the existing PROGRESS cadence).

This test:

  (a) Drives a ``FakeProcessMonitor(live_subagent_count=2)`` for more
      than ``watchdog_subagent_progress_interval_seconds`` in the
      WAITING_ON_CHILD branch and asserts the SUBAGENT_PROGRESS event
      fires exactly once with both fields populated.

  (b) Drives the same scenario with a description via
      ``record_subagent_work`` and asserts the description is
      forwarded in the event's diagnostic dict (sanitized, truncated).

  (c) Verifies the rate-limit: two ticks inside the throttle window
      emit only one event; the next tick after the throttle elapses
      emits a second event.

All tests use FakeClock + a fake ProcessMonitor with
``live_subagent_count()``; no real subprocess, no ``time.sleep``, no
real network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
)
from ralph.agents.timeout_clock import FakeClock


@dataclass
class _FakeProcessMonitor:
    """Fake process monitor with a configurable live-subagent count."""

    count: int = 0

    def live_subagent_count(self) -> int:
        return self.count

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


def _make_watchdog(
    *,
    subagent_interval: float = 30.0,
    monitor_count: int = 0,
    max_waiting: float = 600.0,
    idle_timeout: float = 5.0,
) -> tuple[IdleWatchdog, FakeClock, list[WaitingStatusEvent]]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        watchdog_subagent_progress_interval_seconds=subagent_interval,
        waiting_status_interval_seconds=60.0,
        max_waiting_on_child_seconds=max_waiting,
        max_waiting_on_child_no_progress_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    captured: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append(event)

    watchdog = IdleWatchdog(
        policy,
        clock,
        listener=_listener,
        process_monitor=_FakeProcessMonitor(count=monitor_count),
    )
    return watchdog, clock, captured


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def test_subagent_progress_event_kind_exists() -> None:
    """WaitingStatusKind.SUBAGENT_PROGRESS MUST exist on the enum so
    listeners can filter by kind.
    """
    assert hasattr(WaitingStatusKind, "SUBAGENT_PROGRESS"), (
        "WaitingStatusKind.SUBAGENT_PROGRESS missing; the watchdog's"
        " waiting-status stream cannot surface per-subagent progress"
        " without this enum value"
    )
    assert (
        WaitingStatusKind.SUBAGENT_PROGRESS.value == "subagent_progress"
    )


def test_subagent_progress_emits_once_when_monitor_has_live_subagents() -> None:
    """When ``_process_monitor.live_subagent_count() > 0`` the watchdog
    MUST emit exactly one ``SUBAGENT_PROGRESS`` event per throttle
    window while WAITING_ON_CHILD is active.
    """
    watchdog, clock, captured = _make_watchdog(
        subagent_interval=30.0,
        monitor_count=2,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    # Advance past idle_timeout so the next evaluate enters
    # WAITING_ON_CHILD (which is where _handle_waiting_branch emits).
    clock.advance(6.0)

    # First evaluate call enters WAITING_ON_CHILD and emits ENTERED.
    verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert verdict.value == "waiting_on_child", (
        f"evaluate MUST return WAITING_ON_CHILD when classify_quiet"
        f" returns WAITING_ON_CHILD and idle_timeout elapsed; got {verdict}"
    )
    # ENTERED is captured above; clear so we only inspect SUBAGENT_PROGRESS.
    captured.clear()

    # Advance 5s and evaluate again: the SUBAGENT_PROGRESS cadence
    # window (30s) has NOT elapsed since the (empty) emit timestamp so
    # the event must NOT emit.
    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert subagent_emits == [], (
        f"SUBAGENT_PROGRESS emitted before the throttle window"
        f" elapsed; got {len(subagent_emits)} events"
    )

    # Advance to 31s past the previous evaluate tick: throttle window
    # has elapsed and the next evaluate MUST emit exactly one
    # SUBAGENT_PROGRESS event.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(subagent_emits) == 1, (
        f"expected exactly 1 SUBAGENT_PROGRESS event after the throttle"
        f" window elapsed; got {len(subagent_emits)}"
    )
    # The diagnostic dict carries the live subagent count.
    assert subagent_emits[0].diagnostic.get("live_subagent_count") == 2, (
        f"SUBAGENT_PROGRESS diagnostic.live_subagent_count MUST be 2;"
        f" got {subagent_emits[0].diagnostic.get('live_subagent_count')}"
    )


def test_subagent_progress_emits_with_recorded_description() -> None:
    """When ``record_subagent_work`` was called the diagnostic dict
    MUST carry the sanitized ``subagent_activity`` field.
    """
    watchdog, clock, captured = _make_watchdog(
        subagent_interval=30.0,
        monitor_count=0,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(description="reading source.py")
    # Advance past idle_timeout to enter WAITING_ON_CHILD.
    clock.advance(6.0)
    # First evaluate: ENTERED.
    watchdog.evaluate(classify_quiet=_waiting)
    captured.clear()
    # Advance past the throttle window and re-evaluate.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(subagent_emits) == 1, (
        f"expected 1 SUBAGENT_PROGRESS event; got {len(subagent_emits)}"
    )
    diag = subagent_emits[0].diagnostic
    assert diag.get("subagent_activity") == "reading source.py", (
        f"SUBAGENT_PROGRESS diagnostic.subagent_activity MUST forward"
        f" the recorded description; got {diag.get('subagent_activity')!r}"
    )
    assert diag.get("live_subagent_count") == 0


def test_subagent_progress_does_not_emit_without_evidence() -> None:
    """When NEITHER ``record_subagent_work`` was called NOR
    ``live_subagent_count() > 0`` the watchdog MUST NOT emit
    ``SUBAGENT_PROGRESS`` (the predicate guards against empty payloads).
    """
    watchdog, clock, captured = _make_watchdog(
        subagent_interval=30.0,
        monitor_count=0,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting)
    captured.clear()
    # Advance past the throttle window and re-evaluate; no record,
    # no monitor count -> no SUBAGENT_PROGRESS event.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert subagent_emits == [], (
        f"SUBAGENT_PROGRESS MUST NOT emit without evidence; got"
        f" {len(subagent_emits)} events"
    )


def test_subagent_progress_rate_limit_respected() -> None:
    """Two ticks within the throttle window emit ONE event; a third
    tick after the window elapses emits a SECOND event.
    """
    watchdog, clock, captured = _make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    # ENTERED emit on first evaluate.
    watchdog.evaluate(classify_quiet=_waiting)
    captured.clear()

    # First throttle-elapsed tick -> emit #1.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(subagent_emits) == 1, (
        f"expected 1 SUBAGENT_PROGRESS emit after first throttle window;"
        f" got {len(subagent_emits)}"
    )

    # A tick at +5s (well within the 30s window) -> no new emit.
    clock.advance(5.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(subagent_emits) == 1, (
        f"second tick within throttle window MUST NOT re-emit; got"
        f" {len(subagent_emits)}"
    )

    # A tick at +31s past the first emit (well past the 30s window)
    # -> emit #2.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    subagent_emits = [
        e for e in captured if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(subagent_emits) == 2, (
        f"expected 2 SUBAGENT_PROGRESS emits after second throttle"
        f" window; got {len(subagent_emits)}"
    )


def test_subagent_progress_resets_on_record_invocation_start() -> None:
    """``record_invocation_start`` MUST reset the throttle so a new
    invocation's first SUBAGENT_PROGRESS emit is not suppressed by a
    prior invocation's throttle state.
    """
    watchdog, clock, captured = _make_watchdog(
        subagent_interval=30.0,
        monitor_count=1,
        idle_timeout=5.0,
    )
    watchdog.record_invocation_start()
    clock.advance(6.0)
    # Drive a tick that emits SUBAGENT_PROGRESS at +31s.
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    assert any(
        e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured
    ), "first invocation SUBAGENT_PROGRESS missing"

    # Reset to a new invocation. The throttle map MUST be cleared
    # so the first eligible tick after the reset can emit again.
    captured.clear()
    watchdog.record_invocation_start()
    clock.advance(6.0)
    clock.advance(31.0)
    watchdog.evaluate(classify_quiet=_waiting)
    assert any(
        e.kind == WaitingStatusKind.SUBAGENT_PROGRESS for e in captured
    ), (
        "second invocation's first SUBAGENT_PROGRESS emit was"
        " suppressed by stale throttle state from the prior invocation"
    )