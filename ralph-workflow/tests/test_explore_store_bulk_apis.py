from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.explore.store import (
    ExploreStore,
    FileRow,
    sha256_text,
)

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
    store = _build_store(tmp_path)
    try:
        store.upsert_file(_make_file_row("a.py"))
        assert store.get_file_many([]) == {}
    finally:
        store.close()


def test_upsert_file_many_replaces_each_row_on_conflict(tmp_path: Path) -> None:
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
    store = _build_store(tmp_path)
    try:
        store.upsert_file_many([])
        assert store.count_files() == 0
    finally:
        store.close()


def test_upsert_manifest_many_rejects_mismatched_lengths(tmp_path: Path) -> None:
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


def test_bulk_size_mtime_for_paths_skips_removed_rows(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        store.upsert_file_many(
            [
                _make_file_row("a.py", size_bytes=10, mtime_ns=100),
                _make_file_row("b.py", size_bytes=20, mtime_ns=200),
                _make_file_row("c.py", size_bytes=30, mtime_ns=300),
            ]
        )
        store.delete_file_rows("b.py")

        fetched = store.bulk_size_mtime_for_paths(["a.py", "b.py", "c.py", "missing.py"])
        assert fetched == {
            "a.py": (10, 100),
            "c.py": (30, 300),
        }
    finally:
        store.close()


def test_bulk_size_mtime_for_paths_empty_input_is_noop(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        store.upsert_file(_make_file_row("a.py"))
        assert store.bulk_size_mtime_for_paths([]) == {}
    finally:
        store.close()
