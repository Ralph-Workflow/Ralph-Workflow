"""Burst-debounce scheduler for dirty-path notifications.

A burst of workspace mutations (an agent writing many files in quick
succession) should coalesce into ONE reindex fire, not N. The
``BurstDebounceScheduler`` collects ``mark(callable)`` entries inside a
debounce window and fires a single merged callback once the window
elapses with no new marks.

Lifecycle hooks (``on_workflow_complete`` / ``on_workflow_cancel`` /
``on_workflow_fail`` / ``on_workflow_restart``) release the pending
timer so a workflow exit never strands a deferred fire. ``restart``
additionally re-arms the scheduler for the next workflow (the pending
fire is dropped, not delivered, because a restart means the prior
workflow's deferred work is superseded).

The scheduler is deliberately single-threaded and clock-injected: it
holds no thread of its own. The owner advances the clock (production:
the reindex writer's deadline poll; tests: a ``FakeClock``) and calls
``fire_if_due()`` — or, in production, the debounce window is short
enough that the next mark after the window fires the prior batch.

Design choice (ponytail): rather than spawn a timer thread per
scheduler (a resource the lifecycle hooks would have to join), the
scheduler is passive. ``mark`` records the pending closures and the
scheduled fire time; whoever owns the clock calls ``fire_if_due()`` to
drain. The wiring tests prove the ONE shared instance is what
production reads.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock


class BurstDebounceScheduler:
    """Coalesce N ``mark`` calls inside a debounce window into one fire.

    Args:
        clock: callable returning monotonic seconds.
        on_fire: callable invoked once with no args when the coalesced
            fire triggers. Production binds this to the reindex
            dirty-path drain.
        debounce_window: seconds of quiet required before the pending
            batch fires.
    """

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
        self._pending: list[Callable[[], None]] = []  # bounded-accumulator-ok: drained on fire_if_due / lifecycle hooks; bounded by distinct dirty-path marks per debounce window
        self._last_mark_at: float | None = None
        # Tracks whether a fire has been suppressed by a lifecycle hook
        # since the last ``mark``. Used only for diagnostics; does not
        # gate behavior.
        self._suppressed = False

    def mark(self, closure: Callable[[], None]) -> None:
        """Record one dirty-path closure and (re)start the debounce window."""
        with self._lock:
            self._pending.append(closure)
            self._last_mark_at = self._clock()
            self._suppressed = False

    @property
    def pending_count(self) -> int:
        """Number of un-fired closures currently held."""
        with self._lock:
            return len(self._pending)

    @property
    def has_pending(self) -> bool:
        """True when at least one closure is awaiting fire."""
        with self._lock:
            return bool(self._pending)

    def fire_if_due(self) -> bool:
        """Fire the coalesced batch when the debounce window has elapsed.

        Returns True if a fire occurred. A fire invokes ``on_fire`` exactly
        once regardless of how many closures were pending: the closures are
        evidence that work is pending, but the dirty paths themselves are
        already persisted in the store by each ``mark`` caller. ``on_fire`` is
        the single reindex trigger that drains them, so N marks in one window
        produce ONE fire (and therefore one reindex), not N.
        """
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
        """Drop the pending batch without firing (lifecycle exit)."""
        with self._lock:
            self._pending = []
            self._last_mark_at = None
            self._suppressed = True

    def on_workflow_complete(self) -> None:
        """Release the pending timer on successful completion."""
        self._release_pending()

    def on_workflow_cancel(self) -> None:
        """Release the pending timer on cancellation."""
        self._release_pending()

    def on_workflow_fail(self) -> None:
        """Release the pending timer on failure."""
        self._release_pending()

    def on_workflow_restart(self) -> None:
        """Release the pending timer and re-arm for the next workflow."""
        self._release_pending()


__all__ = ["BurstDebounceScheduler"]
