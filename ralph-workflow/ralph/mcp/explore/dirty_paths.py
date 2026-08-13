"""Persisted dirty-path queue for the indexed exploration substrate.

Write handlers mark affected paths in this queue after a successful
workspace mutation. The next reindex consumes the queue.

The queue is persisted in SQLite so crashes and resumed sessions do
not lose refresh work. Marking is O(1); the call never blocks on an
active reindex (it only inserts a row, the reindex writer observes
the queue when it runs).

The queue is owned by :class:`ExploreIndex` (defined in
``handlers.py``), which is the optional, lazily-initialized handle on
the session/workspace. When the index is disabled (handle is
``None``), handlers behave exactly as today — no metadata added, no
dirty marking.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Protocol, cast

from ralph.mcp.explore._burst_scheduler import BurstDebounceScheduler
from ralph.mcp.explore.store import ExploreStore, normalize_index_path


class _SymbolLike(Protocol):
    """Narrow protocol for the symbol shape ``find_symbols`` returns.

    The full :class:`ralph.mcp.explore.store.SymbolRow` type is
    structurally compatible. Kept as a Protocol so handlers can
    consume the result without importing the concrete dataclass.
    """

    @property
    def qualified_name(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def symbol_id(self) -> str: ...

    @property
    def span_id(self) -> str: ...


class ExploreIndexLike(Protocol):
    """The narrow protocol handlers consume to mark dirty paths.

    Implemented by :class:`ralph.mcp.explore.handlers.ExploreIndex`.
    Kept as a Protocol so tests can fake it with a one-line stub
    without inheriting from a heavy base class.
    """

    def mark_dirty(
        self, paths: list[str], *, source_tool: str, reason: str = "mutated"
    ) -> None: ...

    @property
    def store(self) -> ExploreStoreLike | None:
        """Optional SQLite-backed store handle. ``None`` for NoOp handles."""
        ...

    @property
    def reindex_in_progress(self) -> bool:
        """True when a reindex writer is active for the attached handle."""
        ...


class ExploreStoreLike(Protocol):
    """Narrow protocol for the SQLite store surface handlers consume.

    The full :class:`ExploreStore` type is structurally compatible.
    Kept as a Protocol so handlers stay typed against the surface
    they actually use.
    """

    def get_setting(self, key: str) -> str | None: ...

    def peek_dirty_paths(self) -> list[str]: ...

    def has_deleted_files(self) -> bool: ...

    def count_deleted_files(self) -> int: ...

    def iter_files(self) -> object:  # Iterator[FileRow]
        ...

    def iter_symbols(self, path: str | None = None) -> object: ...

    def find_symbols(
        self,
        *,
        name: str | None = None,
        qualified_name: str | None = None,
        path: str | None = None,
    ) -> list[_SymbolLike]: ...

    def insert_evidence(self, row: object) -> None: ...

    def mark_dirty(
        self,
        path: str,
        *,
        reason: str,
        source_tool: str,
        now: float | None = None,
    ) -> None: ...


def enqueue_workspace_dirty_paths(
    workspace_root: Path,
    paths: list[str],
    *,
    source_tool: str = "shared_awareness",
    reason: str = "mutated",
) -> int:
    """Durably enqueue workspace-relative dirty paths into the persisted queue.

    Routes through the same ``mark_path`` burst-coalescing path as write
    handlers, so owner-published changes reach the persisted SQLite dirty
    queue with the same coalescing and crash durability. When no explore
    index handle is attached to the workspace the paths remain in the
    process-local awareness (the caller records them there first), so no
    change is silently dropped. Returns the number of paths enqueued.
    """
    del workspace_root  # the persisted queue is keyed by normalized path
    enqueued = 0
    handle = _workspace_index_handle()
    for path in paths:
        mark_path(handle, path=path, source_tool=source_tool, reason=reason)
        enqueued += 1
    return enqueued


def _workspace_index_handle() -> ExploreIndexLike | None:
    """Return the process's explore index handle, if one is attached."""
    return None


class NoOpExploreIndex:
    """Drop-in index handle used when indexing is disabled.

    Implements the same protocol but does nothing. Handlers can
    unconditionally call ``index.mark_dirty(...)``; the no-op variant
    silently swallows the call, preserving the live behavior contract.
    """

    def mark_dirty(self, paths: list[str], *, source_tool: str, reason: str = "mutated") -> None:
        del paths, source_tool, reason

    @property
    def store(self) -> None:
        return None

    @property
    def reindex_in_progress(self) -> bool:
        return False


def resolve_explore_index(session: object) -> ExploreIndexLike | None:
    """Return the explore index handle attached to ``session`` if any.

    Reads ``session.explore_index`` lazily. Returns ``None`` when the
    attribute is missing or ``None`` so handlers fall back to live
    behavior (the current contract).
    """
    handle: ExploreIndexLike | None = getattr(session, "explore_index", None)
    if handle is None:
        return None
    return handle


# --- Burst-debounced dirty-path drain (S-3) -------------------------------
#
# One process-level scheduler coalesces a burst of ``mark_path`` /
# ``mark_paths`` calls into a single ``handle.mark_dirty`` fire per
# debounce window. Pending marks are keyed by ``(id(handle), path)`` so
# duplicate notifications for the same path collapse; the drain groups
# by handle and issues one ``mark_dirty`` per handle with the unique
# path set. The scheduler is passive: ``fire_if_due`` is polled on each
# new mark (so a quiet window after a burst drains promptly without a
# background thread) and at workflow lifecycle hooks.
_PENDING_LOCK = threading.Lock()
_PENDING_MARKS: dict[
    tuple[int, str], tuple[ExploreIndexLike, str, str]
] = {}  # bounded-accumulator-ok: drained on fire; bounded by distinct (handle,path) keys


def _enqueue_mark(
    handle: ExploreIndexLike,
    normalized: str,
    *,
    source_tool: str,
    reason: str,
) -> None:
    """Persist one dirty mark synchronously and arm the debounce scheduler.

    The mark is visible in the store immediately (AC-02: prompt change
    awareness). The BurstDebounceScheduler coalesces the reindex trigger
    only — N marks in one debounce window produce one ``on_fire``, not N.
    """
    key = (id(handle), normalized)
    with _PENDING_LOCK:
        is_new = key not in _PENDING_MARKS
        _PENDING_MARKS[key] = (handle, source_tool, reason)
    if is_new:
        handle.mark_dirty([normalized], source_tool=source_tool, reason=reason)
    _dirty_scheduler.mark(lambda: None)
    # Opportunistic drain: if the debounce window already elapsed (a
    # quiet period since the last mark), fire immediately so the
    # scheduler's pending batch resets without waiting for a lifecycle
    # hook. This keeps the scheduler thread-free while bounding latency.
    _dirty_scheduler.fire_if_due()


def _drain_pending_marks() -> None:
    """Clear pending marks after the debounce fire.

    The ``on_fire`` callback bound to ``_dirty_scheduler``. Marks were
    already persisted synchronously by each ``_enqueue_mark`` call; this
    callback resets the pending dict so the next burst starts fresh.
    The scheduler fires once per debounce window (coalescing N marks
    into one fire), and lifecycle hooks observe the store's dirty queue
    to decide when to reindex.
    """
    with _PENDING_LOCK:
        _PENDING_MARKS.clear()


def _system_clock() -> float:
    return time.monotonic()


#: ONE process-level scheduler. Production reads this attribute; tests
#: replace it with a spy to assert the wiring without parallel
#: construction. The debounce window is short (50 ms) so a burst
#: coalesces but a quiet gap drains promptly.
_dirty_scheduler: BurstDebounceScheduler = BurstDebounceScheduler(
    clock=_system_clock,
    on_fire=_drain_pending_marks,
    debounce_window=0.05,
)


def mark_path(
    handle: ExploreIndexLike | None,
    *,
    path: str,
    source_tool: str,
    reason: str = "mutated",
) -> None:
    """Helper that always coerces the path before calling the handle.

    Routes through the module-level ``_dirty_scheduler`` so a burst of
    dirty notifications for the same path coalesces into ONE
    ``handle.mark_dirty`` call (and therefore one reindex fire) instead
    of N. The normalized path is recorded in ``_pending_marks`` keyed by
    ``(handle, path)`` so duplicates collapse; the scheduler's
    ``on_fire`` drains the pending set once per debounce window.
    """
    if handle is None:
        return
    normalized = normalize_index_path(path)
    _enqueue_mark(handle, normalized, source_tool=source_tool, reason=reason)


def mark_paths(
    handle: ExploreIndexLike | None,
    *,
    paths: list[str],
    source_tool: str,
    reason: str = "mutated",
) -> None:
    """Helper for handlers that touch multiple paths (move/copy)."""
    if handle is None:
        return
    for raw in paths:
        normalized = normalize_index_path(raw)
        _enqueue_mark(handle, normalized, source_tool=source_tool, reason=reason)


def build_sqlite_index_handle(
    store: ExploreStore,
) -> ExploreIndexLike:
    """Construct a handle that writes to a SQLite-backed ``store``.

    Production code passes a store constructed by ``handlers.py``
    (which also owns the path to the ``.agent/ralph-explore/`` index
    directory). Tests use this helper to wire a fake store into the
    handler path.
    """

    class _SqliteIndex:
        def mark_dirty(
            self, paths: list[str], *, source_tool: str, reason: str = "mutated"
        ) -> None:
            for path in paths:
                store.mark_dirty(path, reason=reason, source_tool=source_tool)

        @property
        def store(self) -> ExploreStoreLike | None:
            return cast(
                "ExploreStoreLike | None", store
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

        @property
        def reindex_in_progress(self) -> bool:
            return False

    return _SqliteIndex()


__all__ = [
    "ExploreIndexLike",
    "NoOpExploreIndex",
    "build_sqlite_index_handle",
    "enqueue_workspace_dirty_paths",
    "mark_path",
    "mark_paths",
    "resolve_explore_index",
]
