from __future__ import annotations

import threading


class _BlockingStdout:
    """Stdout that blocks forever — drives the idle timeout path.

    Uses FakeClock-aware coordination to avoid real wall-clock waits.
    The stdout iterator yields nothing and raises StopIteration immediately,
    but sets a done event that the test controls. The main loop's
    FakeClock.wait_for_event advances time until the watchdog fires.
    """

    def __init__(self, done_event: threading.Event | None = None) -> None:
        self._done_event = done_event or threading.Event()

    def __iter__(self) -> _BlockingStdout:
        return self

    def __next__(self) -> str:
        raise StopIteration
