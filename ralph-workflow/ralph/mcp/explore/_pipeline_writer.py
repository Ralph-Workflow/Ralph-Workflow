from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore._pipeline_state import (
    ReindexOptions,
    ReindexResult,
)
from ralph.mcp.explore._thread_state import ActiveWriterBox

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore

logger = logging.getLogger(__name__)

_ACTIVE_WRITERS = ActiveWriterBox()


class ReindexWriter:
    __slots__ = ("dirty_paths_count", "generation", "store")

    @classmethod
    def configure(cls, *, lock_factory: Callable[[], threading.Lock]) -> None:
        _ACTIVE_WRITERS.configure(lock_factory=lock_factory)

    def __init__(self, store: ExploreStore) -> None:
        self.store = store
        self.generation = int(store.get_setting("current_generation") or 0)
        self.dirty_paths_count = len(store.peek_dirty_paths())

    @classmethod
    def claim(
        cls,
        store: ExploreStore,
        *,
        workspace_root: Path,
        options: ReindexOptions | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> ReindexResult:
        from ralph.mcp.explore.pipeline import reindex

        key = str(store.db_path)
        with _ACTIVE_WRITERS.lock:
            active_obj = _ACTIVE_WRITERS.active.get(key)
            if active_obj is not None:
                if not isinstance(active_obj, ReindexWriter):
                    msg = "active reindex writer has unexpected type"
                    raise TypeError(msg)
                active = active_obj
                if options is None:
                    options = ReindexOptions()
                if cancel is not None and cancel():
                    return ReindexResult(
                        job_id=f"coalesced-cancel-{key}",
                        generation=active.generation,
                        status="cancelled",
                        dirty_paths_count=active.dirty_paths_count,
                        elapsed_seconds=0.0,
                    )
                return ReindexResult(
                    job_id=f"coalesced-{active.store.db_path.name}",
                    generation=active.generation,
                    status="skipped_no_changes",
                    dirty_paths_count=active.dirty_paths_count,
                    elapsed_seconds=0.0,
                )
            _ACTIVE_WRITERS.active[key] = cls(store)
        try:
            return reindex(store, workspace_root, options=options, cancel=cancel)
        finally:
            with _ACTIVE_WRITERS.lock:
                _ACTIVE_WRITERS.active.pop(key, None)


__all__ = [
    "ReindexOptions",
    "ReindexResult",
    "ReindexWriter",
]
