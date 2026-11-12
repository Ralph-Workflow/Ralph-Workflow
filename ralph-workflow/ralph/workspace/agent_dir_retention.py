from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import sys
import threading
import time
from typing import TYPE_CHECKING, cast

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.mcp.artifacts.state_db import DB_RELPATH, RunStateDB

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600.0
_COALESCED_WAVE_WINDOW_SECONDS = 0.05

_SCRATCH_GLOBS: tuple[str, ...] = (
    "agent_retry_*.md",
    "agent_retry_context_*.md",
)
_ACTIVE_RUNS_RELPATH = ".agent/active_runs.json"
_OWNERSHIP_RELPATH = ".agent/tmp/ownership.json"
_METADATA_LOCK_RELPATH = ".agent/.retention-metadata.lock"


class RetentionPassCoordinator:
    def __init__(self, *, on_wave_acquired: Callable[[], None] | None = None) -> None:
        self._condition = threading.Condition()
        self._pass_count = 0
        self._wave: int | None = None
        self._published_until = 0.0
        self._in_flight = False
        self._joiners = 0
        self._late_joiners = 0
        self._on_wave_acquired = on_wave_acquired

    @property
    def passes(self) -> int:
        with self._condition:
            return self._pass_count

    @contextlib.contextmanager
    def guard(self, workspace_root: Path) -> Iterator[int | None]:
        del workspace_root
        with self._condition:
            now = time.monotonic()
            if self._wave is not None and (
                self._in_flight or now < self._published_until or self._late_joiners > 0
            ):
                yield_wave: int | None = self._wave
                owner = False
                consume = False
                late = not self._in_flight
                if late:
                    self._late_joiners += 1
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
            try:
                yield yield_wave
            finally:
                if late:
                    with self._condition:
                        self._late_joiners -= 1
                        if self._late_joiners == 0:
                            self._published_until = (
                                time.monotonic() + _COALESCED_WAVE_WINDOW_SECONDS
                            )
                        self._condition.notify_all()
            return
        if consume:
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
                while self._joiners > 0 or self._late_joiners > 0:
                    self._condition.wait()
                self._in_flight = False
                self._condition.notify_all()

    def record(self, removed: int) -> None:
        with self._condition:
            self._wave = removed
            self._published_until = time.monotonic() + _COALESCED_WAVE_WINDOW_SECONDS
            self._pass_count += 1
            self._condition.notify_all()


_active_run_lock = threading.Lock()


@contextlib.contextmanager
def _metadata_lock(workspace_root: Path) -> Iterator[None]:
    lock_path = workspace_root / _METADATA_LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if sys.platform == "win32":
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> object:
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded


def _write_json(path: Path, value: object) -> None:
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
    with _active_run_lock, _metadata_lock(workspace_root):
        _write_json(
            workspace_root / _ACTIVE_RUNS_RELPATH,
            {"run_ids": sorted(_active_run_ids(workspace_root) | {run_id})},
        )


def unregister_active_run(workspace_root: Path, run_id: str) -> None:
    with _active_run_lock, _metadata_lock(workspace_root):
        _write_json(
            workspace_root / _ACTIVE_RUNS_RELPATH,
            {"run_ids": sorted(_active_run_ids(workspace_root) - {run_id})},
        )


def prune_lock_run_ids(
    workspace_root: Path,
    *,
    extra_keep: Iterable[str] = (),
) -> frozenset[str]:
    return _active_run_ids(workspace_root) | frozenset(extra_keep)


def register_temporary_path_owner(workspace_root: Path, path: Path, run_id: str) -> None:
    ownership_path = workspace_root / _OWNERSHIP_RELPATH
    try:
        relative_path = path.relative_to(workspace_root / ".agent").as_posix()
    except ValueError:
        return
    with _metadata_lock(workspace_root):
        raw = _read_json(ownership_path)
        owners: dict[str, object] = (
            {key: cast("object", value) for key, value in raw.items() if isinstance(key, str)}
            if isinstance(raw, dict)
            else {}
        )
        owners[relative_path] = {"run_id": run_id, "created_at": time.time()}
        _write_json(ownership_path, owners)


def unregister_temporary_path_owner(workspace_root: Path, path: Path) -> None:
    ownership_path = workspace_root / _OWNERSHIP_RELPATH
    try:
        relative_path = path.relative_to(workspace_root / ".agent").as_posix()
    except ValueError:
        return
    with _metadata_lock(workspace_root):
        raw = _read_json(ownership_path)
        if not isinstance(raw, dict):
            return
        owners: dict[str, object] = {
            key: value for key, value in raw.items() if isinstance(key, str)
        }
        if relative_path in owners:
            owners.pop(relative_path)
            _write_json(ownership_path, owners)


def _prune_missing_temporary_path_owners(workspace_root: Path) -> None:
    ownership_path = workspace_root / _OWNERSHIP_RELPATH
    with _metadata_lock(workspace_root):
        owners = _temporary_path_owners(workspace_root)
        retained = {
            relative_path: entry
            for relative_path, entry in owners.items()
            if (workspace_root / ".agent" / relative_path).exists()
        }
        if retained != owners:
            _write_json(
                ownership_path,
                {
                    relative_path: {"run_id": run_id, "created_at": created_at}
                    for relative_path, (run_id, created_at) in retained.items()
                },
            )


def _temporary_path_owners(workspace_root: Path) -> dict[str, tuple[str, float]]:
    raw = _read_json(workspace_root / _OWNERSHIP_RELPATH)
    if not isinstance(raw, dict):
        return {}
    owners: dict[str, tuple[str, float]] = {}
    for path, entry in raw.items():
        if isinstance(path, str) and isinstance(entry, dict):
            run_id = entry.get("run_id")
            created_at = entry.get("created_at")
            if isinstance(run_id, str) and isinstance(created_at, (int, float)):
                owners[path] = (run_id, float(created_at))
    return owners


def _older_than(path: Path, cutoff: float) -> bool:
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
            shutil.rmtree(run_dir)
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_codex_home_dirs(
    tmp_dir: Path,
    *,
    cutoff: float,
    owners: dict[str, tuple[str, float]],
    active_run_ids: frozenset[str],
) -> int:
    if not tmp_dir.is_dir():
        return 0
    removed = 0
    for home in tmp_dir.glob("codex-home-*"):
        owner = owners.get(home.relative_to(tmp_dir.parent).as_posix())
        if owner is not None and (owner[0] in active_run_ids or owner[1] >= cutoff):
            continue
        try:
            is_aged_dir = home.is_dir() and home.lstat().st_mtime < cutoff
        except OSError:
            continue
        if not is_aged_dir:
            continue
        try:
            shutil.rmtree(home)
            removed += 1
        except OSError:
            continue
    return removed


def _sweep_session_files(agent_dir: Path, *, cutoff: float) -> int:
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
    tmp_dir: Path,
    *,
    cutoff: float,
    owners: dict[str, tuple[str, float]],
    active_run_ids: frozenset[str],
) -> int:
    if not tmp_dir.is_dir():
        return 0
    removed = 0
    for pattern in _SCRATCH_GLOBS:
        for scratch in tmp_dir.glob(pattern):
            owner = owners.get(scratch.relative_to(tmp_dir.parent).as_posix())
            if owner is not None and (owner[0] in active_run_ids or owner[1] >= cutoff):
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
    _prune_missing_temporary_path_owners(workspace_root)
    removed += _sweep_session_files(agent_dir, cutoff=cutoff)
    removed += _sweep_run_state_db_rows(workspace_root, cutoff=cutoff, keep_run_ids=keep_run_ids)
    return removed


_RETENTION_COORDINATOR_HOLDER: list[RetentionPassCoordinator | None] = [None]


def process_retention_coordinator() -> RetentionPassCoordinator:
    if _RETENTION_COORDINATOR_HOLDER[0] is None:
        _RETENTION_COORDINATOR_HOLDER[0] = RetentionPassCoordinator()
    coordinator = _RETENTION_COORDINATOR_HOLDER[0]
    assert coordinator is not None
    return coordinator


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS",
    "RetentionPassCoordinator",
    "process_retention_coordinator",
    "prune_lock_run_ids",
    "register_active_run",
    "register_temporary_path_owner",
    "sweep_agent_dir",
    "unregister_active_run",
    "unregister_temporary_path_owner",
]
