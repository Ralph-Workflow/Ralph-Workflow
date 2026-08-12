"""Run-start retention sweep for machine-only ``.agent`` bookkeeping.

Long-lived workspaces accumulate one ``completion_seen_<run_id>.json``
per agent session, one ``receipts/<run_id>/`` directory per run,
``agent_retry_*`` scratch per retry, crashed-run ``tmp/codex-home-*``
directories, and crashed MCP session JSON files — hundreds of files over
multi-day runs. Nothing reads them after their run ends. The sweep deletes
entries older than ``max_age_seconds`` (default 7 days), always keeping
the current run's entries regardless of age.

Everything here is best-effort: a failed unlink is skipped, never raised,
so a permission quirk cannot break run startup. The DB prune (RFC-013
P3) is invoked with the same best-effort contract.

Two coordination seams keep concurrent callers safe:

* ``RetentionPassCoordinator`` coalesces parallel ``sweep_agent_dir``
  callers into one retention pass per wave generation: the first entrant
  runs the inner sweep body and every concurrent caller joins that wave
  and shares its ``removed`` count instead of re-running the sweep.
* The filesystem-backed active-run registry (``register_active_run`` /
  ``unregister_active_run`` / ``prune_lock_run_ids``) protects receipts,
  sentinels, DB rows, and owned temporary paths for every registered run
  across processes.

``RetentionPassCoordinator`` remains process-local because it only coalesces
redundant work; the shared files preserve cross-process ownership.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.mcp.artifacts.state_db import DB_RELPATH, RunStateDB

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600.0

_SCRATCH_GLOBS: tuple[str, ...] = (
    "agent_retry_*.md",
    "agent_retry_context_*.md",
)
_ACTIVE_RUNS_RELPATH = ".agent/active_runs.json"
_OWNERSHIP_RELPATH = ".agent/tmp/ownership.json"


class RetentionPassCoordinator:
    """Coalesce parallel retention passes into one inner sweep per wave.

    This coordinator is process-local; it intentionally does not coordinate
    independent processes, which remains the B6 future boundary.

    The first thread entering ``guard`` becomes the wave owner: it runs
    the inner sweep body, stores the ``Wave`` result, and increments the
    process-local pass counter exactly once. Concurrent callers arriving
    while the wave is in flight join that wave, observe the same
    ``Wave.removed`` value, and do NOT re-run the inner body or
    increment the counter. Once the owner exits, the wave slot clears
    and the next arrival starts a new wave with a fresh counter
    increment.
    """

    def __init__(self, *, on_wave_acquired: Callable[[], None] | None = None) -> None:
        self._condition = threading.Condition()
        self._pass_count = 0
        # Shared result of one coalesced retention pass (the removed count),
        # or ``None`` while the owning wave is still in flight.
        self._wave: int | None = None
        self._in_flight = False
        self._joiners = 0
        # Test seam: invoked once per caller AFTER the wave is acquired
        # (owner or joiner) but BEFORE the owner runs the sweep body, so
        # a barrier can prove every caller entered the wave first.
        self._on_wave_acquired = on_wave_acquired

    @property
    def passes(self) -> int:
        """Number of completed retention waves (inner sweep bodies run)."""
        with self._condition:
            return self._pass_count

    @contextlib.contextmanager
    def guard(self, workspace_root: Path) -> Iterator[int | None]:
        """Yield ``None`` to the wave owner (run the body) or the shared count.

        The owner stores its result with ``coordinator.record(removed)``
        before exiting; joiners receive the completed removed count.
        """
        del workspace_root  # one coordinator per process is sufficient today
        with self._condition:
            if self._wave is not None:
                # Wave result already published: join without waiting.
                yield_wave: int | None = self._wave
                owner = False
                consume = False
            elif self._in_flight:
                yield_wave = None
                owner = False
                consume = True
                self._joiners += 1
            else:
                self._in_flight = True
                yield_wave = None
                owner = True
                consume = False
        if not owner and not consume:
            yield yield_wave
            return
        if consume:
            # The wave is mid-sweep: announce entry BEFORE blocking so a
            # barrier can prove every caller entered the wave before the
            # owner runs the sweep body, then wait for the publication.
            if self._on_wave_acquired is not None:
                self._on_wave_acquired()
            with self._condition:
                while self._wave is None:
                    self._condition.wait()
                wave = self._wave
                self._joiners -= 1
                self._condition.notify_all()
            yield wave
            return
        if self._on_wave_acquired is not None:
            self._on_wave_acquired()
        try:
            yield None
        finally:
            with self._condition:
                # Hold the published wave until every joiner consumed it;
                # only then clear the slot so the next arrival owns a new
                # wave with a fresh pass-count increment.
                while self._joiners > 0:
                    self._condition.wait()
                self._in_flight = False
                self._wave = None
                self._condition.notify_all()

    def record(self, removed: int) -> None:
        """Publish the owner's removed count and count exactly one pass."""
        with self._condition:
            self._wave = removed
            self._pass_count += 1
            self._condition.notify_all()


_active_run_lock = threading.Lock()


def _read_json(path: Path) -> object:
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded


def _write_json(path: Path, value: object) -> None:
    # filesystem-write-ok: atomic cross-process retention metadata publication
    atomic_write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        path,
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        tmp_path=path.with_name(f".{path.name}.tmp"),
        prepare_write=lambda: path.parent.mkdir(parents=True, exist_ok=True),
    )


def _active_run_ids(workspace_root: Path) -> frozenset[str]:
    raw = _read_json(workspace_root / _ACTIVE_RUNS_RELPATH)
    if not isinstance(raw, dict):
        return frozenset()
    values = raw.get("run_ids")
    if not isinstance(values, list):
        return frozenset()
    return frozenset(value for value in values if isinstance(value, str))


def register_active_run(workspace_root: Path, run_id: str) -> None:
    """Mark ``run_id`` active in the shared registry so every process keeps its data."""
    with _active_run_lock:
        _write_json(
            workspace_root / _ACTIVE_RUNS_RELPATH,
            {"run_ids": sorted(_active_run_ids(workspace_root) | {run_id})},
        )


def unregister_active_run(workspace_root: Path, run_id: str) -> None:
    """Release ``run_id`` from the shared active-run registry."""
    with _active_run_lock:
        _write_json(
            workspace_root / _ACTIVE_RUNS_RELPATH,
            {"run_ids": sorted(_active_run_ids(workspace_root) - {run_id})},
        )


def prune_lock_run_ids(
    workspace_root: Path,
    *,
    extra_keep: Iterable[str] = (),
) -> frozenset[str]:
    """Return shared active runs merged with explicit exclusions."""
    return _active_run_ids(workspace_root) | frozenset(extra_keep)


def register_temporary_path_owner(workspace_root: Path, path: Path, run_id: str) -> None:
    """Record the run that owns a retry scratch file or workspace Codex home."""
    ownership_path = workspace_root / _OWNERSHIP_RELPATH
    raw = _read_json(ownership_path)
    owners: dict[str, object] = (
        {key: value for key, value in raw.items() if isinstance(key, str)}
        if isinstance(raw, dict)
        else {}
    )
    try:
        relative_path = path.relative_to(workspace_root / ".agent").as_posix()
    except ValueError:
        return
    owners[relative_path] = {"run_id": run_id, "created_at": time.time()}
    _write_json(ownership_path, owners)


def unregister_temporary_path_owner(workspace_root: Path, path: Path) -> None:
    """Remove ownership metadata after the normal owner has released its path."""
    ownership_path = workspace_root / _OWNERSHIP_RELPATH
    raw = _read_json(ownership_path)
    if not isinstance(raw, dict):
        return
    try:
        relative_path = path.relative_to(workspace_root / ".agent").as_posix()
    except ValueError:
        return
    owners: dict[str, object] = {key: value for key, value in raw.items() if isinstance(key, str)}
    if relative_path in owners:
        owners.pop(relative_path)
        _write_json(ownership_path, owners)


def _prune_missing_temporary_path_owners(workspace_root: Path, owners: dict[str, str]) -> None:
    """Drop ownership entries once their temporary path was reclaimed."""
    retained = {
        relative_path: run_id
        for relative_path, run_id in owners.items()
        if (workspace_root / ".agent" / relative_path).exists()
    }
    if retained != owners:
        _write_json(
            workspace_root / _OWNERSHIP_RELPATH,
            {
                relative_path: {"run_id": run_id, "created_at": time.time()}
                for relative_path, run_id in retained.items()
            },
        )


def _temporary_path_owners(workspace_root: Path) -> dict[str, str]:
    raw = _read_json(workspace_root / _OWNERSHIP_RELPATH)
    if not isinstance(raw, dict):
        return {}
    owners: dict[str, str] = {}
    for path, entry in raw.items():
        if isinstance(path, str) and isinstance(entry, dict):
            run_id = entry.get("run_id")
            if isinstance(run_id, str):
                owners[path] = run_id
    return owners



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
    keep_sentinels: Iterable[str],
) -> int:
    """Remove aged completion sentinel JSON files (never raises)."""
    keep = frozenset(keep_sentinels)
    removed = 0
    for sentinel in agent_dir.glob("completion_seen_*.json"):
        if sentinel.name in keep or not _older_than(sentinel, cutoff):
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
    keep_run_ids: Iterable[str],
) -> int:
    """Remove aged per-run receipt directories (never raises)."""
    if not receipts_dir.is_dir():
        return 0
    keep = frozenset(keep_run_ids)
    removed = 0
    for run_dir in receipts_dir.iterdir():
        if not run_dir.is_dir() or run_dir.name in keep:
            continue
        if not _older_than(run_dir, cutoff):
            continue
        try:
            # filesystem-write-ok: bounded oldest-first retention cleanup of obsolete receipt directory
            shutil.rmtree(run_dir)
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_codex_home_dirs(
    tmp_dir: Path, *, cutoff: float, owners: dict[str, str], active_run_ids: frozenset[str]
) -> int:
    """Remove aged Codex-home directories using their own metadata (never raises)."""
    if not tmp_dir.is_dir():
        return 0
    removed = 0
    for home in tmp_dir.glob("codex-home-*"):
        if owners.get(home.relative_to(tmp_dir.parent).as_posix()) in active_run_ids:
            continue
        try:
            is_aged_dir = home.is_dir() and home.lstat().st_mtime < cutoff
        except OSError:
            continue
        if not is_aged_dir:
            continue
        try:
            # filesystem-write-ok: bounded retention cleanup of stale temporary Codex home
            shutil.rmtree(home)
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_session_files(agent_dir: Path, *, cutoff: float) -> int:
    """Remove aged MCP session metadata files (never raises)."""
    removed = 0
    for session_file in agent_dir.glob("ralph-mcp-session-*.json"):
        if not _older_than(session_file, cutoff):
            continue
        try:
            session_file.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_scratch_files(
    tmp_dir: Path, *, cutoff: float, owners: dict[str, str], active_run_ids: frozenset[str]
) -> int:
    """Remove aged ``agent_retry_*`` scratch files (never raises)."""
    if not tmp_dir.is_dir():
        return 0
    removed = 0
    for pattern in _SCRATCH_GLOBS:
        for scratch in tmp_dir.glob(pattern):
            if owners.get(scratch.relative_to(tmp_dir.parent).as_posix()) in active_run_ids:
                continue
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
    keep_run_ids: Iterable[str],
) -> int:
    """RFC-013 P3: prune aged rows in ``.agent/state.db`` (never raises).

    Mirrors the file-path ``keep_run_ids`` contract: rows for every kept
    run are preserved regardless of age so the DB-backed retention
    behavior does not regress any in-flight run's own receipts and
    sentinels.

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
    removed = 0
    try:
        try:
            removed = db.prune_older_than(cutoff, keep_run_ids=keep_run_ids)
        except (OSError, RuntimeError, sqlite3.Error):
            return 0
        return removed
    finally:
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            db.close()


def sweep_agent_dir(
    workspace_root: Path,
    *,
    keep_run_id: str | None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Callable[[], float] = time.time,
    coordinator: RetentionPassCoordinator | None = None,
) -> int:
    """Delete aged machine-only bookkeeping under ``<workspace>/.agent``.

    The file-glob sweep covers ``completion_seen_*.json``, ``receipts/``,
    ``tmp/agent_retry_*.md``, ``tmp/codex-home-*`` (using the directory's
    own mtime so mirrored symlinks cannot keep orphans fresh), and
    ``ralph-mcp-session-*.json``. When ``.agent/state.db`` is present
    (RFC-013 P3) the sweep also calls ``RunStateDB.prune_older_than`` so
    aged DB rows do not accumulate either. Both passes are best-effort.

    Args:
        workspace_root: Workspace root containing ``.agent``.
        keep_run_id: Current run id whose sentinel/receipts are always kept.
            Merged with the process-local active-run registry so every
            registered in-process run's data survives the sweep.
        max_age_seconds: Entries younger than this are kept.
        now: Clock injection for tests.
        coordinator: Optional shared ``RetentionPassCoordinator``. When
            supplied, parallel callers coalesce into one retention pass
            per wave: the first caller runs the inner sweep body and
            every concurrent caller in the same wave receives its shared
            ``removed`` count without re-running the sweep.

    Returns:
        Number of filesystem entries removed (file count + DB row count).
    """
    if coordinator is not None:
        with coordinator.guard(workspace_root) as wave:
            if wave is not None:
                return wave
            removed = _sweep_agent_dir_body(
                workspace_root,
                keep_run_id=keep_run_id,
                max_age_seconds=max_age_seconds,
                now=now,
            )
            coordinator.record(removed)
            return removed
    return _sweep_agent_dir_body(
        workspace_root,
        keep_run_id=keep_run_id,
        max_age_seconds=max_age_seconds,
        now=now,
    )


def _sweep_agent_dir_body(
    workspace_root: Path,
    *,
    keep_run_id: str | None,
    max_age_seconds: float,
    now: Callable[[], float],
) -> int:
    """Run one retention pass body (invoked at most once per wave)."""
    agent_dir = workspace_root / ".agent"
    if not agent_dir.is_dir():
        return 0
    cutoff = now() - max_age_seconds
    extra_keep: tuple[str, ...] = (keep_run_id,) if keep_run_id is not None else ()
    keep_run_ids = prune_lock_run_ids(workspace_root, extra_keep=extra_keep)
    keep_sentinels = tuple(f"completion_seen_{run_id}.json" for run_id in keep_run_ids)
    removed = _sweep_completion_sentinels(
        agent_dir,
        cutoff=cutoff,
        keep_sentinels=keep_sentinels,
    )
    removed += _sweep_receipt_dirs(
        agent_dir / "receipts",
        cutoff=cutoff,
        keep_run_ids=keep_run_ids,
    )
    tmp_dir = agent_dir / "tmp"
    owners = _temporary_path_owners(workspace_root)
    removed += _sweep_scratch_files(
        tmp_dir, cutoff=cutoff, owners=owners, active_run_ids=keep_run_ids
    )
    removed += _sweep_codex_home_dirs(
        tmp_dir, cutoff=cutoff, owners=owners, active_run_ids=keep_run_ids
    )
    _prune_missing_temporary_path_owners(workspace_root, owners)
    removed += _sweep_session_files(agent_dir, cutoff=cutoff)
    removed += _sweep_run_state_db_rows(workspace_root, cutoff=cutoff, keep_run_ids=keep_run_ids)
    return removed


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS",
    "RetentionPassCoordinator",
    "prune_lock_run_ids",
    "register_active_run",
    "register_temporary_path_owner",
    "sweep_agent_dir",
    "unregister_active_run",
    "unregister_temporary_path_owner",
]
