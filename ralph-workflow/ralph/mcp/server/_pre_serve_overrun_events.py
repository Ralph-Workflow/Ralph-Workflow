"""In-memory startup stage recorder for the bounded pre-serve path.

``StageEventRecorder`` is the public observation seam for the startup stage
sequence: a caller registers it (through
:func:`ralph.mcp.server._pre_serve_overrun.stage_recorder_override`) to assert
the stage labels a child recorded -- and the stage attribution of a fail-closed
exit -- without reading the on-disk stage file.

The class lives in its own module so ``_pre_serve_overrun`` keeps a single
top-level class (the repo-structure policy allows one public top-level class
per module). It is re-exported from :mod:`ralph.mcp.server._pre_serve_overrun`,
so every existing importer keeps working unchanged.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class StageEventRecorder:
    """In-memory startup stage recorder that remembers the stage labels it is handed.

    Public observation seam: a caller registers it to assert the startup
    stage sequence -- and the stage attribution of a fail-closed exit --
    without reading the on-disk stage file.
    """

    def __init__(self, *, max_events: int = 64) -> None:
        """Create a recorder that keeps at most ``max_events`` stage labels."""
        self._events: deque[str] = deque(maxlen=max_events)  # bounded-accumulator-ok: max_events

    def __call__(self, root: Path, stage: str, *, timeout_s: float | None = None) -> None:
        """Record one startup stage label."""
        self._events.append(stage)

    @property
    def stages(self) -> tuple[str, ...]:
        """Return the recorded stage labels in order."""
        return tuple(self._events)

    @property
    def last_stage(self) -> str | None:
        """Return the most recently recorded stage label, or ``None``."""
        return self._events[-1] if self._events else None


__all__ = ["StageEventRecorder"]
