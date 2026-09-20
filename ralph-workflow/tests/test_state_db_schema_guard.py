"""Regression coverage for RunStateDB's per-file schema version guard."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from ralph.mcp.artifacts import state_db
from ralph.mcp.artifacts.state_db import DB_RELPATH, MISSING, RunStateDB

if TYPE_CHECKING:
    from pathlib import Path


def _user_version(db: RunStateDB) -> int:
    row = db._conn.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def test_state_db_regression_schema_guard_creates_and_versions_fresh_database(
    tmp_path: Path,
) -> None:
    """S-11: a fresh database receives the schema and its version marker."""
    db = RunStateDB(tmp_path)
    try:
        assert _user_version(db) == state_db._SCHEMA_VERSION
        assert db.get_receipt_hmac("run-1", "plan") is MISSING
    finally:
        db.close()


def test_state_db_regression_schema_guard_preserves_existing_data_on_reopen(
    tmp_path: Path,
) -> None:
    """S-11: a versioned database reopens without needing schema recreation."""
    writer = RunStateDB(tmp_path)
    writer.upsert_receipt("run-1", "plan", "signature")
    writer.close()

    reader = RunStateDB(tmp_path)
    try:
        assert _user_version(reader) == state_db._SCHEMA_VERSION
        assert reader.get_receipt_hmac("run-1", "plan") == "signature"
    finally:
        reader.close()


def test_state_db_migrates_v1_receipt_database_with_normalization_audit(tmp_path: Path) -> None:
    """A v1 receipt database gains the audit column without losing its HMAC."""
    db_path = tmp_path / DB_RELPATH
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(str(db_path))
    try:
        connection.executescript(
            """
            CREATE TABLE receipts (
                run_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                hmac TEXT,
                created_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (run_id, artifact_type)
            );
            INSERT INTO receipts (run_id, artifact_type, hmac) VALUES ('run-1', 'plan', 'signature');
            PRAGMA user_version = 1;
            """
        )
        connection.commit()
    finally:
        connection.close()

    migrated = RunStateDB(tmp_path)
    try:
        assert _user_version(migrated) == state_db._SCHEMA_VERSION
        assert migrated.get_receipt_hmac("run-1", "plan") == "signature"
        assert migrated.get_receipt_normalization_audit("run-1", "plan") is None
        migrated.upsert_receipt("run-1", "plan", "signature", '{"confidence":"high"}')
        assert migrated.get_receipt_normalization_audit("run-1", "plan") == '{"confidence":"high"}'
    finally:
        migrated.close()


def test_state_db_regression_schema_guard_recreates_deleted_database(tmp_path: Path) -> None:
    """S-11: deleting the file resets its version and triggers create-on-open."""
    initial = RunStateDB(tmp_path)
    initial.close()
    db_path = tmp_path / DB_RELPATH
    db_path.unlink()

    recreated = RunStateDB(tmp_path)
    try:
        assert _user_version(recreated) == state_db._SCHEMA_VERSION
        assert recreated.get_receipt_hmac("run-1", "plan") is MISSING
    finally:
        recreated.close()

    connection = sqlite3.connect(str(db_path))
    try:
        assert connection.execute("SELECT count(*) FROM receipts").fetchone() == (0,)
    finally:
        connection.close()
