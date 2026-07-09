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

from typing import Protocol, cast

from ralph.mcp.explore.store import ExploreStore, normalize_index_path


class ExploreIndexLike(Protocol):
    """The narrow protocol handlers consume to mark dirty paths.

    Implemented by :class:`ralph.mcp.explore.handlers.ExploreIndex`.
    Kept as a Protocol so tests can fake it with a one-line stub
    without inheriting from a heavy base class.
    """

    def mark_dirty(self, paths: list[str], *, source_tool: str, reason: str = "mutated") -> None:
        ...

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

    def get_setting(self, key: str) -> str | None:
        ...

    def peek_dirty_paths(self) -> list[str]:
        ...

    def iter_files(self) -> object:  # Iterator[FileRow]
        ...

    def iter_symbols(self, path: str | None = None) -> object:
        ...

    def insert_evidence(self, row: object) -> None:
        ...

    def mark_dirty(
        self,
        path: str,
        *,
        reason: str,
        source_tool: str,
        now: float | None = None,
    ) -> None:
        ...


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


def mark_path(
    handle: ExploreIndexLike | None,
    *,
    path: str,
    source_tool: str,
    reason: str = "mutated",
) -> None:
    """Helper that always coerces the path before calling the handle.

    Centralizes the path-normalization call so handlers stay tidy.
    """
    if handle is None:
        return
    normalized = normalize_index_path(path)
    handle.mark_dirty([normalized], source_tool=source_tool, reason=reason)


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
    normalized = [normalize_index_path(p) for p in paths]
    handle.mark_dirty(normalized, source_tool=source_tool, reason=reason)


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
                store.mark_dirty(
                    path, reason=reason, source_tool=source_tool
                )

        @property
        def store(self) -> ExploreStoreLike | None:
            return cast("ExploreStoreLike | None", store)

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
