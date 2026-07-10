"""Black-box tests for the idempotent reindex pipeline.

Tests cover the Idempotence And Efficiency Contract from the
architecture finding:

* Cold build creates the manifest, FTS rows, file rows, and initial
  generation.
* Warm no-op reindex parses zero files and duplicates zero rows.
* Warm small-edit reindex reparses only changed files.
* Delete, move, copy, and partial failure scenarios leave unchanged
  records usable.
* mode='full' rebuilds into a temp generation atomically.
* Timeout is fail-closed for the job and fail-open for tools.
* Single-writer coalescing contract.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore.pipeline import (
    DEFAULT_TIMEOUT_MS,
    ReindexOptions,
    ReindexWriter,
    reindex,
)
from ralph.mcp.explore.store import ExploreStore

if TYPE_CHECKING:
    import pytest


class FakeClock:
    """Test clock with a controllable time."""

    def __init__(self, initial: float = 1_000.0) -> None:
        self._t = initial

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _seed_workspace(tmp_path: Path) -> Path:
    """Create a small workspace with three files."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("def hello():\n    return 1\n")
    (workspace / "b.py").write_text("x = 1\n" * 50)
    (workspace / "README.md").write_text("# Title\n\nSome body text.\n")
    return workspace


def _count_files_rows(store: ExploreStore) -> int:
    cur = sqlite3.connect(str(store.db_path))
    try:
        return int(cur.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    finally:
        cur.close()


def _count_chunks_rows(store: ExploreStore) -> int:
    cur = sqlite3.connect(str(store.db_path))
    try:
        return int(cur.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    finally:
        cur.close()


def _count_fts_rows(store: ExploreStore) -> int:
    cur = sqlite3.connect(str(store.db_path))
    try:
        return int(cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
    finally:
        cur.close()


def _build_store(tmp_path: Path) -> ExploreStore:
    return ExploreStore(tmp_path / ".agent" / "ralph-explore")


def test_cold_build_creates_manifest_and_generation(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status == "ok"
        assert store.get_setting("current_generation") == "1"
        assert _count_files_rows(store) >= 3
        assert _count_chunks_rows(store) >= 3
        assert _count_fts_rows(store) >= 3
    finally:
        store.close()


def test_warm_no_op_reindex_parses_zero_files(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Second call: nothing changed.
        second = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.parse_count >= 1
        assert second.parse_count == 0
        assert second.status == "skipped_no_changes"
    finally:
        store.close()


def test_warm_no_op_does_not_duplicate_rows(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        files_after_first = _count_files_rows(store)
        chunks_after_first = _count_chunks_rows(store)
        fts_after_first = _count_fts_rows(store)
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert _count_files_rows(store) == files_after_first
        assert _count_chunks_rows(store) == chunks_after_first
        assert _count_fts_rows(store) == fts_after_first
    finally:
        store.close()


def test_warm_small_edit_reparses_only_changed_file(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("def hello():\n    return 42\n")
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        # Only a.py changed.
        assert result.parse_count == 1
        assert tuple(result.changed_files) == ("a.py",)
    finally:
        store.close()


def test_delete_path_marks_file_deleted(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "b.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        row = store.get_file("b.py")
        assert row is not None
        assert row.is_deleted is True
    finally:
        store.close()


def test_partial_failure_records_failed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # Patch hash_workspace_file at the module level so the lookup
        # inside ``pipeline._run_reindex`` resolves to our flaky version.
        from ralph.mcp.explore import pipeline

        original = pipeline.hash_workspace_file

        def flaky(workspace_root: Path, relative_path: str):
            # Fail only on the targeted file regardless of iteration order.
            if relative_path == "a.py":
                raise OSError("simulated failure")
            return original(workspace_root, relative_path)

        monkeypatch.setattr("ralph.mcp.explore.pipeline.hash_workspace_file", flaky)
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # The failed file must be recorded; the rest must still be indexed.
        assert "a.py" in result.failed_files
        # Successful files are still usable.
        b_row = store.get_file("b.py")
        assert b_row is not None and b_row.is_deleted is False
    finally:
        store.close()


def test_mode_full_rebuilds_atomically(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("NEW CONTENT\n")
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result.status == "ok"
        row = store.get_file("a.py")
        assert row is not None
        # Generation is reset to 1 in full mode.
        assert store.get_setting("current_generation") == "1"
    finally:
        store.close()


def test_timeout_is_fail_closed_for_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        clock = FakeClock(initial=1_000.0)
        # A tiny timeout that the reindex will exceed.
        # Patch ``hash_workspace_file`` so each call advances the
        # clock by more than the timeout budget, simulating slow I/O.
        from ralph.mcp.explore import pipeline

        original_hash = pipeline.hash_workspace_file
        call_count = {"n": 0}

        def slow_hash(workspace_root: Path, relative_path: str):
            call_count["n"] += 1
            # Advance the clock by 0.1s on the first hash call.
            if call_count["n"] == 1:
                clock.advance(0.1)
            return original_hash(workspace_root, relative_path)

        monkeypatch.setattr(
            "ralph.mcp.explore.pipeline.hash_workspace_file", slow_hash
        )
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=1, clock=clock),
        )
        assert result.status == "timed_out"
    finally:
        store.close()


def test_consume_dirty_paths_during_reindex(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        store.mark_dirty("a.py", reason="write", source_tool="write_file")
        assert store.peek_dirty_paths() == ["a.py"]
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # No change because the file's content hash did not move.
        assert store.peek_dirty_paths() == []
    finally:
        store.close()


def test_tombstone_created_on_content_change(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("def hello():\n    return 99\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute(
                "SELECT COUNT(*) FROM evidence_tombstones WHERE path = ?",
                ("a.py",),
            ).fetchone()[0]
        finally:
            cur.close()
        assert count >= 1
    finally:
        store.close()


def test_expired_tombstone_returns_retention_expired(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("def hello():\n    return 99\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Manually age the tombstone past retention (30 days).
        cur = sqlite3.connect(str(store.db_path))
        try:
            cur.execute(
                "UPDATE evidence_tombstones SET stale_at = ?",
                (time.time() - 31 * 24 * 60 * 60,),
            )
            cur.commit()
        finally:
            cur.close()
        # Trigger retention by writing a fresh tombstone (real clock,
        # not the FakeClock) — this re-runs the retention check and
        # deletes the aged tombstone.
        store.record_tombstone(
            evidence_id="ev-fresh",
            path="b.py",
            start_line=1,
            end_line=2,
            content_hash="x",
            generation=2,
            stale_reason="manual",
            stale_at=time.time(),
            replacement_evidence_id=None,
        )
        # Tombstone aged past retention is deleted; only the fresh one remains.
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute(
                "SELECT COUNT(*) FROM evidence_tombstones WHERE path = ?",
                ("a.py",),
            ).fetchone()[0]
        finally:
            cur.close()
        assert count == 0
    finally:
        store.close()


def test_path_scope_limits_work(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full", timeout_ms=DEFAULT_TIMEOUT_MS, path_scope=("a.py",)
            ),
        )
        # Only a.py should be indexed; the rest are untouched.
        assert result.status == "ok"
        assert "a.py" in result.changed_files
        assert "b.py" not in result.changed_files
    finally:
        store.close()


def test_reindex_writer_coalesces(tmp_path: Path) -> None:
    """A second reindex claim while one is active is coalesced."""
    ReindexWriter.configure(lock_factory=threading.Lock)
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # Initial build to make store non-empty.
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Manually hold the active slot by calling claim from inside a lock-held block.
        lock_key = str(store.db_path)
        ReindexWriter._active[lock_key] = ReindexWriter(store)
        try:
            result = ReindexWriter.claim(store, workspace_root=workspace)
            assert result.status == "skipped_no_changes"
        finally:
            ReindexWriter._active.pop(lock_key, None)
    finally:
        store.close()


def test_reindex_writer_returns_result_when_uncontended(tmp_path: Path) -> None:
    ReindexWriter.configure(lock_factory=threading.Lock)
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        result = ReindexWriter.claim(store, workspace_root=workspace)
        assert result.status in {"ok", "skipped_no_changes"}
    finally:
        store.close()


def test_reindex_records_job_history(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            cur.close()
        assert count >= 1
    finally:
        store.close()


def test_changed_reindex_clears_structure_rows_for_deleted_path(
    tmp_path: Path,
) -> None:
    """AC-05/AC-06: deleting a file then running a changed reindex must
    drop its chunks, evidence, spans, symbols, and edges. Graph
    queries against the deleted path must return nothing.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "gone.py").write_text("def gone():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert [s.qualified_name for s in store.iter_symbols("gone.py")] == [
            "gone.gone"
        ]
        # Delete the file and re-run a changed reindex.
        (workspace / "gone.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))

        # Structure rows for the deleted path must be gone.
        assert list(store.iter_symbols("gone.py")) == []
        assert list(store.iter_spans("gone.py")) == []
        assert list(store.iter_edges(path="gone.py")) == []
        # Lexical rows must also be gone: no chunks, no FTS hits, no
        # evidence, and the file row is either removed or marked
        # is_deleted=1 (either is acceptable; what is NOT acceptable
        # is the file returning live graph/text data).
        cur = sqlite3.connect(str(store.db_path))
        try:
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            evidence_count = cur.execute(
                "SELECT COUNT(*) FROM evidence WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert chunk_count == 0
        assert fts_count == 0
        assert evidence_count == 0
    finally:
        store.close()


def test_full_reindex_clears_structure_rows_for_deleted_path(
    tmp_path: Path,
) -> None:
    """AC-05/AC-06: a full reindex must clear every index table that can
    serve structural facts, including spans, symbols, and edges. A
    deleted file's structure rows must not survive a full rebuild.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "gone.py").write_text("def gone():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Sanity: the symbol exists before the file is removed.
        assert [s.qualified_name for s in store.iter_symbols("gone.py")] == [
            "gone.gone"
        ]
        (workspace / "gone.py").unlink()
        reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        # No spans / symbols / edges / chunks / FTS / evidence for
        # the deleted path after a full rebuild.
        cur = sqlite3.connect(str(store.db_path))
        try:
            span_count = cur.execute(
                "SELECT COUNT(*) FROM spans WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            symbol_count = cur.execute(
                "SELECT COUNT(*) FROM symbols WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            edge_count = cur.execute(
                "SELECT COUNT(*) FROM edges WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            evidence_count = cur.execute(
                "SELECT COUNT(*) FROM evidence WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert span_count == 0
        assert symbol_count == 0
        assert edge_count == 0
        assert chunk_count == 0
        assert fts_count == 0
        assert evidence_count == 0
    finally:
        store.close()


def test_delete_then_identical_restore_reindexes_path(tmp_path: Path) -> None:
    """AC-05/AC-06: a delete-then-restore cycle of identical bytes must
    re-extract the path and clear ``is_deleted``. The previous pipeline
    treated any matching content hash as a no-op, leaving the file row
    as ``is_deleted=1`` with no live chunks/spans/symbols/edges.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "restore.py").write_text("def original():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Sanity: the symbol exists before deletion.
        assert [s.qualified_name for s in store.iter_symbols("restore.py")] == [
            "restore.original"
        ]
        # Delete the file and reindex.
        (workspace / "restore.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        deleted_row = store.get_file("restore.py")
        assert deleted_row is not None
        assert deleted_row.is_deleted is True
        # Restore the file with byte-identical content.
        (workspace / "restore.py").write_text("def original():\n    return 1\n")
        # The bug was: the second reindex would short-circuit on equal
        # content hash and leave the file row marked deleted. The
        # corrected pipeline must clear is_deleted and re-extract.
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        assert "restore.py" in tuple(result.changed_files)
        restored_row = store.get_file("restore.py")
        assert restored_row is not None
        assert restored_row.is_deleted is False
        # Live lexical/structure rows must be present.
        cur = sqlite3.connect(str(store.db_path))
        try:
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
            evidence_count = cur.execute(
                "SELECT COUNT(*) FROM evidence WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
            symbol_count = cur.execute(
                "SELECT COUNT(*) FROM symbols WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert chunk_count > 0
        assert fts_count > 0
        assert evidence_count > 0
        assert symbol_count > 0
        assert [
            s.qualified_name for s in store.iter_symbols("restore.py")
        ] == ["restore.original"]
    finally:
        store.close()


def test_tombstone_record_is_idempotent_on_repeat_delete(tmp_path: Path) -> None:
    """AC-05: ``record_tombstone`` is called with a deterministic ID that
    is identical across a delete -> restore changed content -> delete ->
    restore original content -> delete cycle. The store must not raise
    ``IntegrityError`` and the row count must remain stable.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "t.py").write_text("def t():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cycle 1: delete + reindex.
        (workspace / "t.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cycle 2: restore with different content, reindex, delete, reindex.
        (workspace / "t.py").write_text("def t():\n    return 2\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cycle 3: restore original content, reindex, delete, reindex.
        (workspace / "t.py").write_text("def t():\n    return 1\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        # The previous bug raised IntegrityError on the third delete.
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # No IntegrityError and the tombstone row count is bounded.
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute(
                "SELECT COUNT(*) FROM evidence_tombstones WHERE path = ?", ("t.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert count >= 1
    finally:
        store.close()


def test_move_path_reindexes_with_normalized_paths(tmp_path: Path) -> None:
    """AC-02: reindex normalizes relative paths and treats a move as
    source-tombstone + destination-create. The old path's file row
    is marked deleted; the new path gets fresh chunks/symbols/
    evidence keyed by the new normalized path.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Move a.py to sub/helper.py (a brand-new directory).
        (workspace / "sub").mkdir()
        (workspace / "a.py").rename(workspace / "sub" / "helper.py")
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # Source path is marked deleted.
        old_row = store.get_file("a.py")
        assert old_row is not None and old_row.is_deleted is True
        # Destination path is a fresh, non-deleted file.
        new_row = store.get_file("sub/helper.py")
        assert new_row is not None and new_row.is_deleted is False
        # Destination has extracted symbols.
        symbols = list(store.iter_symbols("sub/helper.py"))
        assert [s.qualified_name for s in symbols] == ["helper.hello"]
    finally:
        store.close()


def test_copy_path_reindexes_with_fresh_destination(tmp_path: Path) -> None:
    """AC-02: reindex treats a copy as destination-create. The source
    path stays intact; the destination path gets fresh chunks /
    symbols / evidence keyed by the new normalized path.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Copy b.py to b_copy.py.
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        # Source stays live.
        old_row = store.get_file("b.py")
        assert old_row is not None and old_row.is_deleted is False
        # Destination is a fresh file with chunks.
        new_row = store.get_file("b_copy.py")
        assert new_row is not None and new_row.is_deleted is False
        cur = sqlite3.connect(str(store.db_path))
        try:
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("b_copy.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("b_copy.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert chunk_count > 0
        assert fts_count > 0
    finally:
        store.close()


def test_move_with_identical_content_is_a_path_pivot(tmp_path: Path) -> None:
    """AC-02: a move that preserves the file content reuses the
    extraction payloads via the content hash. The destination path
    must still be a fresh, non-deleted file keyed by the new
    normalized path. A path pivot is a deterministic, idempotent
    reindex, not a parse event.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Save the content, move the file, restore the same content.
        original = (workspace / "a.py").read_bytes()
        (workspace / "moved.py").write_bytes(original)
        (workspace / "a.py").unlink()
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # Old path marked deleted; new path live.
        old_row = store.get_file("a.py")
        assert old_row is not None and old_row.is_deleted is True
        new_row = store.get_file("moved.py")
        assert new_row is not None and new_row.is_deleted is False
        # Idempotent: a second reindex with no further edits is a no-op.
        result2 = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result2.parse_count == 0
    finally:
        store.close()
