"""Reusable, resettable per-workspace exec sandbox pools.

`ExecSandboxManager` owns the lifecycle for the workspace-keyed sandbox pool
that MCP exec runs in. Each `acquire()` returns a clean sandbox slot selected
from a pool keyed by the workspace path hash; repeated same-workspace execs can
run concurrently by leasing different slots, while each individual slot still
resets before use and cleans up after release.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools._exec_sandbox_busy_error import ExecSandboxBusyError
from ralph.mcp.tools.exec_overlay import (
    _current_process_identity,
    _ensure_git_isolation,
    _mirror_workspace,
    _overlay_owner_metadata,
    _process_identity_matches,
    _write_overlay_owner_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_READY_FILE = ".ralph-sandbox-ready"
_LOCK_FILE = ".ralph-sandbox.lock"
_POOL_STATE_FILE = ".ralph-sandbox-pool.json"
_POOL_LOCK_FILE = ".ralph-sandbox-pool.lock"
_DEFAULT_LOCK_TIMEOUT_S = 0.1
_MIN_POOL_LOCK_TIMEOUT_S = 0.05
_LOCK_POLL_INTERVAL_S = 0.005
_KEY_LENGTH = 16
_MIN_SLOTS = 1
_DEFAULT_MAX_SLOTS = 8
_DEFAULT_MAX_WORKSPACE_POOLS = 8
_INITIAL_AVERAGE_SLOTS = 1.0
_SLOT_PREFIX = "slot-"
_BASE_LOCK_FILE = ".ralph-exec-base.lock"
_TRASH_PREFIX = ".ralph-exec-trash-"
_BASE_PRUNE_INTERVAL_S = 1.0


def _workspace_key(workspace_root: Path) -> str:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:_KEY_LENGTH]


class ExecSandboxManager:
    """Manage reusable per-workspace exec sandboxes with reset-before-run semantics."""

    def __init__(
        self,
        *,
        base_dir: Path,
        lock_timeout_s: float = _DEFAULT_LOCK_TIMEOUT_S,
        max_slots: int = _DEFAULT_MAX_SLOTS,
        max_workspace_pools: int = _DEFAULT_MAX_WORKSPACE_POOLS,
    ) -> None:
        self._base_dir = base_dir
        self._lock_timeout_s = lock_timeout_s
        self._max_slots = max(_MIN_SLOTS, max_slots)
        self._max_workspace_pools = max(_MIN_SLOTS, max_workspace_pools)
        self._last_base_prune_monotonic = 0.0
        self._managed_workspace_keys: set[str] = set()

    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        """Yield a freshly reset sandbox worktree for the given workspace."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        workspace_key = _workspace_key(workspace_root)
        pool_root = self._base_dir / workspace_key
        force_prune = (
            workspace_key not in self._managed_workspace_keys
            and len(self._managed_workspace_keys) >= self._max_workspace_pools
        )
        if self._should_prune_base(workspace_key, force_prune=force_prune):
            try:
                with self._base_lock():
                    if self._should_prune_base(workspace_key, force_prune=force_prune):
                        self._prune_stale_workspace_pools_locked(
                            current_workspace_key=workspace_key
                        )
                        self._last_base_prune_monotonic = time.monotonic()
                    pool_root.mkdir(parents=True, exist_ok=True)
            except ExecSandboxBusyError:
                if force_prune:
                    raise
                pool_root.mkdir(parents=True, exist_ok=True)
        else:
            pool_root.mkdir(parents=True, exist_ok=True)
        self._managed_workspace_keys.add(workspace_key)
        sandbox_root, _slot_index = self._lease_slot(pool_root, workspace_key)
        try:
            worktree = sandbox_root / "ws"
            self._reset(workspace_root, sandbox_root, worktree)
            try:
                yield worktree
            finally:
                self._cleanup_worktree(worktree)
        finally:
            self._release_lock(sandbox_root)

            self._shrink_idle_slots(pool_root, workspace_key)

    def _reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
        if not self._is_ready(sandbox_root):
            self._full_reset(workspace_root, sandbox_root, worktree)
            return
        if not self._fast_reset(workspace_root, sandbox_root, worktree):
            self._full_reset(workspace_root, sandbox_root, worktree)

    def _full_reset(
        self, workspace_root: Path, sandbox_root: Path, worktree: Path
    ) -> None:
        del worktree
        self._clear_sandbox_contents(sandbox_root)
        _write_overlay_owner_metadata(sandbox_root)
        rebuilt_worktree = sandbox_root / "ws"
        _mirror_workspace(workspace_root, rebuilt_worktree)
        _ensure_git_isolation(workspace_root, rebuilt_worktree, sandbox_root)
        self._write_ready_sentinel(sandbox_root)

    def _fast_reset(
        self, workspace_root: Path, sandbox_root: Path, worktree: Path
    ) -> bool:
        if not self._is_ready(sandbox_root):
            return False
        if worktree.exists():
            shutil.rmtree(worktree)
        _write_overlay_owner_metadata(sandbox_root)
        _mirror_workspace(workspace_root, worktree)
        _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        return True

    def _clear_sandbox_contents(self, sandbox_root: Path) -> None:
        sandbox_root.mkdir(parents=True, exist_ok=True)
        for child in sandbox_root.iterdir():
            if child.name == _LOCK_FILE:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)

    def _cleanup_worktree(self, worktree: Path) -> None:
        with suppress(FileNotFoundError):
            shutil.rmtree(worktree)

    def _ready_path(self, sandbox_root: Path) -> Path:
        return sandbox_root / _READY_FILE

    def _is_ready(self, sandbox_root: Path) -> bool:
        return self._ready_path(sandbox_root).is_file()

    def _write_ready_sentinel(self, sandbox_root: Path) -> None:
        self._ready_path(sandbox_root).write_text('{"ready": true}', encoding="utf-8")

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

    def _reclaim_stale_lock(self, lock_path: Path) -> bool:
        owner_pid, owner_started_at = self._lock_owner_metadata(lock_path)
        if owner_pid is None or _process_identity_matches(owner_pid, owner_started_at):
            return False
        lock_path.unlink(missing_ok=True)
        return True

    def _slot_root(self, pool_root: Path, workspace_key: str, slot_index: int) -> Path:
        return pool_root / f"{_SLOT_PREFIX}{workspace_key}-{slot_index:04d}"

    def _slot_indices(self, pool_root: Path, workspace_key: str) -> list[int]:
        prefix = f"{_SLOT_PREFIX}{workspace_key}-"
        indices: list[int] = []
        for child in pool_root.iterdir():
            if not child.is_dir():
                continue
            if not child.name.startswith(prefix):
                continue
            suffix = child.name.removeprefix(prefix)
            if suffix.isdigit():
                indices.append(int(suffix))
        return sorted(indices)

    def _default_pool_state(self) -> dict[str, float | int]:
        return {
            "average_slots": _INITIAL_AVERAGE_SLOTS,
            "target_slots": _MIN_SLOTS,
            "low_usage_streak": 0,
        }

    def _load_pool_state(self, pool_root: Path) -> dict[str, float | int]:
        state_path = self._pool_state_path(pool_root)
        if not state_path.is_file():
            return self._default_pool_state()
        try:
            raw = cast("object", json.loads(state_path.read_text(encoding="utf-8")))
        except Exception:
            return self._default_pool_state()
        if not isinstance(raw, dict):
            return self._default_pool_state()
        payload = cast("dict[str, object]", raw)
        average_slots = payload.get("average_slots", _INITIAL_AVERAGE_SLOTS)
        target_slots = payload.get("target_slots", _MIN_SLOTS)
        low_usage_streak = payload.get("low_usage_streak", 0)
        if not isinstance(average_slots, (int, float)):
            average_slots = _INITIAL_AVERAGE_SLOTS
        if not isinstance(target_slots, int):
            target_slots = _MIN_SLOTS
        if not isinstance(low_usage_streak, int):
            low_usage_streak = 0
        normalized_target = self._normalize_target_slots(target_slots)
        normalized_average = max(_INITIAL_AVERAGE_SLOTS, float(average_slots))
        return {
            "average_slots": normalized_average,
            "target_slots": normalized_target,
            "low_usage_streak": max(0, low_usage_streak),
        }

    def _save_pool_state(self, pool_root: Path, state: dict[str, float | int]) -> None:
        self._pool_state_path(pool_root).write_text(json.dumps(state), encoding="utf-8")

    def _normalize_target_slots(self, target_slots: int) -> int:
        return max(_MIN_SLOTS, min(self._max_slots, target_slots))

    def _should_prune_base(self, workspace_key: str, *, force_prune: bool) -> bool:
        if force_prune:
            return True
        del workspace_key
        return (
            time.monotonic() - self._last_base_prune_monotonic
        ) >= _BASE_PRUNE_INTERVAL_S

    def _slot_dirs(self, pool_root: Path, workspace_key: str) -> list[Path]:
        prefix = f"{_SLOT_PREFIX}{workspace_key}-"
        return [
            child
            for child in pool_root.iterdir()
            if child.is_dir() and child.name.startswith(prefix)
        ]

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

    def _slot_owned_by_current_process(self, slot_root: Path) -> bool:
        owner_pid, owner_started_at = _overlay_owner_metadata(slot_root)
        return owner_pid is not None and _process_identity_matches(
            owner_pid, owner_started_at
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

    def _pool_owned_by_current_process(self, pool_root: Path) -> bool:
        return any(
            self._slot_owned_by_current_process(slot_root)
            for slot_root in self._all_slot_dirs(pool_root)
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

    def _stage_dir_for_deletion(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        staged_path = path.with_name(
            f"{_TRASH_PREFIX}{path.name}-{os.getpid()}-{time.monotonic_ns()}"
        )
        path.replace(staged_path)
        return staged_path

    def _stage_locked_pool_dir_for_deletion(self, pool_root: Path) -> Path | None:
        if not pool_root.exists():
            return None
        if not self._try_acquire_named_lock(self._pool_lock_path(pool_root)):
            return None
        return self._stage_dir_for_deletion(pool_root)

    def _prune_stale_workspace_pools_locked(self, current_workspace_key: str) -> None:
        staged_pool_dirs: list[Path] = []
        reusable_idle_pools: list[Path] = []
        deleted_pool_keys: set[str] = set()
        for child in self._base_dir.iterdir():
            if not child.is_dir() or child.name.startswith(_TRASH_PREFIX):
                continue
            if child.name == current_workspace_key:
                continue
            if self._pool_lock_is_live(child):
                continue
            slot_dirs = self._all_slot_dirs(child)
            if not slot_dirs:
                staged_path = self._stage_locked_pool_dir_for_deletion(child)
                if staged_path is not None:
                    deleted_pool_keys.add(child.name)
                    staged_pool_dirs.append(staged_path)
                continue
            if self._pool_has_live_leases(child):
                continue
            if self._pool_owned_by_current_process(child):
                reusable_idle_pools.append(child)
                continue
            staged_path = self._stage_locked_pool_dir_for_deletion(child)
            if staged_path is not None:
                deleted_pool_keys.add(child.name)
                staged_pool_dirs.append(staged_path)

        keep_idle_pool_count = max(0, self._max_workspace_pools - 1)
        reusable_idle_pools.sort(key=self._pool_last_used_at, reverse=True)
        for child in reusable_idle_pools[keep_idle_pool_count:]:
            staged_path = self._stage_locked_pool_dir_for_deletion(child)
            if staged_path is not None:
                deleted_pool_keys.add(child.name)
                staged_pool_dirs.append(staged_path)
        self._managed_workspace_keys.difference_update(deleted_pool_keys)
        self._delete_paths(staged_pool_dirs)

    def _lease_slot(self, pool_root: Path, workspace_key: str) -> tuple[Path, int]:
        staged_stale_slot_dirs: list[Path] = []
        leased_slot: tuple[Path, int] | None = None
        with self._pool_lock(pool_root):
            state = self._load_pool_state(pool_root)
            target_slots = int(state["target_slots"])
            existing_indices = [
                slot_index
                for slot_index in self._slot_indices(pool_root, workspace_key)
                if slot_index <= self._max_slots
            ]
            warm_slots = max(target_slots, max(existing_indices, default=0))
            for slot_index in range(1, warm_slots + 1):
                slot_root = self._slot_root(pool_root, workspace_key, slot_index)
                if self._try_acquire_lock(slot_root):
                    leased_slot = (slot_root, slot_index)
                    break
            if leased_slot is None:
                if warm_slots >= self._max_slots:
                    raise ExecSandboxBusyError(f"sandbox busy: {pool_root}")
                slot_index = warm_slots + 1
                while True:
                    if slot_index > self._max_slots:
                        raise ExecSandboxBusyError(f"sandbox busy: {pool_root}")
                    slot_root = self._slot_root(pool_root, workspace_key, slot_index)
                    if self._try_acquire_lock(slot_root):
                        leased_slot = (slot_root, slot_index)
                        break
                    slot_index += 1
            assert leased_slot is not None
            stale_slot_dirs = [
                path for path in self._stale_slot_dirs(pool_root) if path != leased_slot[0]
            ]
            for stale_slot_dir in stale_slot_dirs:
                staged_path = self._stage_locked_slot_dir_for_deletion(stale_slot_dir)
                if staged_path is not None:
                    staged_stale_slot_dirs.append(staged_path)
            self._record_slot_usage_locked(pool_root, leased_slot[1])

        leased_root, leased_index = leased_slot
        self._delete_paths(staged_stale_slot_dirs)
        return leased_root, leased_index

    def _record_slot_usage_locked(self, pool_root: Path, slot_index: int) -> None:
        state = self._load_pool_state(pool_root)
        average_slots = float(state["average_slots"])
        updated_average = (average_slots + slot_index) / 2.0
        updated_target = self._normalize_target_slots(
            max(_MIN_SLOTS, round(updated_average), slot_index)
        )
        low_usage_streak = int(state["low_usage_streak"])
        if slot_index == 1:
            low_usage_streak += 1
        else:
            low_usage_streak = 0
        self._save_pool_state(
            pool_root,
            {
                "average_slots": updated_average,
                "target_slots": updated_target,
                "low_usage_streak": low_usage_streak,
            },
        )

    def _shrink_idle_slots(self, pool_root: Path, workspace_key: str) -> None:
        staged_slot_dirs_to_delete: list[Path] = []
        with self._pool_lock(pool_root):
            state = self._load_pool_state(pool_root)
            target_slots = int(state["target_slots"])
            average_slots = float(state["average_slots"])
            low_usage_streak = int(state["low_usage_streak"])
            if target_slots > _MIN_SLOTS and low_usage_streak >= 2:
                average_slots = (average_slots + _MIN_SLOTS) / 2.0
                target_slots = self._normalize_target_slots(round(average_slots))
                state = {
                    "average_slots": average_slots,
                    "target_slots": target_slots,
                    "low_usage_streak": 0,
                }
                self._save_pool_state(pool_root, state)

            target_slots = self._normalize_target_slots(target_slots)
            for slot_index in reversed(self._slot_indices(pool_root, workspace_key)):
                if slot_index <= target_slots and slot_index <= self._max_slots:
                    continue
                slot_root = self._slot_root(pool_root, workspace_key, slot_index)
                staged_path = self._stage_locked_slot_dir_for_deletion(slot_root)
                if staged_path is not None:
                    staged_slot_dirs_to_delete.append(staged_path)

        self._delete_paths(staged_slot_dirs_to_delete)

    def _stale_slot_dirs(self, pool_root: Path) -> list[Path]:
        stale_slot_dirs: list[Path] = []
        for child in pool_root.iterdir():
            if not child.is_dir():
                continue
            owner_pid, owner_started_at = _overlay_owner_metadata(child)
            if owner_pid is None or _process_identity_matches(owner_pid, owner_started_at):
                continue
            stale_slot_dirs.append(child)
        return stale_slot_dirs

    def _stage_locked_slot_dir_for_deletion(self, slot_dir: Path) -> Path | None:
        if not slot_dir.exists():
            return None
        if not self._try_acquire_lock(slot_dir):
            return None
        return self._stage_dir_for_deletion(slot_dir)

    def _delete_paths(self, paths: list[Path]) -> None:
        for path in paths:
            if not path.exists():
                continue
            shutil.rmtree(path)

    def _release_lock(self, sandbox_root: Path) -> None:
        self._lock_path(sandbox_root).unlink(missing_ok=True)

    @contextmanager
    def _pool_lock(self, pool_root: Path) -> Iterator[None]:
        self._acquire_named_lock(self._pool_lock_path(pool_root))
        try:
            yield
        finally:
            self._pool_lock_path(pool_root).unlink(missing_ok=True)

    @contextmanager
    def _base_lock(self) -> Iterator[None]:
        self._acquire_named_lock(self._base_lock_path())
        try:
            yield
        finally:
            self._base_lock_path().unlink(missing_ok=True)

    def _try_acquire_lock(self, sandbox_root: Path) -> bool:
        lock_path = self._lock_path(sandbox_root)
        sandbox_root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if self._reclaim_stale_lock(lock_path):
                return self._try_acquire_lock(sandbox_root)
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

    def _acquire_named_lock(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(self._lock_timeout_s, _MIN_POOL_LOCK_TIMEOUT_S)
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._reclaim_stale_lock(lock_path):
                    continue
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
            if self._reclaim_stale_lock(lock_path):
                return self._try_acquire_named_lock(lock_path)
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


__all__ = ["ExecSandboxBusyError", "ExecSandboxManager"]
