
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

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore

logger = logging.getLogger(__name__)


class ReindexWriter:

    _lock_factory: Callable[[], threading.Lock] = threading.Lock
    _active: dict[
        str, ReindexWriter
    ] = {}
    _active_lock: threading.Lock = threading.Lock()

    @classmethod
    def configure(cls, *, lock_factory: Callable[[], threading.Lock]) -> None:
        cls._lock_factory = lock_factory
        cls._active_lock = lock_factory()

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
        with cls._active_lock:
            active = cls._active.get(key)
            if active is not None:
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
            cls._active[key] = cls(store)
        try:
            return reindex(store, workspace_root, options=options, cancel=cancel)
        finally:
            with cls._active_lock:
                cls._active.pop(key, None)


__all__ = [
    "ReindexOptions",
    "ReindexResult",
    "ReindexWriter",
]
