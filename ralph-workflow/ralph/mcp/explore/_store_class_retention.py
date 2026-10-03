"""Retention and storage method mixin for :class:`ExploreStore`.

Extracted from :mod:`ralph.mcp.explore._store_class` so the host
module stays under the repository file-size ceiling.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from ralph.mcp.explore._store_types import (
    JOB_HISTORY_CAP,
    JOB_HISTORY_RETENTION_SECONDS,
    TOMBSTONE_CAP,
    TOMBSTONE_RETENTION_SECONDS,
    _row_int_opt,
)


class _RetentionMethods:
    """Mixin supplying job history, tombstones, and storage size methods."""

    _conn: sqlite3.Connection
    _db_path: Path
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    # --- Job history (bounded) ----------------------------------------

    def record_job(
        self,
        *,
        job_id: str,
        generation: int,
        status: str,
        started_at: float,
        finished_at: float | None,
        files_seen: int,
        files_changed: int,
        files_failed: int,
        error_summary: str | None,
        now: float | None = None,
    ) -> None:
        now_seconds = time.time() if now is None else now
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    job_id, generation, status, started_at, finished_at,
                    files_seen, files_changed, files_failed, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    generation,
                    status,
                    started_at,
                    finished_at,
                    files_seen,
                    files_changed,
                    files_failed,
                    error_summary,
                ),
            )
            # Batch cap pruning: deleting on every insert after the cap turns
            # a bounded history into repeated full-table scans. Keep at most a
            # ten-record slack, then prune back to the canonical cap.
            count_row: sqlite3.Row | None = cur.execute("SELECT COUNT(*) FROM jobs").fetchone()
            job_count = _row_int_opt(count_row, 0) if count_row is not None else 0
            if job_count > JOB_HISTORY_CAP + 10:
                cur.execute(
                    """
                    DELETE FROM jobs WHERE job_id IN (
                        SELECT job_id FROM jobs
                        ORDER BY started_at DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (JOB_HISTORY_CAP,),
                )
            cur.execute(
                "DELETE FROM jobs WHERE started_at < ?",
                (now_seconds - JOB_HISTORY_RETENTION_SECONDS,),
            )

    def latest_job(self) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM jobs ORDER BY started_at DESC LIMIT 1")
        row: sqlite3.Row | None = cur.fetchone()
        return row

    # --- Evidence tombstones (bounded) -------------------------------

    def record_tombstone(
        self,
        *,
        evidence_id: str,
        path: str,
        start_line: int,
        end_line: int,
        content_hash: str,
        generation: int,
        stale_reason: str,
        stale_at: float,
        replacement_evidence_id: str | None,
        now: float | None = None,
    ) -> None:
        now_seconds = time.time() if now is None else now
        with self._transaction() as cur:
            # AC-05: tombstone identity is derived deterministically
            # from (path, content_hash, kind), so a delete-then-restore
            # cycle of the same bytes can produce the same evidence_id
            # on the next delete. The lifecycle must remain idempotent
            # to avoid ``IntegrityError`` on the primary-key collision.
            # An ON CONFLICT refreshes ``stale_at``/``stale_reason``/
            # ``replacement_evidence_id`` on the existing row so the
            # row count does not balloon and the most recent deletion
            # wins for lookup, while bounded retention still applies.
            cur.execute(
                """
                INSERT INTO evidence_tombstones (
                    evidence_id, path, start_line, end_line, content_hash,
                    generation, stale_reason, stale_at, replacement_evidence_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    stale_at=excluded.stale_at,
                    stale_reason=excluded.stale_reason,
                    replacement_evidence_id=excluded.replacement_evidence_id,
                    generation=excluded.generation
                """,
                (
                    evidence_id,
                    path,
                    start_line,
                    end_line,
                    content_hash,
                    generation,
                    stale_reason,
                    stale_at,
                    replacement_evidence_id,
                ),
            )
            cur.execute(
                """
                DELETE FROM evidence_tombstones WHERE evidence_id IN (
                    SELECT evidence_id FROM evidence_tombstones
                    ORDER BY stale_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (TOMBSTONE_CAP,),
            )
            cur.execute(
                "DELETE FROM evidence_tombstones WHERE stale_at < ?",
                (now_seconds - TOMBSTONE_RETENTION_SECONDS,),
            )

    def get_tombstone(self, evidence_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            """
            SELECT * FROM evidence_tombstones WHERE evidence_id = ?
            """,
            (evidence_id,),
        )
        row: sqlite3.Row | None = cur.fetchone()
        return row

    # --- Storage size -------------------------------------------------

    def index_storage_bytes(self) -> int:
        total = 0
        if self._db_path.exists():
            total += self._db_path.stat().st_size
        wal = self._db_path.with_suffix(".sqlite-wal")
        if wal.exists():
            total += wal.stat().st_size
        shm = self._db_path.with_suffix(".sqlite-shm")
        if shm.exists():
            total += shm.stat().st_size
        return total
