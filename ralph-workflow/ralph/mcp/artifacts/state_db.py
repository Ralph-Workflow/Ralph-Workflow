from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


MISSING: Final = _Missing()

CLEARED_SENTINEL_HMAC: Final[str] = "__ralph_internal_cleared__"

_SCHEMA_VERSION: Final[int] = 2
_INITIAL_SCHEMA_VERSION: Final[int] = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    run_id        TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    hmac          TEXT,
    normalization_audit TEXT,
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


class RunStateDB:
    def __init__(self, workspace_root: Path) -> None:
        db_path = workspace_root / DB_RELPATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=5.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        version_row: object = self._conn.execute("PRAGMA user_version").fetchone()
        if not isinstance(version_row, tuple) or not version_row:
            raise RuntimeError("SQLite PRAGMA user_version returned no version")
        version_value: object = version_row[0]
        if not isinstance(version_value, int):
            raise RuntimeError("SQLite PRAGMA user_version returned a non-integer version")
        if version_value < _INITIAL_SCHEMA_VERSION:
            self._conn.executescript(_SCHEMA)
        if version_value < _SCHEMA_VERSION:
            rows = cast(
                "list[object]", self._conn.execute("PRAGMA table_info(receipts)").fetchall()
            )
            columns = {
                row[1]
                for row in rows
                if isinstance(row, tuple) and len(row) > 1 and isinstance(row[1], str)
            }
            if "normalization_audit" not in columns:
                self._conn.execute("ALTER TABLE receipts ADD COLUMN normalization_audit TEXT")
        if version_value < _SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            self._conn.commit()
        self._closed: bool = False

    @classmethod
    def _raise_upsert_fail(cls, run_id: str, artifact_type: str, hmac_hex: str | None) -> None:
        raise RuntimeError("simulated db upsert failure")

    def upsert_receipt(
        self,
        run_id: str,
        artifact_type: str,
        hmac_hex: str | None,
        normalization_audit: str | None = None,
    ) -> None:
        with self._conn:
            params: tuple[str | None, ...] = (
                run_id,
                artifact_type,
                hmac_hex,
                normalization_audit,
            )
            self._conn.execute(
                "INSERT INTO receipts (run_id, artifact_type, hmac, normalization_audit) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(run_id, artifact_type) DO UPDATE SET "
                "hmac=excluded.hmac, normalization_audit=excluded.normalization_audit, "
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
        self._conn.close()


__all__ = [
    "CLEARED_SENTINEL_HMAC",
    "DB_RELPATH",
    "MISSING",
    "RunStateDB",
    "_Missing",
]
