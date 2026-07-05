"""Run-start retention sweep for machine-only ``.agent`` bookkeeping.

Long-lived workspaces accumulate one ``completion_seen_<run_id>.json``
per agent session, one ``receipts/<run_id>/`` directory per run, and
``agent_retry_*`` scratch per retry — hundreds of files over multi-day
runs. Nothing reads them after their run ends. The sweep deletes
entries older than ``max_age_seconds`` (default 7 days), always keeping
the current run's entries regardless of age.

Everything here is best-effort: a failed unlink is skipped, never raised,
so a permission quirk cannot break run startup. The DB prune (RFC-013
P3) is invoked with the same best-effort contract.
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from ralph.mcp.artifacts.state_db import DB_RELPATH, RunStateDB

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600.0

_SCRATCH_GLOBS: tuple[str, ...] = (
    "agent_retry_*.md",
    "agent_retry_context_*.md",
)


def _older_than(path: Path, cutoff: float) -> bool:
    """True when *path*'s mtime (or any contained file for a directory) is older than *cutoff*."""
    try:
        if path.is_dir():
            try:
                newest_mtime = path.stat().st_mtime
            except OSError:
                newest_mtime = 0.0
            for child in path.iterdir():
                try:
                    child_mtime = child.stat().st_mtime
                except OSError:
                    continue
                newest_mtime = max(newest_mtime, child_mtime)
            return newest_mtime < cutoff
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _sweep_completion_sentinels(
    agent_dir: Path,
    *,
    cutoff: float,
    keep_sentinel: str | None,
) -> int:
    """Remove aged completion sentinel JSON files (never raises)."""
    removed = 0
    for sentinel in agent_dir.glob("completion_seen_*.json"):
        if sentinel.name == keep_sentinel or not _older_than(sentinel, cutoff):
            continue
        try:
            sentinel.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_receipt_dirs(
    receipts_dir: Path,
    *,
    cutoff: float,
    keep_run_id: str | None,
) -> int:
    """Remove aged per-run receipt directories (never raises)."""
    if not receipts_dir.is_dir():
        return 0
    removed = 0
    for run_dir in receipts_dir.iterdir():
        if not run_dir.is_dir() or run_dir.name == keep_run_id:
            continue
        if not _older_than(run_dir, cutoff):
            continue
        try:
            shutil.rmtree(run_dir)
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_scratch_files(tmp_dir: Path, *, cutoff: float) -> int:
    """Remove aged ``agent_retry_*`` scratch files (never raises)."""
    if not tmp_dir.is_dir():
        return 0
    removed = 0
    for pattern in _SCRATCH_GLOBS:
        for scratch in tmp_dir.glob(pattern):
            if not _older_than(scratch, cutoff):
                continue
            try:
                scratch.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _sweep_run_state_db_rows(
    workspace_root: Path,
    *,
    cutoff: float,
    keep_run_id: str | None,
) -> int:
    """RFC-013 P3: prune aged rows in ``.agent/state.db`` (never raises).

    Mirrors the file-path ``keep_run_id`` contract: when ``keep_run_id``
    is provided, rows for that run are preserved regardless of age so
    the DB-backed retention behavior does not regress the in-flight
    run's own receipts and sentinels.

    Side-effect free: when ``.agent/state.db`` is absent the sweep
    does NOT create one. ``RunStateDB.__init__`` creates the database
    on open, so this helper short-circuits on absence to avoid
    turning the cleanup path into a state.db-creation path.
    """
    db_path = workspace_root / DB_RELPATH
    if not db_path.exists():
        return 0
    try:
        db = RunStateDB(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        return 0
    try:
        try:
            return db.prune_older_than(cutoff, keep_run_id=keep_run_id)
        except (OSError, RuntimeError, sqlite3.Error):
            return 0
    finally:
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            db.close()


def sweep_agent_dir(
    workspace_root: Path,
    *,
    keep_run_id: str | None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Callable[[], float] = time.time,
) -> int:
    """Delete aged machine-only bookkeeping under ``<workspace>/.agent``.

    The file-glob sweep covers ``completion_seen_*.json``, ``receipts/``,
    and ``tmp/agent_retry_*.md``. When ``.agent/state.db`` is present
    (RFC-013 P3) the sweep also calls ``RunStateDB.prune_older_than`` so
    aged DB rows do not accumulate either. Both passes are best-effort.

    Args:
        workspace_root: Workspace root containing ``.agent``.
        keep_run_id: Current run id whose sentinel/receipts are always kept.
        max_age_seconds: Entries younger than this are kept.
        now: Clock injection for tests.

    Returns:
        Number of filesystem entries removed (file count + DB row count).
    """
    agent_dir = workspace_root / ".agent"
    if not agent_dir.is_dir():
        return 0
    cutoff = now() - max_age_seconds
    keep_sentinel = (
        f"completion_seen_{keep_run_id}.json" if keep_run_id is not None else None
    )
    removed = _sweep_completion_sentinels(
        agent_dir,
        cutoff=cutoff,
        keep_sentinel=keep_sentinel,
    )
    removed += _sweep_receipt_dirs(
        agent_dir / "receipts",
        cutoff=cutoff,
        keep_run_id=keep_run_id,
    )
    removed += _sweep_scratch_files(agent_dir / "tmp", cutoff=cutoff)
    removed += _sweep_run_state_db_rows(
        workspace_root, cutoff=cutoff, keep_run_id=keep_run_id
    )
    return removed


__all__ = ["DEFAULT_MAX_AGE_SECONDS", "sweep_agent_dir"]
