"""Per-workspace SQLite store for machine-only run bookkeeping.

Replaces one-file-per-event bookkeeping under ``.agent/`` (receipts,
completion sentinels) with a single WAL-mode database at
``.agent/state.db``. Motivation: on long multi-instance runs the
per-event file creates were a measurable share of the macOS fseventsd
event storm, and the files accumulated without bound.

Scope rule: ONLY machine-only state belongs here. Anything an agent or
a human reads through workspace file tools (PLAN.md, prompts, artifact
JSON, exec spills) stays a plain file.

Concurrency: the MCP server process writes while the engine process
reads. WAL mode plus a busy timeout covers that on a local filesystem.
Every public method opens no extra connections; one connection per
RunStateDB instance, serialized by SQLite itself.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path


class _Missing:
    """Sentinel type distinguishing 'row absent' from 'hmac is NULL'."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<MISSING>"


MISSING: Final = _Missing()

#: Tombstone marker written to ``completion_sentinels.hmac`` when a
#: ``delete_completion_sentinel`` call raises ``sqlite3.Error`` so the
#: downstream reader (``_db_sentinel_lookup``) honours the cleared
#: state even though the row could not be physically removed. A model
#: with workspace write access cannot forge a sentinel with this exact
#: marker because the read path treats it as "not completed" and the
#: HMAC secret is owned by the broker, not the agent.
CLEARED_SENTINEL_HMAC: Final[str] = "__ralph_internal_cleared__"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    run_id        TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    hmac          TEXT,
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
    """Extract a stored ``hmac`` value from a sqlite row tuple.

    SQLite returns the column as ``str | None`` for our schema; the
    cast here lets mypy treat the return value as the narrowed type
    without any ``Any`` leakage.
    """
    if not isinstance(row, tuple):
        return None
    first: object = row[0] if len(row) > 0 else None
    if isinstance(first, str):
        return first
    return None


class RunStateDB:
    """Handle to the workspace bookkeeping database (create-on-open)."""

    def __init__(self, workspace_root: Path) -> None:
        db_path = workspace_root / DB_RELPATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=5.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._closed: bool = False

    @classmethod
    def _raise_upsert_fail(cls, run_id: str, artifact_type: str, hmac_hex: str | None) -> None:
        """Test seam: raise to simulate DB write failure during atomic rollback tests."""
        raise RuntimeError("simulated db upsert failure")

    # -- receipts ---------------------------------------------------------

    def upsert_receipt(self, run_id: str, artifact_type: str, hmac_hex: str | None) -> None:
        with self._conn:
            params: tuple[str | None, ...] = (run_id, artifact_type, hmac_hex)
            self._conn.execute(
                "INSERT INTO receipts (run_id, artifact_type, hmac) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, artifact_type) DO UPDATE SET hmac=excluded.hmac",
                params,
            )

    def get_receipt_hmac(self, run_id: str, artifact_type: str) -> str | None | _Missing:
        cursor = self._conn.execute(
            "SELECT hmac FROM receipts WHERE run_id = ? AND artifact_type = ?",
            (run_id, artifact_type),
        )
        row: object = cursor.fetchone()
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

    # -- completion sentinels ---------------------------------------------

    def upsert_completion_sentinel(self, run_id: str, hmac_hex: str | None) -> None:
        with self._conn:
            params: tuple[str | None, ...] = (run_id, hmac_hex)
            self._conn.execute(
                "INSERT INTO completion_sentinels (run_id, hmac) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET hmac=excluded.hmac",
                params,
            )

    def get_completion_sentinel_hmac(self, run_id: str) -> str | None | _Missing:
        cursor = self._conn.execute(
            "SELECT hmac FROM completion_sentinels WHERE run_id = ?",
            (run_id,),
        )
        row: object = cursor.fetchone()
        if row is None:
            return MISSING
        return _coerce_hmac(row)

    def delete_completion_sentinel(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM completion_sentinels WHERE run_id = ?", (run_id,)
            )

    def mark_completion_sentinel_cleared(self, run_id: str) -> None:
        """Write a tombstone marker so the reader honours the cleared state.

        Used as the durable-fallback when ``delete_completion_sentinel``
        raises ``sqlite3.Error`` (locked / corrupt / unsupported WAL):
        physically removing the row is best-effort, but the read path
        must observe the cleared state so a reused ``run_id`` cannot
        inherit a previous run's "completed" verdict.

        ``_db_sentinel_lookup`` recognises ``CLEARED_SENTINEL_HMAC`` and
        returns ``(False, None)`` so ``_check_completion_sentinel``
        falls through to the legacy-file path. A successful upsert
        here replaces any existing row (including a valid HMAC row),
        so even if a future retry of ``delete_completion_sentinel``
        fails the cleared state remains authoritative.
        """
        with self._conn:
            params: tuple[str, ...] = (run_id, CLEARED_SENTINEL_HMAC)
            self._conn.execute(
                "INSERT INTO completion_sentinels (run_id, hmac) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET hmac=excluded.hmac",
                params,
            )

    # -- retention ---------------------------------------------------------

    def prune_older_than(self, cutoff: float, *, keep_run_id: str | None = None) -> int:
        """Delete aged rows from both tables. Returns total row count removed.

        Used by the run-start retention sweep (RFC-013 P3) so DB rows do
        not accumulate alongside the file-glob bookkeeping sweep.

        When ``keep_run_id`` is provided, rows for that run are skipped
        regardless of age — mirrors the file-path ``keep_run_id``
        contract so the DB-backed retention behavior matches the
        on-disk convention during the rollout.
        """
        receipt_sql = "DELETE FROM receipts WHERE created_at < ?"
        sentinel_sql = "DELETE FROM completion_sentinels WHERE created_at < ?"
        params: tuple[float | str, ...]
        if keep_run_id is not None:
            receipt_sql += " AND run_id != ?"
            sentinel_sql += " AND run_id != ?"
            params = (cutoff, keep_run_id)
        else:
            params = (cutoff,)
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
