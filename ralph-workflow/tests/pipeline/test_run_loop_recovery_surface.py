"""Black-box tests for the recovery/stopping user-facing display surface.

The Trustworthy Idle Watchdog product brief lists six acceptance criteria.
The R1-R8 spec pinning model covers criteria 1, 2, 3, 4, and 6. The
single remaining gap is acceptance criterion 5: *Users can understand why
Ralph is waiting, recovering, or stopping.*

The 'waiting' half is well covered by the existing ``WAITING`` /
``RESUMED`` lines emitted by ``_run_inner_loop``. The 'recovering' and
'stopping' halves were user-invisible: ``RecoveryController`` published
``FalloverEvent`` and ``FailureEvent`` instances onto the
``FailureEventBus`` whose only subscriber was a loguru logger. There was
no run-loop-level display surface for them, and a watchdog-driven kill
propagated only as an exception through the invoke layer with no
``emit_activity_line`` notification.

This file pins the new display surface that closes that AC-5 gap. The
new surface is a SECOND subscriber on the same ``FailureEventBus``
(``_subscribe_recovery_display``) that is registered after the run
loop has built its active display, and routes every fallover /
failure-event through ``emit_activity_line(active_display, None,
status_text(...))`` — exactly mirroring the existing ``WAITING`` /
``RESUMED`` direct-emit pattern in ``_run_inner_loop``. The subscriber
is cadenced (a per-event-kind-tag throttle keyed by an injected
clock) so repeated events within the configured window emit at most
one line; it is defensive (the entire callback body is wrapped in
``try/except``) so a display rendering exception cannot break recovery
propagation.

These are BLACK-BOX CAPTURE TESTS, NOT pin tests. They are not in the
``RALPH_PIN_TEST_PATHS`` inventory in
``tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py``
and are not listed in the watchdog-spec.md pin-test inventory — that
inventory is for R1-R8 watchdog criteria, and this test enforces a
product-brief AC-5 pipeline display surface. Each test asserts only on
captured ``emit_activity_line`` output strings, never on private
implementation attributes.

AC-01 contract: ``FalloverEvent`` -> one ``RECOVERING`` line that names
    ``from_agent``, ``to_agent``, and the reason.
AC-02 contract: ``FailureEvent`` carrying ``watchdog_reason`` -> a line
    explaining the stall reason and 'resuming'; ``chain_capacity_remaining=0``
    -> a line that surfaces the root cause (not 'Unknown error').
AC-03 contract: cadence gate. Repeated same-kind events within the
    window emit at most one line.
AC-04 contract: defensive. A raising display fake does not break the
    bus publish; the bus is fail-safe and recovery state is unaffected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.run_loop import (
    _FailureEvent,
    _FalloverEvent,
    _subscribe_recovery_display,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent as _FailureEventCls
from ralph.recovery.events import FalloverEvent as _FalloverEventCls
from ralph.recovery.failure_event import FailureEvent
from ralph.recovery.fallover_event import FalloverEvent

if TYPE_CHECKING:
    import pytest


def _build_controller_with_bus() -> tuple[RecoveryController, FakeClock]:
    """Build a real ``RecoveryController`` with the default ``FailureEventBus``.

    Returns ``(controller, clock)`` where ``clock`` is the injected
    ``FakeClock`` driving the controller's decision logic. Tests register
    listeners on ``controller.event_bus`` directly via the public property;
    they do not touch the private ``_bus`` attribute.
    """
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=cast("Any", clock)),
    )
    return controller, clock


def _capture_emitted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Monkeypatch ``emit_activity_line`` in ``ralph.pipeline.run_loop`` to
    capture emitted strings into a list. Returns the captured list, which
    the test appends to from the patched function.
    """
    captured: list[str] = []

    def _capture(display: object, unit_id: str | None, line: str) -> None:
        captured.append(line)

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", _capture)
    return captured


def _make_fallover_event(
    *,
    phase: str = "development",
    from_agent: str = "claude",
    to_agent: str = "opencode",
    reason: str = "out_of_credits",
    unavailability_reason: str = "out_of_credits",
    watchdog_reason: str | None = None,
) -> FalloverEvent:
    """Construct a real ``FalloverEvent`` (the bus dispatch type)."""
    return FalloverEvent(
        timestamp=datetime.now(UTC),
        phase=phase,
        from_agent=from_agent,
        to_agent=to_agent,
        reason=reason,
        watchdog_reason=watchdog_reason,
        unavailability_reason=unavailability_reason,
    )


def _make_failure_event(
    *,
    phase: str = "development",
    agent: str | None = "claude",
    category: str = "agent",
    reason: str = "no_output_at_start",
    counted_against_budget: bool = True,
    chain_capacity_remaining: int = 2,
    recovery_cycle: int = 1,
    retry_delay_ms: int = 500,
    watchdog_reason: str | None = None,
    unavailability_reason: str | None = None,
) -> FailureEvent:
    """Construct a real ``FailureEvent`` (the bus dispatch type)."""
    return FailureEvent(
        timestamp=datetime.now(UTC),
        phase=phase,
        agent=agent,
        category=category,
        reason=reason,
        counted_against_budget=counted_against_budget,
        chain_capacity_remaining=chain_capacity_remaining,
        recovery_cycle=recovery_cycle,
        retry_delay_ms=retry_delay_ms,
        watchdog_reason=watchdog_reason,
        unavailability_reason=unavailability_reason,
    )


def test_fallover_emits_recovering_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-01: a ``FalloverEvent`` published on the bus produces a user-facing
    ``RECOVERING`` line that names ``from_agent``, ``to_agent``, and the
    reason.

    Black-box: tests build a real ``RecoveryController``, register the
    display subscriber on the public ``controller.event_bus`` property,
    publish the event via ``controller.event_bus.publish(...)``, and
    assert on captured ``emit_activity_line`` strings — no private
    attribute access.
    """
    captured = _capture_emitted(monkeypatch)
    controller, clock = _build_controller_with_bus()

    unsubscribe = _subscribe_recovery_display(
        controller,
        display=MagicMock(),
        interval_seconds=10.0,
        now=clock.monotonic,
    )
    try:
        event = _make_fallover_event(
            from_agent="claude",
            to_agent="opencode",
            reason="out_of_credits",
        )
        controller.event_bus.publish(event)
    finally:
        unsubscribe()

    recovering_lines = [line for line in captured if "RECOVERING" in line]
    assert len(recovering_lines) >= 1, f"expected at least one RECOVERING line, got {captured!r}"
    line = recovering_lines[0]
    assert "claude" in line, f"expected from_agent='claude' in line, got {line!r}"
    assert "opencode" in line, f"expected to_agent='opencode' in line, got {line!r}"
    assert "out_of_credits" in line, f"expected reason 'out_of_credits' in line, got {line!r}"


def test_watchdog_kill_emits_stall_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-02: a watchdog-driven kill (a ``FailureEvent`` carrying a
    ``watchdog_reason`` with ``retry_delay_ms > 0`` representing a
    resumable situation) produces a user-facing line that names the
    watchdog reason and that recovery is resuming — never the bare
    'Unknown error' fallback.

    Black-box: build a real controller, register the display subscriber,
    publish the event, assert on captured output.
    """
    captured = _capture_emitted(monkeypatch)
    controller, clock = _build_controller_with_bus()

    unsubscribe = _subscribe_recovery_display(
        controller,
        display=MagicMock(),
        interval_seconds=10.0,
        now=clock.monotonic,
    )
    try:
        event = _make_failure_event(
            reason="watchdog_kill",
            watchdog_reason="no_output_at_start",
            retry_delay_ms=500,
            chain_capacity_remaining=2,
        )
        controller.event_bus.publish(event)
    finally:
        unsubscribe()

    stall_lines = [
        line for line in captured if "RECOVERING" in line and "no_output_at_start" in line
    ]
    assert len(stall_lines) >= 1, (
        f"expected at least one RECOVERING line that surfaces the "
        f"watchdog reason 'no_output_at_start', got {captured!r}"
    )


def test_terminal_failure_surfaces_root_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-02: a terminal classified failure (``chain_capacity_remaining==0``)
    surfaces the root-cause category/reason in the user-facing line, NOT
    a generic 'Unknown error' fallback.

    The run loop's display fallback at the terminal phase is
    ``status_text("Pipeline failed", state.last_error or "Unknown error", ...)``.
    This test asserts that the recovery/stopping display adds a SEPARATE
    line that carries the actual root-cause text ahead of the run loop's
    generic fallback so an operator is never left with 'Unknown error'
    alone.
    """
    captured = _capture_emitted(monkeypatch)
    controller, clock = _build_controller_with_bus()

    unsubscribe = _subscribe_recovery_display(
        controller,
        display=MagicMock(),
        interval_seconds=10.0,
        now=clock.monotonic,
    )
    try:
        terminal_event = _make_failure_event(
            category="agent",
            reason="cycle_cap_exceeded",
            watchdog_reason=None,
            retry_delay_ms=0,
            chain_capacity_remaining=0,
            unavailability_reason="cycle_cap_exceeded",
        )
        controller.event_bus.publish(terminal_event)
    finally:
        unsubscribe()

    stopping_lines = [
        line for line in captured if "STOPPING" in line and "cycle_cap_exceeded" in line
    ]
    assert len(stopping_lines) >= 1, (
        f"expected at least one STOPPING line carrying the root cause "
        f"'cycle_cap_exceeded', got {captured!r}"
    )


def test_recovery_messages_are_cadenced(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-03: the low-noise guarantee. Repeated events of the same kind
    within the configured cadence window emit AT MOST one user-facing
    line. Repeated events AFTER the window elapses emit a fresh line.

    This is the deterministic cadence proof. The subscriber takes an
    injected ``now`` callable so this test passes ``FakeClock.monotonic``
    and advances it explicitly; there is no real wall-clock wait.
    """
    captured = _capture_emitted(monkeypatch)
    controller, clock = _build_controller_with_bus()

    interval_seconds = 10.0
    unsubscribe = _subscribe_recovery_display(
        controller,
        display=MagicMock(),
        interval_seconds=interval_seconds,
        now=clock.monotonic,
    )
    try:
        event = _make_fallover_event(
            from_agent="claude",
            to_agent="opencode",
            reason="out_of_credits",
        )
        for _ in range(5):
            controller.event_bus.publish(event)
        assert len([line for line in captured if "RECOVERING" in line]) == 1, (
            f"expected exactly one RECOVERING line within the cadence window, got {captured!r}"
        )
        clock.advance(interval_seconds + 0.001)
        controller.event_bus.publish(event)
        assert len([line for line in captured if "RECOVERING" in line]) == 2, (
            f"expected exactly two RECOVERING lines after the cadence "
            f"window elapsed and a new event was published, got {captured!r}"
        )
    finally:
        unsubscribe()


def test_cadence_is_per_event_kind_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-03 (per-tag detail): the cadence map is keyed by event-kind tag,
    NOT a single global slot. A ``FalloverEvent`` within the window followed
    immediately by a ``FailureEvent`` carrying a watchdog_reason emits BOTH
    lines (one per kind tag).
    """
    captured = _capture_emitted(monkeypatch)
    controller, clock = _build_controller_with_bus()

    unsubscribe = _subscribe_recovery_display(
        controller,
        display=MagicMock(),
        interval_seconds=10.0,
        now=clock.monotonic,
    )
    try:
        controller.event_bus.publish(_make_fallover_event(from_agent="claude", to_agent="opencode"))
        controller.event_bus.publish(
            _make_failure_event(
                watchdog_reason="no_output_at_start",
                retry_delay_ms=500,
            )
        )
    finally:
        unsubscribe()

    fallover_lines = [line for line in captured if "RECOVERING" in line and "opencode" in line]
    failure_lines = [
        line for line in captured if "RECOVERING" in line and "no_output_at_start" in line
    ]
    assert len(fallover_lines) == 1, (
        f"expected exactly one fallover RECOVERING line, got {captured!r}"
    )
    assert len(failure_lines) == 1, f"expected exactly one watchdog-failure line, got {captured!r}"


def test_display_exception_does_not_break_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-04: the display subscriber is defensive. A raising display fake
    does NOT break recovery propagation: ``bus.publish`` returns normally
    and the listener exception is swallowed to a debug log.
    """
    controller, clock = _build_controller_with_bus()

    raising_display = MagicMock()

    def _raising_emit(display: object, unit_id: str | None, line: str) -> None:
        del display, unit_id, line
        raise RuntimeError("display boom")

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", _raising_emit)

    unsubscribe = _subscribe_recovery_display(
        controller,
        display=raising_display,
        interval_seconds=10.0,
        now=clock.monotonic,
    )
    try:
        event = _make_fallover_event(
            from_agent="claude",
            to_agent="opencode",
            reason="out_of_credits",
        )
        controller.event_bus.publish(event)
        controller.event_bus.publish(
            _make_failure_event(
                watchdog_reason="no_output_at_start",
                retry_delay_ms=500,
            )
        )
    finally:
        unsubscribe()

    snapshot = controller.snapshot()
    assert isinstance(snapshot, dict)


def test_recovery_event_aliases_match_imported_types() -> None:
    """AC-05 wiring: the subscriber reuses the ``_FailureEvent`` / ``_FalloverEvent``
    aliases imported at the top of ``ralph.pipeline.run_loop``. The aliases
    are the SAME dataclass objects as the canonical
    ``ralph.recovery.failure_event.FailureEvent`` / ``FalloverEvent`` types,
    so publish-time ``isinstance`` checks match.
    """
    assert _FailureEvent is _FailureEventCls
    assert _FalloverEvent is _FalloverEventCls
