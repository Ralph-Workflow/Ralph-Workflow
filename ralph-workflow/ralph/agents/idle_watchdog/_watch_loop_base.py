from __future__ import annotations

import threading
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock

__all__ = ["WatchLoopBase"]


T = TypeVar("T")


class WatchLoopBase:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._event = threading.Event()

    def wait_until(
        self,
        *,
        predicate: Callable[[], T | None],
        timeout_s: float,
        poll_interval_s: float,
        on_tick: Callable[[T | None], None] | None = None,
    ) -> T | None:
        deadline = self.clock.monotonic() + timeout_s

        result = predicate()
        if result:
            return result

        while True:
            now = self.clock.monotonic()
            if now >= deadline:
                break
            if on_tick is not None:
                on_tick(result)
            remaining = deadline - now
            wait_for = poll_interval_s if remaining >= poll_interval_s else remaining
            self.clock.wait_for_event(self._event, wait_for)
            result = predicate()
            if result:
                return result

        return None

    def signal_activity(self) -> None:
        self._event.set()
        self._event.clear()
