from __future__ import annotations

import threading

from ralph.agents.timeout_clock import FakeClock


class _CallbackFakeClock(FakeClock):
    """FakeClock that triggers threading.Events at scheduled fake-time points."""

    def __init__(self, start: float = 0.0) -> None:
        super().__init__(start)
        self._listeners: list[tuple[float, threading.Event]] = []

    def _trigger_listeners(self) -> None:
        triggered = [ev for target, ev in self._listeners if self._now >= target]
        if triggered:
            for ev in triggered:
                ev.set()
            self._listeners = [(t, ev) for t, ev in self._listeners if self._now < t]

    def sleep(self, seconds: float) -> None:
        self._now += seconds
        self._trigger_listeners()

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self._now += seconds
        self._trigger_listeners()
        return event.is_set()

    def wait_until(self, target: float) -> threading.Event:
        """Return an event that fires when fake time reaches target."""
        ev = threading.Event()
        if self._now >= target:
            ev.set()
        else:
            self._listeners.append((target, ev))
        return ev
