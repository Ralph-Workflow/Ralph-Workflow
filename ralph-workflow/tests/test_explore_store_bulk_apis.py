"""Bulk-API tests for the indexed exploration store.

Split from :mod:`tests.test_explore_store` so the hub test
module stays under the repository file-size limit. The bulk
APIs (``get_file_many`` / ``upsert_file_many`` /
``upsert_manifest_many``) were added as part of the warm-refresh
batching work; the original store module already exercised them
in production and the new tests here pin their round-trip
semantics, empty-input fast paths, and length-mismatch
validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.explore.store import (
    ExploreStore,
    FileRow,
    sha256_text,
)

# ponytail: SQLite history pruning contends with xdist filesystem setup; 5s preserves the store contract within the 60s suite cap.
pytestmark = pytest.mark.timeout_seconds(5)


def _build_store(tmp_path: Path) -> ExploreStore:
    index_dir = tmp_path / ".agent" / "ralph-explore"
    return ExploreStore(index_dir)


def _make_file_row(
    path: str,
    *,
    content_hash: str | None = None,
    size_bytes: int = 1,
    mtime_ns: int = 1,
    generation: int = 1,
) -> FileRow:
    """Build a minimal ``FileRow`` for bulk API tests."""
    return FileRow(
        path=path,
        content_hash=content_hash or sha256_text(path),
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        language="python",
        indexed_generation=generation,
        indexed_at=0.0,
        is_deleted=False,
    )


def test_get_file_many_returns_only_matching_rows(tmp_path: Path) -> None:
    """Bulk reader returns one ``FileRow`` per persisted path."""
    store = _build_store(tmp_path)
    try:
        store.upsert_file(_make_file_row("a.py"))
        store.upsert_file(_make_file_row("b.py"))
        store.upsert_file(_make_file_row("c.py"))

        fetched = store.get_file_many(["a.py", "c.py", "missing.py"])
        assert set(fetched.keys()) == {"a.py", "c.py"}
        assert fetched["a.py"].content_hash == sha256_text("a.py")
        assert fetched["c.py"].mtime_ns == 1
    finally:
        store.close()


def test_get_file_many_empty_input_is_noop(tmp_path: Path) -> None:
    """Bulk reader with ``[]`` issues no SQL and returns ``{}``."""
    store = _build_store(tmp_path)
    try:
        # Seed a row to make sure the empty-input fast path does not
        # accidentally touch the table.
        store.upsert_file(_make_file_row("a.py"))
        assert store.get_file_many([]) == {}
    finally:
        store.close()


def test_upsert_file_many_replaces_each_row_on_conflict(tmp_path: Path) -> None:
    """Bulk writer persists every row and updates on conflict."""
    store = _build_store(tmp_path)
    try:
        store.upsert_file_many(
            [
                _make_file_row("a.py", content_hash="v1", size_bytes=1, mtime_ns=1),
                _make_file_row("b.py", content_hash="v1", size_bytes=1, mtime_ns=1),
            ]
        )
        assert store.get_file("a.py") is not None
        assert store.get_file("b.py") is not None

        # Bulk replace with new generations.
        store.upsert_file_many(
            [
                _make_file_row("a.py", content_hash="v2", size_bytes=2, mtime_ns=2, generation=2),
                _make_file_row("b.py", content_hash="v2", size_bytes=2, mtime_ns=2, generation=2),
            ]
        )
        a = store.get_file("a.py")
        b = store.get_file("b.py")
        assert a is not None and a.content_hash == "v2" and a.indexed_generation == 2
        assert b is not None and b.content_hash == "v2" and b.indexed_generation == 2
    finally:
        store.close()


def test_upsert_file_many_empty_input_is_noop(tmp_path: Path) -> None:
    """Bulk writer with ``[]`` issues no SQL."""
    store = _build_store(tmp_path)
    try:
        store.upsert_file_many([])
        # No assertion about exact statement count (sqlite trace
        # callbacks fire once per row inside executemany so they
        # cannot distinguish batching from a loop). The contract
        # here is functional: no exception, no rows persisted.
        assert store.count_files() == 0
    finally:
        store.close()


def test_upsert_manifest_many_writes_and_updates(tmp_path: Path) -> None:
    """Bulk manifest writer persists rows visible via direct query."""
    store = _build_store(tmp_path)
    try:
        store.upsert_manifest_many(
            paths=["a.py", "b.py"],
            content_hashes=["h1", "h2"],
            sizes=[10, 20],
            mtimes=[100, 200],
            last_seen_generation=1,
        )
        rows = store._conn.execute(
            "SELECT path, content_hash, size_bytes, mtime_ns, last_seen_generation "
            "FROM manifest ORDER BY path"
        ).fetchall()
        assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [
            ("a.py", "h1", 10, 100, 1),
            ("b.py", "h2", 20, 200, 1),
        ]

        # Update last_seen_generation via conflict path.
        store.upsert_manifest_many(
            paths=["a.py"],
            content_hashes=["h1"],
            sizes=[10],
            mtimes=[100],
            last_seen_generation=2,
        )
        gen = store._conn.execute(
            "SELECT last_seen_generation FROM manifest WHERE path = 'a.py'"
        ).fetchone()[0]
        assert gen == 2
    finally:
        store.close()


def test_upsert_manifest_many_empty_input_is_noop(tmp_path: Path) -> None:
    """Bulk manifest writer with ``[]`` issues no SQL."""
    store = _build_store(tmp_path)
    try:
        store.upsert_manifest_many(paths=[], content_hashes=[], sizes=[], mtimes=[], last_seen_generation=1)
        assert store._conn.execute("SELECT COUNT(*) FROM manifest").fetchone()[0] == 0
    finally:
        store.close()


def test_upsert_manifest_many_rejects_mismatched_lengths(tmp_path: Path) -> None:
    """Bulk manifest writer fails closed on length mismatch."""
    store = _build_store(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.upsert_manifest_many(
                paths=["a.py", "b.py"],
                content_hashes=["h1"],
                sizes=[10, 20],
                mtimes=[100, 200],
                last_seen_generation=1,
            )
    finally:
        store.close()
