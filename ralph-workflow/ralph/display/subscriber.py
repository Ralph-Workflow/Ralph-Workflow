"""Queue-backed asyncio+threading-safe state→snapshot bridge."""

from __future__ import annotations

from queue import Full, Queue
from typing import TYPE_CHECKING

from loguru import logger

from ralph.display.prompt_reader import find_prompt_path, read_prompt_preview
from ralph.display.snapshot import DashboardSnapshot, snapshot_from_state

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.state import PipelineState


class DashboardSubscriber:
    """Receives PipelineState after each reducer reduce and enqueues a DashboardSnapshot.

    Thread and asyncio safe: notify() only calls put_nowait() which is documented
    as thread-safe and never blocks. Prompt preview is read once at construction.
    """

    def __init__(
        self,
        *,
        queue: Queue[DashboardSnapshot],
        workspace_root: Path,
        run_id: str,
        prompt_reader: Callable[[Path], tuple[str, ...]] = read_prompt_preview,
    ) -> None:
        self._queue = queue
        self._run_id = run_id
        self._dropped_count = 0

        prompt_path = find_prompt_path(workspace_root)
        self._prompt_path: str | None = str(prompt_path) if prompt_path is not None else None
        self._prompt_preview: tuple[str, ...] = (
            prompt_reader(prompt_path) if prompt_path is not None else ()
        )

    @property
    def queue(self) -> Queue[DashboardSnapshot]:
        return self._queue

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    def notify(self, state: PipelineState) -> None:
        """Build a DashboardSnapshot from state and enqueue it non-blocking.

        Never blocks. On queue.Full, increments dropped_count and logs at DEBUG.
        Safe to call from both sync (runner.py) and async (coordinator.py) contexts.
        """
        snapshot = snapshot_from_state(
            state,
            prompt_path=self._prompt_path,
            prompt_preview=self._prompt_preview,
            run_id=self._run_id,
        )
        try:
            self._queue.put_nowait(snapshot)
        except Full:
            self._dropped_count += 1
            logger.debug(
                "DashboardSubscriber: queue full, snapshot dropped (total dropped={})",
                self._dropped_count,
            )


__all__ = ["DashboardSubscriber"]
