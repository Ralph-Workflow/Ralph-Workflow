"""Black-box tests for ExploreStore fts_search_grep and batch evidence insertion."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.explore.store import (
    ChunkRow,
    EvidenceRow,
    ExploreStore,
    FileRow,
    derive_chunk_id,
    derive_evidence_id,
)

pytestmark = pytest.mark.timeout_seconds(5)


def _build_store(tmp_path: Path) -> ExploreStore:
    index_dir = tmp_path / ".agent" / "ralph-explore"
    return ExploreStore(index_dir)


def _make_indexed_file(
    store: ExploreStore,
    *,
    path: str,
    content: str,
    text_hash: str,
    content_hash: str,
) -> None:
    """Insert one file row + one chunk + one FTS row for a test path."""
    store.upsert_file(
        FileRow(
            path=path,
            content_hash=content_hash,
            size_bytes=len(content),
            mtime_ns=1,
            language="python",
            indexed_generation=1,
            indexed_at=0.0,
            is_deleted=False,
        )
    )
    chunk_id = derive_chunk_id(
        path=path,
        start_line=1,
        end_line=content.count("\n") + 1,
        text_hash=text_hash,
        extractor_version="v1",
    )
    chunk = ChunkRow(
        chunk_id=chunk_id,
        path=path,
        start_line=1,
        end_line=content.count("\n") + 1,
        text_hash=text_hash,
        role="body",
        generation=1,
    )
    store.upsert_chunk(chunk, content)


def test_fts_search_grep_returns_joined_chunk_metadata(tmp_path: Path) -> None:
    """``fts_search_grep`` returns path, chunk_id, full text, start_line,
    end_line, text_hash, generation, and the file's content_hash in one
    row, so the grep handler can post-filter + build evidence rows
    without per-candidate SELECTs."""
    store = _build_store(tmp_path)
    try:
        _make_indexed_file(
            store,
            path="a.py",
            content="def hello():\n    return 42\n",
            text_hash="aa" * 32,
            content_hash="bb" * 32,
        )
        rows = store.fts_search_grep("hello", limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["path"] == "a.py"
        assert isinstance(row["chunk_id"], str) and row["chunk_id"]
        assert "hello" in row["text"]
        assert row["start_line"] == 1
        assert row["end_line"] == 3
        assert row["text_hash"] == "aa" * 32
        assert row["generation"] == 1
        assert row["file_content_hash"] == "bb" * 32
    finally:
        store.close()


def test_fts_search_grep_excludes_deleted_files(tmp_path: Path) -> None:
    """The JOIN's ``f.is_deleted = 0`` filter must drop deleted-file
    matches so the index never surfaces a path the file row already
    marked as gone."""
    store = _build_store(tmp_path)
    try:
        _make_indexed_file(
            store,
            path="live.py",
            content="def hello():\n    return 1\n",
            text_hash="cc" * 32,
            content_hash="dd" * 32,
        )
        _make_indexed_file(
            store,
            path="gone.py",
            content="def hello():\n    return 2\n",
            text_hash="ee" * 32,
            content_hash="ff" * 32,
        )
        # Mark gone.py deleted in file record to test JOIN filter.
        store.upsert_file(
            FileRow(
                path="gone.py",
                content_hash="ff" * 32,
                size_bytes=len("def hello():\n    return 2\n"),
                mtime_ns=1,
                language="python",
                indexed_generation=1,
                indexed_at=0.0,
                is_deleted=True,
            )
        )
        rows = store.fts_search_grep("hello", limit=10)
        paths = {row["path"] for row in rows}
        assert paths == {"live.py"}
    finally:
        store.close()


def test_insert_evidence_batch_inserts_and_refreshes(tmp_path: Path) -> None:
    """``insert_evidence_batch`` persists every row in a single
    transaction and ON CONFLICT refreshes the existing row, so the
    grep handler can call it once per response instead of N times."""
    store = _build_store(tmp_path)
    try:
        evidence_id = derive_evidence_id(
            path="a.py",
            content_hash="x",
            start_line=1,
            end_line=5,
            kind="chunk",
            extractor_version="v1",
        )
        rows = [
            EvidenceRow(
                evidence_id=evidence_id,
                path="a.py",
                start_line=1,
                end_line=5,
                content_hash="x",
                generation=1,
                source_tool="grep_files",
                evidence_kind="chunk",
                created_at=0.0,
                is_stale=False,
            ),
            EvidenceRow(
                evidence_id=derive_evidence_id(
                    path="b.py",
                    content_hash="y",
                    start_line=2,
                    end_line=4,
                    kind="chunk",
                    extractor_version="v1",
                ),
                path="b.py",
                start_line=2,
                end_line=4,
                content_hash="y",
                generation=1,
                source_tool="grep_files",
                evidence_kind="chunk",
                created_at=0.0,
                is_stale=False,
            ),
        ]
        store.insert_evidence_batch(rows)
        fetched_a = store.get_evidence(evidence_id)
        assert fetched_a is not None and fetched_a.path == "a.py"
        assert store.get_evidence(rows[1].evidence_id) is not None
        # ON CONFLICT refresh: same evidence_id, updated path
        # should overwrite via the batch call.
        refreshed = EvidenceRow(
            evidence_id=evidence_id,
            path="a-renamed.py",
            start_line=1,
            end_line=5,
            content_hash="x",
            generation=1,
            source_tool="grep_files",
            evidence_kind="chunk",
            created_at=0.0,
            is_stale=False,
        )
        store.insert_evidence_batch([refreshed])
        fetched = store.get_evidence(evidence_id)
        assert fetched is not None and fetched.path == "a-renamed.py"
    finally:
        store.close()


def test_insert_evidence_batch_empty_is_noop(tmp_path: Path) -> None:
    """An empty batch must not open a transaction at all so a query
    that produces no matches pays zero round-trips on the evidence
    table."""
    store = _build_store(tmp_path)
    try:
        # Should not raise and should leave the table empty.
        store.insert_evidence_batch([])
        # Verify no evidence was inserted.
        assert store.get_evidence("nonexistent") is None
    finally:
        store.close()
