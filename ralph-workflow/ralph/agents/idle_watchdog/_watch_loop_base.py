"""Template-method base for agent timeout watchdog poll loops.

WatchLoopBase provides a ``wait_until`` template method that polls a predicate
at a configurable interval. Subclasses call ``wait_until`` from their public
methods instead of writing ad-hoc ``clock.monotonic() < deadline`` loops.

An injected ``Clock`` (the ``Clock`` protocol from ``ralph.agents.clock``)
keeps the base fully testable with ``FakeClock`` — no real wall-clock waits.

The base maintains a ``threading.Event`` that ``signal_activity`` can pulse
to wake a blocked ``wait_until`` immediately in multi-threaded contexts.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock

__all__ = ["WatchLoopBase"]


T = TypeVar("T")


class WatchLoopBase:
    """Template-method base for poll-loop watchdogs.

    Subclass and call ``self.wait_until(...)`` from public methods.
    Call ``self.signal_activity()`` from another thread to wake a
    blocked ``wait_until`` early.
    """

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
        """Poll *predicate* until it returns truthy or *timeout_s* elapses.

        The predicate is checked on entry (before the first wait) so
        already-true conditions return immediately with zero clock
        budget consumed.

        On each tick the poll interval is spent in
        ``self.clock.wait_for_event(self._event, poll_interval_s)`` so that
        ``signal_activity()`` can wake the loop early when activity is
        detected on another thread.

        Args:
            predicate: Zero-arg callable; return a truthy value when the
                waited-for condition is met, or a falsy value (None) to
                keep polling.
            timeout_s: Wall-clock deadline measured from the call.
            poll_interval_s: Seconds to wait between predicate checks.
            on_tick: Called after each predicate check with the predicate's
                return value (falsy values included).  Never called on the
                entry check.  Useful for progress/debug logging.

        Returns:
            The predicate's truthy return value, or ``None`` on timeout.
        """
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
        """Pulse the internal event to wake a blocked ``wait_until``.

        Safe to call from any thread.  The wake is a single-pulse
        (set-then-clear) so repeated calls without intervening waits
        are harmless.
        """
        self._event.set()
        self._event.clear()
