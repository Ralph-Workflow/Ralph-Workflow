"""Surface tests for real-time subagent progress visibility.

IdleWatchdog records subagent progress via ``record_subagent_work`` but the
description was previously private state. These tests pin the public accessors
and the default subagent-activity listener hook so operators and downstream
code can observe what the subagent is doing in real time.

All tests use FakeClock; no real subprocess, no real sleep, no real network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
)
from ralph.agents.timeout_clock import FakeClock


@dataclass
class _NoProcessMonitor:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


def _make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor()), clock


def test_last_subagent_progress_description_property_returns_recorded_value() -> None:
    """``IdleWatchdog.last_subagent_progress_description`` returns the most
    recent description set via ``record_subagent_work`` and resets to
    ``None`` on ``record_invocation_start``."""
    watchdog, _clock = _make_watchdog()
    watchdog.record_invocation_start()

    assert watchdog.last_subagent_progress_description is None

    watchdog.record_subagent_work(description="agent is reading foo.py")
    assert watchdog.last_subagent_progress_description == "agent is reading foo.py"

    watchdog.record_subagent_work(description="agent is writing bar.py")
    assert watchdog.last_subagent_progress_description == "agent is writing bar.py"

    watchdog.record_invocation_start()
    assert watchdog.last_subagent_progress_description is None


def test_register_default_subagent_activity_listener_invokes_listener_with_payload() -> None:
    """``register_default_subagent_activity_listener`` receives every
    ``WaitingStatusEvent`` whose ``subagent_activity`` field is populated.

    The listener is reset to ``None`` on ``record_invocation_start`` so state
    does not leak across invocations.
    """
    watchdog, _clock = _make_watchdog()
    captured: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append(event)

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    watchdog.record_subagent_work(description="agent is reading foo.py")
    # Trigger an emit cycle; the listener should receive the event with the
    # subagent_activity field populated from the last recorded description.
    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )

    assert len(captured) == 1
    assert captured[0].subagent_activity == "agent is reading foo.py"
    assert captured[0].kind == WaitingStatusKind.ENTERED

    # A second emit without a new subagent activity description should still
    # forward the most recent description.
    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=1.0,
        idle_elapsed=1.0,
        ceiling_seconds=60.0,
    )
    assert len(captured) == 2
    assert captured[1].subagent_activity == "agent is reading foo.py"

    # Resetting the invocation must clear the listener state so a prior run's
    # listener is not called for a fresh run.
    watchdog.record_invocation_start()
    captured.clear()
    watchdog._emit(
        WaitingStatusKind.ENTERED,
        current_run_seconds=0.0,
        idle_elapsed=0.0,
        ceiling_seconds=60.0,
    )
    assert captured == []
