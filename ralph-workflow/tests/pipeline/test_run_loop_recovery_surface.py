from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.run_loop import (
    _FailureEvent,
    _FalloverEvent,
    _subscribe_recovery_display,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FalloverEvent
from ralph.recovery.events import FailureEvent as _FailureEventCls
from ralph.recovery.events import FalloverEvent as _FalloverEventCls

if TYPE_CHECKING:
    import pytest


def _build_controller_with_bus() -> tuple[RecoveryController, FakeClock]:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock),
    )
    return controller, clock


def _capture_emitted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
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


def test_provider_cooldown_emits_recovery_line_without_watchdog_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_emitted(monkeypatch)
    controller, clock = _build_controller_with_bus()

    unsubscribe = _subscribe_recovery_display(
        controller,
        display=MagicMock(),
        interval_seconds=10.0,
        now=clock.monotonic,
    )
    try:
        controller.event_bus.publish(
            _make_failure_event(
                reason="server cluster is currently under high load (529)",
                retry_delay_ms=60_000,
                chain_capacity_remaining=2,
                unavailability_reason="provider_unavailable",
            )
        )
    finally:
        unsubscribe()

    lines = [line for line in captured if "RECOVERING" in line]
    assert len(lines) == 1
    assert "provider_unavailable" in lines[0]
    assert "60000ms" in lines[0]


def test_terminal_failure_surfaces_root_cause(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert _FailureEvent is _FailureEventCls
    assert _FalloverEvent is _FalloverEventCls
