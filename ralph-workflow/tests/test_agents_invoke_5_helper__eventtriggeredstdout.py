from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading


class _EventTriggeredStdout:
    """Stdout that yields one line when an event fires, then EOF."""

    def __init__(self, line: str, trigger: threading.Event) -> None:
        self._line = line
        self._trigger = trigger
        self._done = False

    def __iter__(self) -> _EventTriggeredStdout:
        return self

    def __next__(self) -> str:
        if not self._done:
            self._trigger.wait()
            self._done = True
            return self._line
        raise StopIteration
