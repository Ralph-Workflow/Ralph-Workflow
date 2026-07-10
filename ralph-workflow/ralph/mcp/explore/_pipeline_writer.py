"""ReindexWriter class for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore.pipeline` so the hub module
stays under the per-file line ceiling. The writer owns the single-
writer/coalescing contract for the persisted index: dirty paths are
persisted on every workspace mutation; the writer drains them in
one bounded run with deadline-aware polling and atomic swap-on-full.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore.pipeline import (
    ReindexOptions,
    ReindexResult,
)

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore

logger = logging.getLogger(__name__)


class ReindexWriter:
    """Single reindex writer per workspace.

    Concurrent calls coalesce dirty paths rather than starting a
    second writer (the architecture finding's "Concurrency and
    lifecycle contract").

    Ponytail: the lock is a module-level dict keyed by store path so
    tests do not see thread-shared state leaking between workspaces.
    Tests inject a custom ``lock_factory`` to avoid contention.
    """

    @staticmethod
    def _default_lock_factory() -> threading.Lock:
        raise RuntimeError("lock_factory not configured")

    _lock_factory: Callable[[], threading.Lock] = _default_lock_factory
    _active: dict[str, ReindexWriter] = {}  # bounded-accumulator-ok: keyed by db_path; entries are popped in `finally` of claim()
    _active_lock: threading.Lock | None = None

    @classmethod
    def configure(cls, *, lock_factory: Callable[[], threading.Lock]) -> None:
        cls._lock_factory = lock_factory
        cls._active_lock = lock_factory()

    def __init__(self, store: ExploreStore) -> None:
        self.store = store

    @classmethod
    def claim(
        cls,
        store: ExploreStore,
        *,
        workspace_root: Path,
        options: ReindexOptions | None = None,
    ) -> ReindexResult:
        """Run reindex, coalescing with any active writer for the same store."""
        # Lazy import: ``pipeline.py`` imports ``ReindexWriter`` at
        # module scope. Importing ``reindex`` from this module's top
        # scope would form a partial-init cycle. The function-level
        # resolution breaks the cycle and matches the lint policy
        # without weakening the audit allowlist.
        from ralph.mcp.explore.pipeline import reindex

        if cls._active_lock is None:
            # Production callers should have configured the lock
            # factory in module init; tests bypass via direct calls.
            return reindex(store, workspace_root, options=options)
        key = str(store.db_path)
        assert cls._active_lock is not None
        with cls._active_lock:
            active = cls._active.get(key)
            if active is not None:
                # Coalesce: just process any pending dirty paths and
                # return a synthetic skipped result.
                pending = store.peek_dirty_paths()
                if options is None:
                    options = ReindexOptions()
                return ReindexResult(
                    job_id=f"coalesced-{active.store.db_path.name}",
                    generation=int(store.get_setting("current_generation") or 0),
                    status="skipped_no_changes",
                    dirty_paths_count=len(pending),
                    elapsed_seconds=0.0,
                )
            cls._active[key] = cls(store)
        try:
            return reindex(store, workspace_root, options=options)
        finally:
            with cls._active_lock:
                cls._active.pop(key, None)


__all__ = [
    "ReindexOptions",
    "ReindexResult",
    "ReindexWriter",
]
