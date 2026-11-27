from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Final, cast

from ralph.checked_accessors import as_object_list, as_sequence
from ralph.mcp.artifacts._schema_version_type_error import SchemaVersionTypeError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


MISSING: Final = _Missing()

CLEARED_SENTINEL_HMAC: Final[str] = "__ralph_internal_cleared__"

_SCHEMA_VERSION: Final[int] = 4
_INITIAL_SCHEMA_VERSION: Final[int] = 1
_SUBMISSION_SNAPSHOT_COLUMN_COUNT: Final[int] = 3
_LATEST_SUBMISSION_SNAPSHOT_COLUMN_COUNT: Final[int] = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    run_id        TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    hmac          TEXT,
    normalization_audit TEXT,
    submission_snapshot_path TEXT,
    submission_snapshot_digest TEXT,
    submission_snapshot_hmac TEXT,
    created_at    REAL NOT NULL DEFAULT (unixepoch('subsec')),
    PRIMARY KEY (run_id, artifact_type)
);
CREATE TABLE IF NOT EXISTS completion_sentinels (
    run_id     TEXT PRIMARY KEY,
    hmac       TEXT,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""

DB_RELPATH = ".agent/state.db"


def _coerce_hmac(row: object) -> str | None:
    if not isinstance(row, tuple):
        return None
    first: object = row[0] if len(row) > 0 else None
    if isinstance(first, str):
        return first
    return None


_THREAD_CONNECTIONS = threading.local()
_MAX_CONNECTIONS_PER_THREAD = 8


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino


def _thread_connection(db_path: Path) -> tuple[sqlite3.Connection, bool]:
    cache: OrderedDict[str, tuple[sqlite3.Connection, tuple[int, int] | None, int]] | None = (
        getattr(_THREAD_CONNECTIONS, "cache", None)
    )
    if cache is None:
        cache = OrderedDict()
        _THREAD_CONNECTIONS.cache = cache
    key = str(db_path)
    cached = cache.pop(key, None)
    if cached is not None:
        connection, identity, owner_pid = cached
        if (
            owner_pid == os.getpid()
            and identity is not None
            and identity == _file_identity(db_path)
        ):
            cache[key] = cached
            return connection, False
        with contextlib.suppress(sqlite3.Error):
            connection.close()
    connection = sqlite3.connect(key, timeout=5.0)
    cache[key] = (connection, _file_identity(db_path), os.getpid())
    while len(cache) > _MAX_CONNECTIONS_PER_THREAD:
        _evicted_key, (evicted, _identity, _pid) = cache.popitem(last=False)
        with contextlib.suppress(sqlite3.Error):
            evicted.close()
    return connection, True


class RunStateDB:
    __slots__ = ("_closed", "_conn")

    def __init__(self, workspace_root: Path) -> None:
        db_path = workspace_root / DB_RELPATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._closed: bool = False
        self._conn, fresh = _thread_connection(db_path)
        if not fresh:
            return
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        version_row: object = self._conn.execute("PRAGMA user_version").fetchone()
        if not isinstance(version_row, tuple) or not version_row:
            raise RuntimeError("SQLite PRAGMA user_version returned no version")
        version_value: object = version_row[0]
        if not isinstance(version_value, int):
            raise SchemaVersionTypeError(
                "SQLite PRAGMA user_version returned a non-integer version"
            )
        if version_value < _INITIAL_SCHEMA_VERSION:
            self._conn.executescript(_SCHEMA)
        if version_value < _SCHEMA_VERSION:
            rows = as_object_list(
                cast("object", self._conn.execute("PRAGMA table_info(receipts)").fetchall())
            )

            columns: set[str] = set()
            for row in rows:
                if not isinstance(row, tuple) or len(row) <= 1:
                    continue
                name = as_sequence(row)[1]
                if isinstance(name, str):
                    columns.add(name)
            if "normalization_audit" not in columns:
                self._conn.execute("ALTER TABLE receipts ADD COLUMN normalization_audit TEXT")
            if "submission_snapshot_path" not in columns:
                self._conn.execute("ALTER TABLE receipts ADD COLUMN submission_snapshot_path TEXT")
            if "submission_snapshot_digest" not in columns:
                self._conn.execute(
                    "ALTER TABLE receipts ADD COLUMN submission_snapshot_digest TEXT"
                )
            if "submission_snapshot_hmac" not in columns:
                self._conn.execute("ALTER TABLE receipts ADD COLUMN submission_snapshot_hmac TEXT")
        if version_value < _SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()

    @classmethod
    def _raise_upsert_fail(cls, run_id: str, artifact_type: str, hmac_hex: str | None) -> None:
        raise RuntimeError("simulated db upsert failure")

    def upsert_receipt(
        self,
        run_id: str,
        artifact_type: str,
        hmac_hex: str | None,
        normalization_audit: str | None = None,
        submission_snapshot_path: str | None = None,
        submission_snapshot_digest: str | None = None,
        submission_snapshot_hmac: str | None = None,
    ) -> None:
        with self._conn:
            params: tuple[str | None, ...] = (
                run_id,
                artifact_type,
                hmac_hex,
                normalization_audit,
                submission_snapshot_path,
                submission_snapshot_digest,
                submission_snapshot_hmac,
            )
            self._conn.execute(
                "INSERT INTO receipts (run_id, artifact_type, hmac, normalization_audit, "
                "submission_snapshot_path, submission_snapshot_digest, submission_snapshot_hmac) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, artifact_type) DO UPDATE SET "
                "hmac=excluded.hmac, normalization_audit=excluded.normalization_audit, "
                "submission_snapshot_path=excluded.submission_snapshot_path, "
                "submission_snapshot_digest=excluded.submission_snapshot_digest, "
                "submission_snapshot_hmac=excluded.submission_snapshot_hmac, "
                "created_at=unixepoch('subsec')",
                params,
            )

    def get_receipt_normalization_audit(
        self, run_id: str, artifact_type: str
    ) -> str | None | _Missing:
        cursor = self._conn.execute(
            "SELECT normalization_audit FROM receipts WHERE run_id = ? AND artifact_type = ?",
            (run_id, artifact_type),
        )
        try:
            row: object = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return MISSING
        return _coerce_hmac(row)

    def get_receipt_hmac(self, run_id: str, artifact_type: str) -> str | None | _Missing:
        cursor = self._conn.execute(
            "SELECT hmac FROM receipts WHERE run_id = ? AND artifact_type = ?",
            (run_id, artifact_type),
        )
        try:
            row: object = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return MISSING
        return _coerce_hmac(row)

    def get_receipt_submission_snapshot(
        self, run_id: str, artifact_type: str
    ) -> tuple[str | None, str | None, str | None] | _Missing:
        cursor = self._conn.execute(
            "SELECT submission_snapshot_path, submission_snapshot_digest, "
            "submission_snapshot_hmac FROM receipts "
            "WHERE run_id = ? AND artifact_type = ?",
            (run_id, artifact_type),
        )
        try:
            row: object = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return MISSING
        if not isinstance(row, tuple) or len(row) != _SUBMISSION_SNAPSHOT_COLUMN_COUNT:
            return None, None, None
        path = row[0] if isinstance(row[0], str) else None
        digest = row[1] if isinstance(row[1], str) else None
        snapshot_hmac = row[2] if isinstance(row[2], str) else None
        return path, digest, snapshot_hmac

    def get_latest_receipt_submission_snapshot(
        self, artifact_type: str
    ) -> tuple[str, str | None, str | None, str | None, str | None] | _Missing:
        row: object = self._conn.execute(
            "SELECT run_id, hmac, submission_snapshot_path, submission_snapshot_digest, "
            "submission_snapshot_hmac FROM receipts WHERE artifact_type = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (artifact_type,),
        ).fetchone()
        if (
            not isinstance(row, tuple)
            or len(row) != _LATEST_SUBMISSION_SNAPSHOT_COLUMN_COUNT
            or not isinstance(row[0], str)
        ):
            return MISSING
        return (
            row[0],
            row[1] if isinstance(row[1], str) else None,
            row[2] if isinstance(row[2], str) else None,
            row[3] if isinstance(row[3], str) else None,
            row[4] if isinstance(row[4], str) else None,
        )

    def delete_receipt(self, run_id: str, artifact_type: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM receipts WHERE run_id = ? AND artifact_type = ?",
                (run_id, artifact_type),
            )

    def clear_run_receipts(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM receipts WHERE run_id = ?", (run_id,))

    def upsert_completion_sentinel(self, run_id: str, hmac_hex: str | None) -> None:
        with self._conn:
            params: tuple[str | None, ...] = (run_id, hmac_hex)
            self._conn.execute(
                "INSERT INTO completion_sentinels (run_id, hmac) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "hmac=excluded.hmac, created_at=unixepoch('subsec')",
                params,
            )

    def get_completion_sentinel_hmac(self, run_id: str) -> str | None | _Missing:
        cursor = self._conn.execute(
            "SELECT hmac FROM completion_sentinels WHERE run_id = ?",
            (run_id,),
        )
        try:
            row: object = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return MISSING
        return _coerce_hmac(row)

    def delete_completion_sentinel(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM completion_sentinels WHERE run_id = ?", (run_id,))

    def mark_completion_sentinel_cleared(self, run_id: str) -> None:
        with self._conn:
            params: tuple[str, ...] = (run_id, CLEARED_SENTINEL_HMAC)
            self._conn.execute(
                "INSERT INTO completion_sentinels (run_id, hmac) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET hmac=excluded.hmac",
                params,
            )

    def prune_older_than(
        self,
        cutoff: float,
        *,
        keep_run_id: str | None = None,
        keep_run_ids: Iterable[str] = (),
    ) -> int:
        keep = set(keep_run_ids)
        if keep_run_id is not None:
            keep.add(keep_run_id)
        receipt_sql = "DELETE FROM receipts WHERE created_at < ?"
        sentinel_sql = "DELETE FROM completion_sentinels WHERE created_at < ?"
        params: tuple[float | str, ...] = (cutoff,)
        if keep:
            placeholders = ", ".join("?" for _ in keep)
            receipt_sql += f" AND run_id NOT IN ({placeholders})"
            sentinel_sql += f" AND run_id NOT IN ({placeholders})"
            params = (cutoff, *sorted(keep))
        with self._conn:
            receipt_rows = self._conn.execute(receipt_sql, params).rowcount
            sentinel_rows = self._conn.execute(sentinel_sql, params).rowcount
        return int(receipt_rows) + int(sentinel_rows)

    def close(self) -> None:
        self._closed = True


__all__ = [
    "CLEARED_SENTINEL_HMAC",
    "DB_RELPATH",
    "MISSING",
    "RunStateDB",
    "SchemaVersionTypeError",
    "_Missing",
]
