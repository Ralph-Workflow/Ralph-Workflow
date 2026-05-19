from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading


class _ScheduledStdout:
    """Stdout that yields each line after its corresponding event fires."""

    def __init__(self, scheduled_lines: list[tuple[str, threading.Event]]) -> None:
        self._scheduled_lines = list(scheduled_lines)

    def __iter__(self) -> _ScheduledStdout:
        return self

    def __next__(self) -> str:
        if not self._scheduled_lines:
            raise StopIteration
        line, trigger = self._scheduled_lines.pop(0)
        trigger.wait()
        return line
