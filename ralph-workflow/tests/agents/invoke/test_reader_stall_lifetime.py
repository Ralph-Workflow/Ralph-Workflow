"""Regression coverage for the process reader's watchdog-owned stall lifetime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusEvent, WaitingStatusKind
from ralph.agents.invoke._process_reader import _ProcessLineReader
from ralph.agents.invoke._types import _ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    import pytest


class _FakeManagedProcess:
    """In-memory process handle that supplies one completed output stream."""

    def __init__(self) -> None:
        self.pid: int | None = None
        self.stdout = iter(["completed output\n"])

    def poll(self) -> int:
        return 0

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        del grace_period_s


class _FakeProcessManager:
    def register_listener(self, _callback: object) -> object:
        return lambda: None


def test_process_reader_stall_lifetime_regression_teardown_publishes_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DA-001/S-1: exhausting a stalled reader emits the watchdog clear event."""
    import ralph.agents.invoke._process_reader as process_reader

    events: list[WaitingStatusEvent] = []
    monkeypatch.setattr(process_reader, "get_process_manager", _FakeProcessManager)
    clock = FakeClock(start=0.0)
    ctx = _ProcessReaderCtx(
        config=AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC),
        policy=TimeoutPolicy(
            idle_timeout_seconds=60.0,
            drain_window_seconds=0.0,
            process_monitor_enabled=False,
        ),
        waiting_listener=events.append,
    )
    reader = _ProcessLineReader(_FakeManagedProcess(), ctx, clock)
    lines = reader.read_lines()

    assert next(lines) == "completed output\n"
    reader._watchdog._set_stall(active=True, now=1.0, idle_elapsed=1.0)
    assert list(lines) == []

    assert [event.kind for event in events[-2:]] == [
        WaitingStatusKind.STALLED,
        WaitingStatusKind.STALL_RESUMED,
    ]
    assert events[-1].stall_active is False
