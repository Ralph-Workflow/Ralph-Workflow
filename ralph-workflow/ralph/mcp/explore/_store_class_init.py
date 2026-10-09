"""S-4 initialization helpers for :class:`ExploreStore`.

Extracted from :mod:`ralph.mcp.explore._store_class` so the hub
module stays under the repository file-size limit. The helpers
implement the read-first fast path and bounded DDL retry that
fix the ``database is locked`` flake from concurrent
``ExploreStore.__init__`` calls. They are mixed into
:class:`ExploreStore` via ``_InitializeMethods``.
"""

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
    """Mixin supplying :meth:`ExploreStore._initialize` and its helpers.

    The mixin depends on the host class providing ``self._conn``
    (a :class:`sqlite3.Connection`) and ``self._transaction``
    (a context-manager factory) plus ``self._migrate_schema``.
    """

    # Ponytail: declare the private members the mixin depends on as
    # untyped class-level annotations so mypy does not flag the
    # cross-class access. The runtime contract is documented in the
    # class docstring; a regression that breaks the contract would
    # raise ``AttributeError`` at the first call.
    _conn: sqlite3.Connection
    _db_path: Path
    _busy_timeout_ms: int
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    # Ponytail: bounded retry budget for the DDL transaction;
    # 3 attempts with 0.25s -> 0.5s -> 1.0s backoff (2.25s ceiling)
    # keeps the constructor fail-fast while absorbing transient
    # ``database is locked`` from concurrent first-creation.
    _INIT_LOCK_ATTEMPTS: int = 3
    _INIT_LOCK_BACKOFF_SECONDS: float = 0.25

    def _initialize(self) -> None:
        """Apply DDL + pragmas. Idempotent across reloads.

        AC-01: the on-disk ``settings.schema_version`` is compared to
        :data:`SCHEMA_VERSION`; a missing row or an older version
        triggers ``ALTER TABLE`` migrations to bring the database up
        to the current schema, or in the worst case a safe cold
        rebuild when an additive migration is not possible. The
        versions are pinned in ``_SCHEMA_MIGRATIONS`` so a future
        upgrade only needs to append one entry.

        Concurrency seam (S-4): the write transaction used to be
        taken unconditionally on every construction -- even by
        read-only sessions opening an existing current database --
        which forced every concurrent caller to serialize on the
        SQLite write lock and could exceed ``busy_timeout`` under
        loaded-suite contention (``database is locked``). The
        read-first fast path below checks ``settings.schema_version``
        via a plain read query (no lock); when the database is at
        the current version the function returns without ever
        taking a write transaction. The concurrent-creation case
        (empty directory) still runs the DDL, but with a bounded
        retry so transient lock contention from sibling
        constructors does not raise.
        """
        # Ponytail: pragmas (journal_mode, synchronous, busy_timeout,
        # foreign_keys) must be set OUTSIDE of an explicit transaction
        # because SQLite rejects ``PRAGMA synchronous`` (and friends)
        # inside a transaction.
        # Under concurrent construction against a fresh index directory,
        # sibling processes can contend on the write lock during
        # PRAGMA journal_mode=WAL or schema inspection before DDL
        # completes. Setting busy_timeout first and wrapping the
        # initialization in bounded retry prevents spurious failures.
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
                time.sleep(  # filesystem-poll-ok: bounded backoff between DDL retry attempts; total ceiling is 0.25+0.5+1.0=1.75s, not a poll loop.
                    backoff
                )
                backoff *= 2

    def _migrate_schema(self) -> None:
        """Bring an existing SQLite file up to ``SCHEMA_VERSION``.

        Reads ``settings.schema_version``; if the value is missing or
        older than :data:`SCHEMA_VERSION`, the additive migration
        statements in :data:`_SCHEMA_MIGRATIONS` are executed. Each
        migration is itself idempotent (``ALTER TABLE ... ADD COLUMN``
        guarded by a schema introspection) so a re-open of a fully
        migrated database is a no-op. The recorded version is then
        pinned to ``SCHEMA_VERSION`` so the next open is also a no-op.
        """
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
            # Future / newer-than-known schema: refuse rather than
            # silently serve incompatible rows.
            raise RuntimeError(
                f"explore schema version {on_disk!r} is newer than "
                f"supported {SCHEMA_VERSION!r}; rebuild required"
            )
        for version, migration_sql in _SCHEMA_MIGRATIONS:
            if on_disk and on_disk >= version:
                continue
            with self._transaction() as cur:
                # Idempotency guard: ``ALTER TABLE ... ADD COLUMN`` is
                # not idempotent on SQLite and ``CREATE ... IF NOT
                # EXISTS`` already handles itself. We only run an
                # ADD COLUMN when the column is genuinely missing.
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
        """Return True when ``settings.schema_version`` is at ``SCHEMA_VERSION``.

        This is a plain SELECT (no transaction, no DDL); it never
        touches the write lock so concurrent sessions do not
        serialize on it. The helper raises the same future-schema
        error as ``_migrate_schema`` when the on-disk version is
        newer than the supported one, so a stale binary against a
        newer database still surfaces a hard error instead of
        silently serving mismatched rows.

        Returns False when the ``settings`` table itself does not
        exist (the database is brand-new and needs the full DDL
        pass) or when the version row is missing / older.
        """
        # The ``settings`` table is part of the DDL; on a brand-new
        # index file the SELECT raises ``no such table`` and we
        # fall through to the DDL transaction. Treat that case as
        # "schema does not match" so the caller runs the DDL.
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
        """Run the DDL + migration transaction with a bounded retry.

        Concurrent constructors contend on the SQLite write lock
        while running the DDL; under loaded-suite CPU contention a
        transient ``database is locked`` can be raised before
        ``busy_timeout`` expires. The bounded retry catches the
        transient case and retries up to ``_INIT_LOCK_ATTEMPTS``
        times with a small exponential backoff before propagating
        the error. ``_INIT_LOCK_ATTEMPTS`` and
        ``_INIT_LOCK_BACKOFF_SECONDS`` are intentionally tiny so
        a real lock storm still surfaces promptly.
        """
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
                time.sleep(  # filesystem-poll-ok: bounded backoff between DDL retry attempts; total ceiling is 0.25+0.5+1.0=1.75s, not a poll loop.
                    backoff
                )
                backoff *= 2
