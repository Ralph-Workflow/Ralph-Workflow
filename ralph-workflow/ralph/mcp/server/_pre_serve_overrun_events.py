
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class StageEventRecorder:

    def __init__(self, *, max_events: int = 64) -> None:
        self._events: deque[str] = deque(maxlen=max_events)

    def __call__(self, root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        self._events.append(stage)

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(self._events)

    @property
    def last_stage(self) -> str | None:
        return self._events[-1] if self._events else None


__all__ = ["StageEventRecorder"]
