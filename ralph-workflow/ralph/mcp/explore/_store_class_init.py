from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from ralph.mcp.explore._store_types import (
    _DDL,
    _SCHEMA_MIGRATIONS,
    SCHEMA_VERSION,
    _column_exists,
    _is_add_column,
    _parse_add_column,
)


class _InitializeMethods:
    _conn: sqlite3.Connection
    _db_path: Path
    _busy_timeout_ms: int
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    _INIT_LOCK_ATTEMPTS: int = 3
    _INIT_LOCK_BACKOFF_SECONDS: float = 0.25

    def _initialize(self) -> None:
        attempts = 0
        backoff = self._INIT_LOCK_BACKOFF_SECONDS
        while True:
            try:
                self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                if self._schema_version_matches():
                    return
                self._initialize_with_bounded_retry()
                return
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg and "disk i/o error" not in msg:
                    raise
                attempts += 1
                if attempts >= self._INIT_LOCK_ATTEMPTS:
                    raise
                if "disk i/o error" in msg:
                    self._conn.close()
                    self._conn = sqlite3.connect(
                        str(self._db_path),
                        timeout=self._busy_timeout_ms / 1000.0,
                        isolation_level=None,
                        check_same_thread=False,
                    )
                    self._conn.row_factory = sqlite3.Row
                time.sleep(backoff)
                backoff *= 2

    def _migrate_schema(self) -> None:
        cur = self._conn.execute("SELECT value FROM settings WHERE key = 'schema_version'")
        row_obj: sqlite3.Row | None = cur.fetchone()
        on_disk: str = ""
        if row_obj is not None:
            cell_obj: object = row_obj["value"]
            on_disk = cell_obj if isinstance(cell_obj, str) else ""
        if on_disk == SCHEMA_VERSION:
            return
        known_migration_versions = {m[0] for m in _SCHEMA_MIGRATIONS}
        if on_disk and on_disk not in known_migration_versions:
            raise RuntimeError(
                f"explore schema version {on_disk!r} is newer than "
                f"supported {SCHEMA_VERSION!r}; rebuild required"
            )
        for version, migration_sql in _SCHEMA_MIGRATIONS:
            if on_disk and on_disk >= version:
                continue
            with self._transaction() as cur:
                if _is_add_column(migration_sql):
                    table, column = _parse_add_column(migration_sql)
                    if _column_exists(cur, table, column):
                        continue
                cur.execute(migration_sql)
        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES "
                "('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )

    def _schema_version_matches(self) -> bool:
        try:
            cur = self._conn.execute("SELECT value FROM settings WHERE key = 'schema_version'")
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return False
        row_obj: sqlite3.Row | None = cur.fetchone()
        if row_obj is None:
            return False
        cell_obj: object = row_obj["value"]
        on_disk = cell_obj if isinstance(cell_obj, str) else ""
        if on_disk == SCHEMA_VERSION:
            return True
        if on_disk and on_disk not in {m[0] for m in _SCHEMA_MIGRATIONS}:
            raise RuntimeError(
                f"explore schema version {on_disk!r} is newer than "
                f"supported {SCHEMA_VERSION!r}; rebuild required"
            )
        return False

    def _initialize_with_bounded_retry(self) -> None:
        attempts = 0
        backoff = self._INIT_LOCK_BACKOFF_SECONDS
        while True:
            try:
                if self._schema_version_matches():
                    return
                with self._transaction() as cur:
                    for stmt in _DDL:
                        cur.execute(stmt)
                self._migrate_schema()
                return
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                attempts += 1
                if attempts >= self._INIT_LOCK_ATTEMPTS:
                    raise
                time.sleep(backoff)
                backoff *= 2
