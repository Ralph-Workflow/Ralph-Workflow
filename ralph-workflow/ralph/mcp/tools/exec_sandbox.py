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
    _ensure_git_isolation,
    _mirror_workspace,
    _overlay_owner_pid,
    _pid_is_running,
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
_LOCK_POLL_INTERVAL_S = 0.005
_KEY_LENGTH = 16
_MIN_SLOTS = 1
_INITIAL_AVERAGE_SLOTS = 1.0
_SLOT_PREFIX = "slot-"


def _workspace_key(workspace_root: Path) -> str:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:_KEY_LENGTH]


class ExecSandboxManager:
    """Manage reusable per-workspace exec sandboxes with reset-before-run semantics."""

    def __init__(self, *, base_dir: Path, lock_timeout_s: float = _DEFAULT_LOCK_TIMEOUT_S) -> None:
        self._base_dir = base_dir
        self._lock_timeout_s = lock_timeout_s

    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        """Yield a freshly reset sandbox worktree for the given workspace."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        workspace_key = _workspace_key(workspace_root)
        pool_root = self._base_dir / workspace_key
        pool_root.mkdir(parents=True, exist_ok=True)
        sandbox_root, slot_index = self._lease_slot(pool_root, workspace_key)
        try:
            worktree = sandbox_root / "ws"
            self._reset(workspace_root, sandbox_root, worktree)
            self._record_slot_usage(pool_root, slot_index)
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

    def _full_reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
        del worktree
        self._clear_sandbox_contents(sandbox_root)
        _write_overlay_owner_metadata(sandbox_root)
        rebuilt_worktree = sandbox_root / "ws"
        _mirror_workspace(workspace_root, rebuilt_worktree)
        _ensure_git_isolation(workspace_root, rebuilt_worktree, sandbox_root)
        self._write_ready_sentinel(sandbox_root)

    def _fast_reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> bool:
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

    def _lock_owner_pid(self, lock_path: Path) -> int | None:
        if not lock_path.is_file():
            return None
        try:
            raw = cast("object", json.loads(lock_path.read_text(encoding="utf-8")))
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        payload = cast("dict[str, object]", raw)
        pid = payload.get("pid")
        return pid if isinstance(pid, int) else None

    def _reclaim_stale_lock(self, lock_path: Path) -> bool:
        owner_pid = self._lock_owner_pid(lock_path)
        if owner_pid is None or _pid_is_running(owner_pid):
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
        normalized_target = max(_MIN_SLOTS, target_slots)
        normalized_average = max(_INITIAL_AVERAGE_SLOTS, float(average_slots))
        return {
            "average_slots": normalized_average,
            "target_slots": normalized_target,
            "low_usage_streak": max(0, low_usage_streak),
        }

    def _save_pool_state(self, pool_root: Path, state: dict[str, float | int]) -> None:
        self._pool_state_path(pool_root).write_text(json.dumps(state), encoding="utf-8")

    def _lease_slot(self, pool_root: Path, workspace_key: str) -> tuple[Path, int]:
        with self._pool_lock(pool_root):
            self._prune_stale_slot_dirs(pool_root)
            state = self._load_pool_state(pool_root)
            target_slots = int(state["target_slots"])
            existing_indices = self._slot_indices(pool_root, workspace_key)
            warm_slots = max(target_slots, max(existing_indices, default=0))
            for slot_index in range(1, warm_slots + 1):
                slot_root = self._slot_root(pool_root, workspace_key, slot_index)
                if self._try_acquire_lock(slot_root):
                    return slot_root, slot_index
            slot_index = warm_slots + 1
            while True:
                slot_root = self._slot_root(pool_root, workspace_key, slot_index)
                if self._try_acquire_lock(slot_root):
                    return slot_root, slot_index
                slot_index += 1

    def _record_slot_usage(self, pool_root: Path, slot_index: int) -> None:
        with self._pool_lock(pool_root):
            state = self._load_pool_state(pool_root)
            average_slots = float(state["average_slots"])
            updated_average = (average_slots + slot_index) / 2.0
            updated_target = max(_MIN_SLOTS, round(updated_average), slot_index)
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
        with self._pool_lock(pool_root):
            if self._has_active_slot_locks(pool_root, workspace_key):
                return

            state = self._load_pool_state(pool_root)
            target_slots = int(state["target_slots"])
            average_slots = float(state["average_slots"])
            low_usage_streak = int(state["low_usage_streak"])
            if target_slots > _MIN_SLOTS and low_usage_streak >= 2:
                average_slots = (average_slots + _MIN_SLOTS) / 2.0
                target_slots = max(_MIN_SLOTS, round(average_slots))
                state = {
                    "average_slots": average_slots,
                    "target_slots": target_slots,
                    "low_usage_streak": 0,
                }
                self._save_pool_state(pool_root, state)

            for slot_index in reversed(self._slot_indices(pool_root, workspace_key)):
                if slot_index <= target_slots:
                    continue
                slot_root = self._slot_root(pool_root, workspace_key, slot_index)
                if self._lock_path(slot_root).exists():
                    continue
                shutil.rmtree(slot_root, ignore_errors=True)

    def _has_active_slot_locks(self, pool_root: Path, workspace_key: str) -> bool:
        for slot_index in self._slot_indices(pool_root, workspace_key):
            slot_root = self._slot_root(pool_root, workspace_key, slot_index)
            if self._lock_path(slot_root).exists():
                return True
        return False

    def _prune_stale_slot_dirs(self, pool_root: Path) -> None:
        for child in pool_root.iterdir():
            if not child.is_dir():
                continue
            owner_pid = _overlay_owner_pid(child)
            if owner_pid is None or _pid_is_running(owner_pid):
                continue
            self._release_lock(child)
            shutil.rmtree(child, ignore_errors=True)

    def _acquire_lock(self, sandbox_root: Path) -> None:
        lock_path = self._lock_path(sandbox_root)
        sandbox_root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._lock_timeout_s
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise ExecSandboxBusyError(f"sandbox busy: {sandbox_root}") from None
                time.sleep(_LOCK_POLL_INTERVAL_S)
                continue
            os.close(fd)
            return

    def _release_lock(self, sandbox_root: Path) -> None:
        self._lock_path(sandbox_root).unlink(missing_ok=True)

    @contextmanager
    def _pool_lock(self, pool_root: Path) -> Iterator[None]:
        self._acquire_named_lock(self._pool_lock_path(pool_root))
        try:
            yield
        finally:
            self._pool_lock_path(pool_root).unlink(missing_ok=True)

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
            payload: dict[str, int] = {"pid": os.getpid()}
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _acquire_named_lock(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._lock_timeout_s
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
                payload: dict[str, int] = {"pid": os.getpid()}
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            return


__all__ = ["ExecSandboxBusyError", "ExecSandboxManager"]
