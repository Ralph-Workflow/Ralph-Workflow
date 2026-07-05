"""Tests for the per-workspace SQLite bookkeeping store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.state_db import MISSING, RunStateDB

if TYPE_CHECKING:
    from pathlib import Path


def test_receipt_roundtrip(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    assert db.get_receipt_hmac("run-1", "plan") is MISSING
    db.upsert_receipt("run-1", "plan", "abc123")
    assert db.get_receipt_hmac("run-1", "plan") == "abc123"
    db.delete_receipt("run-1", "plan")
    assert db.get_receipt_hmac("run-1", "plan") is MISSING
    db.close()


def test_receipt_null_hmac_distinct_from_missing(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_receipt("run-1", "plan", None)
    assert db.get_receipt_hmac("run-1", "plan") is None
    db.close()


def test_clear_run_receipts_scoped_to_run(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_receipt("run-1", "plan", "a")
    db.upsert_receipt("run-1", "issues", "b")
    db.upsert_receipt("run-2", "plan", "c")
    db.clear_run_receipts("run-1")
    assert db.get_receipt_hmac("run-1", "plan") is MISSING
    assert db.get_receipt_hmac("run-1", "issues") is MISSING
    assert db.get_receipt_hmac("run-2", "plan") == "c"
    db.close()


def test_completion_sentinel_roundtrip(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    assert db.get_completion_sentinel_hmac("run-1") is MISSING
    db.upsert_completion_sentinel("run-1", "sig")
    assert db.get_completion_sentinel_hmac("run-1") == "sig"
    db.delete_completion_sentinel("run-1")
    assert db.get_completion_sentinel_hmac("run-1") is MISSING
    db.close()


def test_cross_connection_visibility(tmp_path: Path) -> None:
    """Simulates MCP-server-writes / engine-reads across processes."""
    writer = RunStateDB(tmp_path)
    reader = RunStateDB(tmp_path)
    writer.upsert_receipt("run-1", "plan", "sig")
    assert reader.get_receipt_hmac("run-1", "plan") == "sig"
    writer.close()
    reader.close()


# RFC-013 P3 storage-mode contract: the per-workspace ``.agent/state.db``
# MUST enable WAL journaling and ``synchronous=NORMAL``. A regression
# to default SQLite settings would pass the CRUD tests above but
# silently reintroduce the per-commit fsync that the RFC eliminated.
def test_pragmas_journal_mode_wal_synchronous_normal(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    try:
        journal_mode_row = db._conn.execute("PRAGMA journal_mode").fetchone()
        synchronous_row = db._conn.execute("PRAGMA synchronous").fetchone()
    finally:
        db.close()
    assert journal_mode_row is not None and journal_mode_row[0].lower() == "wal"
    assert synchronous_row is not None and synchronous_row[0] == 1
