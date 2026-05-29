"""Reusable, resettable per-workspace exec sandbox pools.

`ExecSandboxManager` owns the lifecycle for the workspace-keyed sandbox pool
that MCP exec runs in. Each `acquire()` returns a clean sandbox slot selected
from a pool keyed by the workspace path hash; repeated same-workspace execs can
run concurrently by leasing different slots, while each individual slot still
resets before use and cleans up after release.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools._exec_cache_cleanup_summary import ExecCacheCleanupSummary
from ralph.mcp.tools._exec_sandbox_busy_error import ExecSandboxBusyError
from ralph.mcp.tools.exec_overlay import (
    _GENERATED_DIR_NAMES,
    _current_process_identity,
    _ensure_git_isolation,
    _ignored_workspace_relative_paths,
    _mirror_workspace,
    _overlay_owner_metadata,
    _process_identity_matches,
    _write_overlay_owner_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_LOCK_FILE = ".ralph-sandbox.lock"
_POOL_STATE_FILE = ".ralph-sandbox-pool.json"
_POOL_LOCK_FILE = ".ralph-sandbox-pool.lock"
_DEFAULT_LOCK_TIMEOUT_S = 0.1
_MIN_POOL_LOCK_TIMEOUT_S = 0.05
_LOCK_POLL_INTERVAL_S = 0.005
_KEY_LENGTH = 16
_MIN_SLOTS = 1
_DEFAULT_MAX_SLOTS = 3
_DEFAULT_MAX_WORKSPACE_POOLS = 8
_DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_POOL_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_IDLE_SLOT_AGE_S = 10 * 60.0
_DEFAULT_MAX_IDLE_POOL_AGE_S = 30 * 60.0
_DEFAULT_FORCED_MAX_SLOT_AGE_S = 24 * 60 * 60.0
_DEFAULT_HARD_CAP_MULTIPLIER = 2
_MAX_CONSECUTIVE_CLEANUP_FAILURES = 5
_CLEANUP_COOLDOWN_S = 300.0
_CLEANUP_THROTTLE_S = 10.0
_SLOT_PREFIX = "slot-"
_BASE_LOCK_FILE = ".ralph-exec-base.lock"
_TRASH_PREFIX = ".ralph-exec-trash-"
_BASE_PRUNE_INTERVAL_S = 1.0


def _workspace_key(workspace_root: Path) -> str:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:_KEY_LENGTH]


def _compute_sandbox_limits(workspace_size_bytes: int) -> tuple[int, int, int]:
    """Return (max_total_bytes, max_pool_bytes, max_workspace_bytes) scaled to workspace size.

    Each workspace instance gets its own sandbox manager with limits proportional
    to the repository size, rather than contending for a single shared global cap.
    Small repos keep the existing defaults; large repos scale up to avoid eviction
    pressure when sandbox slots each carry a full workspace mirror.
    """
    if workspace_size_bytes <= 0:
        workspace_size_bytes = _DEFAULT_MAX_TOTAL_BYTES
    # Minimum slot count we want to support without LRU eviction of idle slots.
    min_desired_slots = 4
    # Estimate per-slot overhead: full workspace mirror + git isolation (~20% overhead).
    slot_bytes = int(workspace_size_bytes * 1.2)
    pool_bytes = max(
        _DEFAULT_MAX_POOL_BYTES,
        slot_bytes * min_desired_slots,
    )
    total_bytes = max(
        _DEFAULT_MAX_TOTAL_BYTES,
        pool_bytes * 2,  # allow at least 2 full pools
    )
    workspace_safety_bytes = max(
        _DEFAULT_MAX_WORKSPACE_BYTES,
        workspace_size_bytes * 6,
    )
    return total_bytes, pool_bytes, workspace_safety_bytes


def _compute_workspace_size_bytes(workspace_root: Path) -> int:
    """Calculate the on-disk size of a workspace, excluding generated directories."""
    total = 0
    ignored_relative_paths = _ignored_workspace_relative_paths(workspace_root)

    def _scan_dir(dir_path: Path) -> None:
        nonlocal total
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    if entry.name in _GENERATED_DIR_NAMES:
                        continue
                    child = dir_path / entry.name
                    try:
                        rel = child.relative_to(workspace_root)
                    except ValueError:
                        continue
                    if any(
                        rel == ignored_path or rel.is_relative_to(ignored_path)
                        for ignored_path in ignored_relative_paths
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        _scan_dir(child)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            total += entry.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass

    _scan_dir(workspace_root)
    return total


@dataclass(frozen=True)
class _IdleSlotCandidate:
    slot_root: Path
    pool_root: Path
    last_used_at: float
    size_bytes: int


def _idle_slot_last_used_desc(candidate: _IdleSlotCandidate) -> float:
    return candidate.last_used_at


def _idle_slot_last_used_asc(candidate: _IdleSlotCandidate) -> float:
    return candidate.last_used_at


class ExecSandboxManager:
    """Manage reusable per-workspace exec sandboxes with reset-before-run semantics."""

    def __init__(
        self,
        *,
        base_dir: Path,
        lock_timeout_s: float = _DEFAULT_LOCK_TIMEOUT_S,
        max_slots: int = _DEFAULT_MAX_SLOTS,
        max_workspace_pools: int = _DEFAULT_MAX_WORKSPACE_POOLS,
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
        max_pool_bytes: int = _DEFAULT_MAX_POOL_BYTES,
        max_workspace_bytes: int = _DEFAULT_MAX_WORKSPACE_BYTES,
        max_idle_slot_age_s: float = _DEFAULT_MAX_IDLE_SLOT_AGE_S,
        max_idle_pool_age_s: float = _DEFAULT_MAX_IDLE_POOL_AGE_S,
        forced_max_slot_age_s: float = _DEFAULT_FORCED_MAX_SLOT_AGE_S,
        workspace_size_bytes: int | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._lock_timeout_s = lock_timeout_s
        self._max_slots = max(_MIN_SLOTS, max_slots)
        self._max_workspace_pools = max(_MIN_SLOTS, max_workspace_pools)
        self._max_total_bytes = max(0, max_total_bytes)
        self._max_pool_bytes = max(0, max_pool_bytes)
        self._max_workspace_bytes = max(0, max_workspace_bytes)
        self._max_idle_slot_age_s = max(0.0, max_idle_slot_age_s)
        self._max_idle_pool_age_s = max(0.0, max_idle_pool_age_s)
        self._forced_max_slot_age_s = max(0.0, forced_max_slot_age_s)
        self._last_base_prune_monotonic = 0.0
        self._managed_workspace_keys: set[str] = set()
        self._consecutive_cleanup_failures = 0
        self._cleanup_cooldown_until_monotonic = 0.0
        self._cached_workspace_size_bytes = workspace_size_bytes
        self._last_cleanup_monotonic = 0.0
        self._slot_cycle = itertools.cycle(range(max(_MIN_SLOTS, max_slots)))
        self._slot_lock = threading.Lock()

    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        """Yield a sandbox worktree selected by round-robin.

        Each sandbox slot has a persistent worktree.  rsync ``--delete``
        handles the diff naturally on every call — unchanged files stay,
        changed files are updated, and deleted files are removed — so no
        explicit cleanup is needed.
        """
        self._base_dir.mkdir(parents=True, exist_ok=True)
        if self._cleanup_in_cooldown():
            raise OSError(
                "exec cache cleanup has failed repeatedly — "
                "refusing to proceed until cooldown expires"
            )
        self.cleanup_base()
        workspace_key = _workspace_key(workspace_root)
        pool_root = self._base_dir / workspace_key
        pool_root.mkdir(parents=True, exist_ok=True)

        with self._slot_lock:
            slot_idx = next(self._slot_cycle)
        sandbox_root = pool_root / f"s{slot_idx}"
        sandbox_root.mkdir(parents=True, exist_ok=True)
        worktree = sandbox_root / "ws"

        _mirror_workspace(workspace_root, worktree)
        _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        _write_overlay_owner_metadata(sandbox_root)

        yield worktree

    def cleanup_base(self) -> ExecCacheCleanupSummary:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        now = time.monotonic()
        if now - self._last_cleanup_monotonic < _CLEANUP_THROTTLE_S:
            return ExecCacheCleanupSummary(
                removed_paths=0,
                removed_bytes=0,
                remaining_bytes=0,
            )
        try:
            with self._base_lock():
                summary = self._cleanup_base_locked()
                self._last_cleanup_monotonic = time.monotonic()
                self._record_cleanup_success()
                return summary
        except ExecSandboxBusyError:
            return ExecCacheCleanupSummary(
                removed_paths=0,
                removed_bytes=0,
                remaining_bytes=self._path_size_bytes_via_du(self._base_dir),
            )
        except Exception:
            self._record_cleanup_failure()
            raise

    def _lock_path(self, sandbox_root: Path) -> Path:
        return sandbox_root / _LOCK_FILE

    def _pool_state_path(self, pool_root: Path) -> Path:
        return pool_root / _POOL_STATE_FILE

    def _pool_lock_path(self, pool_root: Path) -> Path:
        return pool_root / _POOL_LOCK_FILE

    def _base_lock_path(self) -> Path:
        return self._base_dir / _BASE_LOCK_FILE

    def _lock_owner_metadata(self, lock_path: Path) -> tuple[int | None, float | None]:
        if not lock_path.is_file():
            return None, None
        try:
            raw = cast("object", json.loads(lock_path.read_text(encoding="utf-8")))
        except Exception:
            return None, None
        if not isinstance(raw, dict):
            return None, None
        payload = cast("dict[str, object]", raw)
        pid = payload.get("pid")
        started_at = payload.get("started_at")
        normalized_pid = pid if isinstance(pid, int) else None
        normalized_started_at = float(started_at) if isinstance(started_at, (int, float)) else None
        return normalized_pid, normalized_started_at

    def _all_slot_dirs(self, pool_root: Path) -> list[Path]:
        return [
            child
            for child in pool_root.iterdir()
            if child.is_dir() and child.name.startswith(_SLOT_PREFIX)
        ]

    def _slot_is_live(self, slot_root: Path) -> bool:
        lock_path = self._lock_path(slot_root)
        lock_owner_pid, lock_owner_started_at = self._lock_owner_metadata(lock_path)
        if lock_owner_pid is not None and _process_identity_matches(
            lock_owner_pid, lock_owner_started_at
        ):
            return True
        owner_pid, owner_started_at = _overlay_owner_metadata(slot_root)
        return owner_pid is not None and _process_identity_matches(owner_pid, owner_started_at)

    def _slot_is_locked_by_live_process(self, slot_root: Path) -> bool:
        lock_owner_pid, lock_owner_started_at = self._lock_owner_metadata(
            self._lock_path(slot_root)
        )
        return lock_owner_pid is not None and _process_identity_matches(
            lock_owner_pid, lock_owner_started_at
        )

    def _pool_has_live_leases(self, pool_root: Path) -> bool:
        return any(
            self._slot_is_locked_by_live_process(slot_root)
            for slot_root in self._all_slot_dirs(pool_root)
        )

    def _pool_lock_is_live(self, pool_root: Path) -> bool:
        lock_owner_pid, lock_owner_started_at = self._lock_owner_metadata(
            self._pool_lock_path(pool_root)
        )
        return lock_owner_pid is not None and _process_identity_matches(
            lock_owner_pid, lock_owner_started_at
        )

    def _pool_last_used_at(self, pool_root: Path) -> float:
        state_path = self._pool_state_path(pool_root)
        try:
            if state_path.exists():
                return state_path.stat().st_mtime
            return pool_root.stat().st_mtime
        except OSError:
            return 0.0

    def _pool_is_stale(self, pool_root: Path) -> bool:
        slot_dirs = self._all_slot_dirs(pool_root)
        if not slot_dirs:
            return True
        return all(not self._slot_is_live(slot_root) for slot_root in slot_dirs)

    def _slot_last_used_at(self, slot_root: Path) -> float:
        candidates = [slot_root, slot_root / "ws"]
        latest = 0.0
        for candidate in candidates:
            try:
                if candidate.exists():
                    latest = max(latest, candidate.stat().st_mtime)
            except OSError:
                continue
        return latest

    def _path_size_bytes(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            try:
                if path.name in {_LOCK_FILE, _POOL_LOCK_FILE, _BASE_LOCK_FILE}:
                    return 0
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    if child.name in {_LOCK_FILE, _POOL_LOCK_FILE, _BASE_LOCK_FILE}:
                        continue
                    total += child.stat().st_size
            except OSError:
                continue
        return total

    def _path_size_bytes_via_du(self, path: Path) -> int:
        if not path.exists():
            return 0
        try:
            children = list(path.iterdir())
        except OSError:
            return self._path_size_bytes(path)
        if len(children) == 0:
            return 0
        du = shutil.which("du")
        if du is not None:
            try:
                result = subprocess.run(
                    [du, "-sk", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0 and result.stdout:
                    return int(result.stdout.split()[0]) * 1024
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
        return self._path_size_bytes(path)

    def _workspace_size_bytes(self, workspace_root: Path) -> int:
        if self._cached_workspace_size_bytes is not None:
            return self._cached_workspace_size_bytes
        return _compute_workspace_size_bytes(workspace_root)

    def _slot_owner_is_collectible(self, slot_root: Path) -> bool:
        marker = slot_root / ".ralph-exec-owner.json"
        owner_pid, owner_started_at = _overlay_owner_metadata(slot_root)
        if not marker.exists() or owner_pid is None:
            return True
        return not _process_identity_matches(owner_pid, owner_started_at)

    def _slot_is_collectible(self, slot_root: Path, current_time: float) -> bool:
        if self._slot_is_locked_by_live_process(slot_root):
            return False
        age_s = current_time - self._slot_last_used_at(slot_root)
        if self._forced_max_slot_age_s > 0 and age_s >= self._forced_max_slot_age_s:
            return True
        if age_s >= self._max_idle_slot_age_s:
            return True
        return self._slot_owner_is_collectible(slot_root)

    def _prune_expired_and_broken_slots_locked(
        self, pool_root: Path, current_time: float
    ) -> tuple[list[_IdleSlotCandidate], int, int]:
        idle_candidates: list[_IdleSlotCandidate] = []
        removed_paths = 0
        removed_bytes = 0
        for slot_root in self._all_slot_dirs(pool_root):
            if self._slot_is_collectible(slot_root, current_time):
                self._rmtree_with_escalation(slot_root)
                removed_paths += 1
                continue
            if self._slot_is_locked_by_live_process(slot_root):
                continue
            idle_candidates.append(
                _IdleSlotCandidate(
                    slot_root=slot_root,
                    pool_root=pool_root,
                    last_used_at=self._slot_last_used_at(slot_root),
                    size_bytes=self._path_size_bytes(slot_root),
                )
            )
        return idle_candidates, removed_paths, removed_bytes

    def _enforce_pool_byte_budget_locked(
        self, idle_candidates: list[_IdleSlotCandidate]
    ) -> tuple[list[_IdleSlotCandidate], int, int]:
        removed_paths = 0
        removed_bytes = 0
        kept = sorted(idle_candidates, key=_idle_slot_last_used_desc, reverse=True)
        retained: list[_IdleSlotCandidate] = []
        retained_bytes = 0
        for candidate in kept:
            if retained_bytes + candidate.size_bytes <= self._max_pool_bytes or not retained:
                retained.append(candidate)
                retained_bytes += candidate.size_bytes
                continue
            removed_bytes += candidate.size_bytes
            self._rmtree_with_escalation(candidate.slot_root)
            removed_paths += 1
        return retained, removed_paths, removed_bytes

    def _delete_expired_pool_locked(self, pool_root: Path, current_time: float) -> tuple[int, int]:
        if self._pool_has_live_leases(pool_root) or self._pool_lock_is_live(pool_root):
            return 0, 0
        last_used_at = self._pool_last_used_at(pool_root)
        if current_time - last_used_at < self._max_idle_pool_age_s:
            return 0, 0
        if not self._try_acquire_named_lock(self._pool_lock_path(pool_root)):
            return 0, 0
        shutil.rmtree(pool_root, ignore_errors=True)
        return 1, 0

    def _cleanup_base_locked(self) -> ExecCacheCleanupSummary:
        current_time = time.time()
        removed_paths = 0
        removed_bytes = 0

        for child in list(self._base_dir.iterdir()):
            if child.name.startswith(_TRASH_PREFIX):
                shutil.rmtree(child, ignore_errors=True)
                removed_paths += 1

        global_idle_candidates: list[_IdleSlotCandidate] = []
        for pool_root in list(self._base_dir.iterdir()):
            if not pool_root.is_dir() or pool_root.name.startswith(_TRASH_PREFIX):
                continue
            try:
                idle_candidates, pool_removed_paths, pool_removed_bytes = (
                    self._prune_expired_and_broken_slots_locked(pool_root, current_time)
                )
                removed_paths += pool_removed_paths
                removed_bytes += pool_removed_bytes
                idle_candidates, budget_removed_paths, budget_removed_bytes = (
                    self._enforce_pool_byte_budget_locked(idle_candidates)
                )
                removed_paths += budget_removed_paths
                removed_bytes += budget_removed_bytes
                if not any(self._all_slot_dirs(pool_root)):
                    removed = self._delete_expired_pool_locked(pool_root, current_time)
                    removed_paths += removed[0]
                    removed_bytes += removed[1]
                    continue
                global_idle_candidates.extend(idle_candidates)
            except OSError:
                continue

        current_total_bytes = self._path_size_bytes_via_du(self._base_dir)
        if self._max_total_bytes > 0 and current_total_bytes > self._max_total_bytes:
            for candidate in sorted(global_idle_candidates, key=_idle_slot_last_used_asc):
                if current_total_bytes <= self._max_total_bytes:
                    break
                if not candidate.slot_root.exists() or self._slot_is_locked_by_live_process(
                    candidate.slot_root
                ):
                    continue
                candidate_size = self._path_size_bytes(candidate.slot_root)
                self._rmtree_with_escalation(candidate.slot_root)
                removed_paths += 1
                removed_bytes += candidate_size
                current_total_bytes = max(0, current_total_bytes - candidate_size)
                pool_is_empty = (
                    candidate.pool_root.exists()
                    and not any(self._all_slot_dirs(candidate.pool_root))
                )
                if pool_is_empty:
                    removed = self._delete_expired_pool_locked(candidate.pool_root, current_time)
                    removed_paths += removed[0]
                    removed_bytes += removed[1]
                    current_total_bytes = max(0, current_total_bytes - removed[1])

        remaining_bytes = self._path_size_bytes_via_du(self._base_dir)
        return ExecCacheCleanupSummary(
            removed_paths=removed_paths,
            removed_bytes=removed_bytes,
            remaining_bytes=remaining_bytes,
        )

    def _rmtree_with_escalation(self, path: Path) -> bool:
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                return True
        except Exception:
            pass
        if os.name == "nt":
            try:
                subprocess.run(
                    ["cmd", "/c", "rmdir", "/s", "/q", str(path)],
                    capture_output=True, timeout=30, check=False,
                )
                if not path.exists():
                    return True
            except Exception:
                pass
            with suppress(Exception):
                subprocess.run(
                    ["cmd", "/c", "del", "/f", "/s", "/q", str(path)],
                    capture_output=True, timeout=30, check=False,
                )
        else:
            try:
                subprocess.run(
                    ["rm", "-rf", str(path)],
                    capture_output=True, timeout=30, check=False,
                )
                if not path.exists():
                    return True
            except Exception:
                pass
        try:
            for child in list(path.rglob("*")):
                with suppress(OSError):
                    child.unlink(missing_ok=True)
        except Exception:
            pass
        return not path.exists()

    def _record_cleanup_success(self) -> None:
        self._consecutive_cleanup_failures = 0

    def _record_cleanup_failure(self) -> None:
        self._consecutive_cleanup_failures += 1
        if self._consecutive_cleanup_failures >= _MAX_CONSECUTIVE_CLEANUP_FAILURES:
            self._cleanup_cooldown_until_monotonic = (
                time.monotonic() + _CLEANUP_COOLDOWN_S
            )

    def _cleanup_in_cooldown(self) -> bool:
        return (
            self._consecutive_cleanup_failures >= _MAX_CONSECUTIVE_CLEANUP_FAILURES
            and time.monotonic() < self._cleanup_cooldown_until_monotonic
        )

    def _delete_paths(self, paths: list[Path]) -> None:
        for path in paths:
            if not path.exists():
                continue
            shutil.rmtree(path, ignore_errors=True)

    @contextmanager
    def _base_lock(self) -> Iterator[None]:
        self._acquire_named_lock(self._base_lock_path())
        try:
            yield
        finally:
            self._base_lock_path().unlink(missing_ok=True)

    def _acquire_named_lock(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(self._lock_timeout_s, _MIN_POOL_LOCK_TIMEOUT_S)
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise ExecSandboxBusyError(f"sandbox busy: {lock_path.parent}") from None
                time.sleep(_LOCK_POLL_INTERVAL_S)
                continue
            try:
                payload: dict[str, int | float] = {"pid": os.getpid()}
                _, started_at = _current_process_identity()
                if started_at is not None:
                    payload["started_at"] = started_at
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            return

    def _try_acquire_named_lock(self, lock_path: Path) -> bool:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            payload: dict[str, int | float] = {"pid": os.getpid()}
            _, started_at = _current_process_identity()
            if started_at is not None:
                payload["started_at"] = started_at
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)
        return True


__all__ = ["ExecCacheCleanupSummary", "ExecSandboxBusyError", "ExecSandboxManager"]
