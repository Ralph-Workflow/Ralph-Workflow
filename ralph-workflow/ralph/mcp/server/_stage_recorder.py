"""StageRecorder protocol for the bounded pre-serve path.

The class lives in its own module so ``_pre_serve_overrun`` conforms to the
repo-structure policy (at most one public top-level class per module). It is
re-exported from :mod:`ralph.mcp.server._pre_serve_overrun`, so every existing
importer keeps working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class StageRecorder(Protocol):
    """Callable that persists one startup stage marker for a workspace root.

    The production implementation is the on-disk stage tracker
    (:func:`ralph.mcp.server.start_stage_tracker.record`). A caller that
    needs a different sink injects one through the ``stage_recorder``
    argument of :func:`log_and_record_stage` /
    :func:`fail_closed_pre_serve_overrun`, or through
    :func:`stage_recorder_override` for the current context.
    """

    def __call__(self, root: Path, stage: str, *, timeout_s: float | None = None) -> None: ...


__all__ = ["StageRecorder"]
