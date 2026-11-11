
from __future__ import annotations

from collections.abc import Callable
from threading import Lock


class BurstDebounceScheduler:

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        on_fire: Callable[[], None],
        debounce_window: float,
    ) -> None:
        self._clock = clock
        self._on_fire = on_fire
        self._debounce_window = debounce_window
        self._lock = Lock()
        self._pending: list[
            Callable[[], None]
        ] = []
        self._last_mark_at: float | None = None
        self._suppressed = False

    def mark(self, closure: Callable[[], None]) -> None:
        with self._lock:
            self._pending.append(closure)
            self._last_mark_at = self._clock()
            self._suppressed = False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def fire_if_due(self) -> bool:
        with self._lock:
            if not self._pending or self._last_mark_at is None:
                return False
            if self._clock() - self._last_mark_at < self._debounce_window:
                return False
            self._pending = []
            self._last_mark_at = None
        self._on_fire()
        return True

    def _release_pending(self) -> None:
        with self._lock:
            self._pending = []
            self._last_mark_at = None
            self._suppressed = True

    def on_workflow_complete(self) -> None:
        self._release_pending()

    def on_workflow_cancel(self) -> None:
        self._release_pending()

    def on_workflow_fail(self) -> None:
        self._release_pending()

    def on_workflow_restart(self) -> None:
        self._release_pending()


__all__ = ["BurstDebounceScheduler"]
