
from __future__ import annotations

import threading
import time
from typing import Protocol, cast

from ralph.mcp.explore._burst_scheduler import BurstDebounceScheduler
from ralph.mcp.explore.store import ExploreStore, normalize_index_path


class _SymbolLike(Protocol):

    @property
    def qualified_name(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def symbol_id(self) -> str: ...

    @property
    def span_id(self) -> str: ...


class ExploreIndexLike(Protocol):

    def mark_dirty(
        self, paths: list[str], *, source_tool: str, reason: str = "mutated"
    ) -> None: ...

    @property
    def store(self) -> ExploreStoreLike | None:
        ...

    @property
    def reindex_in_progress(self) -> bool:
        ...


class ExploreStoreLike(Protocol):

    def get_setting(self, key: str) -> str | None: ...

    def peek_dirty_paths(self) -> list[str]: ...

    def has_deleted_files(self) -> bool: ...

    def count_deleted_files(self) -> int: ...

    def iter_files(self) -> object:
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


class NoOpExploreIndex:

    def mark_dirty(self, paths: list[str], *, source_tool: str, reason: str = "mutated") -> None:
        del paths, source_tool, reason

    @property
    def store(self) -> None:
        return None

    @property
    def reindex_in_progress(self) -> bool:
        return False


def resolve_explore_index(session: object) -> ExploreIndexLike | None:
    handle: ExploreIndexLike | None = getattr(session, "explore_index", None)
    if handle is None:
        return None
    return handle


_PENDING_LOCK = threading.Lock()
_PENDING_MARKS: dict[
    tuple[int, str], tuple[ExploreIndexLike, str, str]
] = {}


def _enqueue_mark(
    handle: ExploreIndexLike,
    normalized: str,
    *,
    source_tool: str,
    reason: str,
) -> None:
    key = (id(handle), normalized)
    with _PENDING_LOCK:
        is_new = key not in _PENDING_MARKS
        _PENDING_MARKS[key] = (handle, source_tool, reason)
    if is_new:
        handle.mark_dirty([normalized], source_tool=source_tool, reason=reason)
    _dirty_scheduler.mark(lambda: None)
    _dirty_scheduler.fire_if_due()


def _drain_pending_marks() -> None:
    with _PENDING_LOCK:
        _PENDING_MARKS.clear()


def _system_clock() -> float:
    return time.monotonic()


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
    if handle is None:
        return
    for raw in paths:
        normalized = normalize_index_path(raw)
        _enqueue_mark(handle, normalized, source_tool=source_tool, reason=reason)


def build_sqlite_index_handle(
    store: ExploreStore,
) -> ExploreIndexLike:

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
            )

        @property
        def reindex_in_progress(self) -> bool:
            return False

    return _SqliteIndex()


__all__ = [
    "ExploreIndexLike",
    "NoOpExploreIndex",
    "build_sqlite_index_handle",
    "mark_path",
    "mark_paths",
    "resolve_explore_index",
]
