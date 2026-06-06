"""Reusable, resettable per-workspace exec sandbox pools.

`ExecSandboxManager` owns the lifecycle for the workspace-keyed sandbox pool
that MCP exec runs in. Each `acquire()` returns a clean sandbox slot selected
via lock-free per-workspace round-robin. Repeated same-workspace execs can run
concurrently by using different (or the same) physical slot directories; each
acquire resets the slot before use. No filesystem locks are created or waited on.

Cleanup runs only under capacity pressure: if base_dir usage exceeds
max_total_bytes, reclaimable garbage is removed while preserving in-process
active slots. Under-budget garbage is left in place.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

from ralph.mcp.tools._exec_cache_cleanup_summary import ExecCacheCleanupSummary
from ralph.mcp.tools._exec_execution_error import ExecutionError
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

_READY_FILE = ".ralph-sandbox-ready"
_SLOT_PREFIX = "slot-"
_TRASH_PREFIX = ".ralph-exec-trash-"
_KEY_LENGTH = 16
_MIN_SLOTS = 1
_DEFAULT_MAX_SLOTS = 8
_DEFAULT_MAX_WORKSPACE_POOLS = 8
_DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_POOL_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
_WORKSPACE_SIZE_CACHE_TTL_S = 60.0
_FORCED_MAX_SLOT_AGE_S = 24 * 60 * 60.0


def _workspace_key(workspace_root: Path) -> str:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:_KEY_LENGTH]


def _compute_sandbox_limits(workspace_size_bytes: int) -> tuple[int, int, int]:
    """Return (max_total_bytes, max_pool_bytes, max_workspace_bytes) scaled to workspace size."""
    if workspace_size_bytes <= 0:
        workspace_size_bytes = _DEFAULT_MAX_TOTAL_BYTES
    min_desired_slots = 4
    slot_bytes = int(workspace_size_bytes * 1.2)
    pool_bytes = max(_DEFAULT_MAX_POOL_BYTES, slot_bytes * min_desired_slots)
    total_bytes = max(_DEFAULT_MAX_TOTAL_BYTES, pool_bytes * 2)
    workspace_safety_bytes = max(_DEFAULT_MAX_WORKSPACE_BYTES, workspace_size_bytes * 6)
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
                        with suppress(OSError):
                            total += entry.stat().st_size
        except OSError:
            pass

    _scan_dir(workspace_root)
    return total


class ExecSandboxManager:
    """Manage reusable per-workspace exec sandboxes with lock-free bounded round-robin.

    Each acquire() selects the next slot via a per-workspace round-robin counter
    bounded by max_slots. Two concurrent acquires may use the same physical slot
    directory; each resets the slot before use. No filesystem locks are created
    or waited on.

    Cleanup runs only under capacity pressure: when base_dir usage exceeds
    max_total_bytes, reclaimable paths are staged and deleted while preserving
    slots currently active in this Python process.
    """

    def __init__(
        self,
        *,
        base_dir: Path,
        max_slots: int = _DEFAULT_MAX_SLOTS,
        max_workspace_pools: int = _DEFAULT_MAX_WORKSPACE_POOLS,
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
        max_pool_bytes: int = _DEFAULT_MAX_POOL_BYTES,
        max_workspace_bytes: int = _DEFAULT_MAX_WORKSPACE_BYTES,
    ) -> None:
        self._base_dir = base_dir
        self._max_slots = max(_MIN_SLOTS, max_slots)
        self._max_workspace_pools = max(_MIN_SLOTS, max_workspace_pools)
        self._max_total_bytes = max(0, max_total_bytes)
        self._max_workspace_bytes = max(0, max_workspace_bytes)
        self._rr_lock = threading.Lock()
        self._next_slot_index: dict[str, int] = {}
        self._active_slots: set[Path] = set()
        self._workspace_size_cache: dict[str, tuple[int, float]] = {}
        self._managed_workspace_keys: set[str] = set()

    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        """Yield a freshly reset sandbox worktree for the given workspace."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        workspace_key = _workspace_key(workspace_root)
        pool_root = self._base_dir / workspace_key
        pool_root.mkdir(parents=True, exist_ok=True)
        self._managed_workspace_keys.add(workspace_key)

        self._ensure_capacity_or_recover(workspace_root)

        workspace_bytes = self._workspace_size_bytes(workspace_root)
        if workspace_bytes > self._max_workspace_bytes:
            raise OSError(
                f"sandbox workspace exceeds safety limit: "
                f"{workspace_bytes} bytes > {self._max_workspace_bytes} bytes"
            )

        slot_root = self._select_slot(workspace_key, pool_root)

        with self._rr_lock:
            self._active_slots.add(slot_root)
        try:
            worktree = slot_root / "ws"
            self._reset(workspace_root, slot_root, worktree)
            yield worktree
        finally:
            with self._rr_lock:
                self._active_slots.discard(slot_root)

    def _select_slot(self, workspace_key: str, pool_root: Path) -> Path:
        """Choose the next slot via round-robin; create slot dir if absent."""
        with self._rr_lock:
            counter = self._next_slot_index.get(workspace_key, 0)
            slot_index = (counter % self._max_slots) + 1
            self._next_slot_index[workspace_key] = counter + 1
        slot_root = pool_root / f"{_SLOT_PREFIX}{workspace_key}-{slot_index:04d}"
        slot_root.mkdir(parents=True, exist_ok=True)
        return slot_root

    def _ensure_capacity_or_recover(self, workspace_root: Path) -> None:
        """Run cleanup only if base_dir usage exceeds max_total_bytes."""
        if self._max_total_bytes <= 0 or not self._base_dir.exists():
            return
        current_bytes = self._path_size_bytes_via_du(self._base_dir)
        if current_bytes <= self._max_total_bytes:
            return
        workspace_key = _workspace_key(workspace_root)
        self._recover_over_capacity(workspace_key, current_bytes)

    def _recover_over_capacity(self, current_workspace_key: str, pre_bytes: int) -> None:
        """Remove reclaimable base-dir entries when over budget.

        Preserves any slot root currently registered in _active_slots.
        Tolerates concurrent missing/reappearing paths gracefully.
        """
        with self._rr_lock:
            active_now = frozenset(self._active_slots)

        staged_dirs: list[Path] = []
        staged_files: list[Path] = []

        if self._base_dir.exists():
            try:
                for child in list(self._base_dir.iterdir()):
                    self._stage_child_for_recovery(
                        child, current_workspace_key, active_now, staged_dirs, staged_files
                    )
            except OSError:
                pass

        removed_paths = 0
        for f in staged_files:
            with suppress(OSError):
                f.unlink(missing_ok=True)
                removed_paths += 1
        for d in staged_dirs:
            if d.exists():
                self._rmtree_with_escalation(d)
                removed_paths += 1

        post_bytes = self._path_size_bytes_via_du(self._base_dir)
        removed_bytes = max(0, pre_bytes - post_bytes)

        if post_bytes <= self._max_total_bytes:
            return

        # Active slots from concurrent acquires in this process legitimately use space.
        # Return without error — capacity will be reclaimed when those slots finish.
        if active_now:
            return

        diag = (
            f"current={post_bytes} bytes, cap={self._max_total_bytes} bytes, "
            f"removed_paths={removed_paths}, removed_bytes={removed_bytes}, "
            f"active_slots={len(active_now)}"
        )
        raise ExecutionError(
            "exec cache still over capacity after automatic reset",
            current_bytes=post_bytes,
            cap_bytes=self._max_total_bytes,
            removed_paths=removed_paths,
            removed_bytes=removed_bytes,
            remaining_bytes=post_bytes,
            diagnostics=diag,
        )

    def _stage_child_for_recovery(
        self,
        child: Path,
        current_workspace_key: str,
        active_slots: frozenset[Path],
        staged_dirs: list[Path],
        staged_files: list[Path],
    ) -> None:
        name = child.name
        # Trash dirs from previous recovery passes
        if name.startswith(_TRASH_PREFIX):
            staged_dirs.append(child)
            return
        if not child.is_dir(follow_symlinks=False):
            if child.is_file(follow_symlinks=False):
                staged_files.append(child)
            return
        is_pool = len(name) == _KEY_LENGTH and all(c in "0123456789abcdef" for c in name)
        if not is_pool:
            # Unknown dir at base level: stage for deletion
            staged_path = self._stage_dir_for_deletion(child)
            if staged_path is not None:
                staged_dirs.append(staged_path)
            return
        if name == current_workspace_key:
            self._recover_current_pool(child, active_slots, staged_dirs)
        elif not any(s.is_relative_to(child) for s in active_slots):
                staged_path = self._stage_dir_for_deletion(child)
                if staged_path is not None:
                    staged_dirs.append(staged_path)
                    self._managed_workspace_keys.discard(name)

    def _recover_current_pool(
        self,
        pool_root: Path,
        active_slots: frozenset[Path],
        staged_dirs: list[Path],
    ) -> None:
        """Stage non-active slot dirs from the current workspace pool for deletion."""
        if not pool_root.exists():
            return
        try:
            for child in list(pool_root.iterdir()):
                if not child.is_dir(follow_symlinks=False):
                    continue
                if not child.name.startswith(_SLOT_PREFIX):
                    continue
                if child in active_slots:
                    continue
                staged_path = self._stage_dir_for_deletion(child)
                if staged_path is not None:
                    staged_dirs.append(staged_path)
        except OSError:
            pass

    def cleanup_base(self) -> ExecCacheCleanupSummary:
        """Remove stale/orphaned cache entries; does not acquire filesystem locks."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        with self._rr_lock:
            active_now = frozenset(self._active_slots)

        removed_paths = 0

        # Remove trash dirs left by prior recovery passes
        try:
            for child in list(self._base_dir.iterdir()):
                if child.name.startswith(_TRASH_PREFIX):
                    self._rmtree_with_escalation(child)
                    removed_paths += 1
        except OSError:
            pass

        # Remove slot dirs whose owner process is dead
        current_time = time.time()
        try:
            for pool_root in list(self._base_dir.iterdir()):
                if not pool_root.is_dir() or pool_root.name.startswith(_TRASH_PREFIX):
                    continue
                try:
                    for slot_root in self._all_slot_dirs(pool_root):
                        if slot_root in active_now:
                            continue
                        if self._slot_owner_is_dead(slot_root, current_time):
                            self._rmtree_with_escalation(slot_root)
                            removed_paths += 1
                except OSError:
                    continue
        except OSError:
            pass

        remaining = self._path_size_bytes_via_du(self._base_dir)
        return ExecCacheCleanupSummary(
            removed_paths=removed_paths,
            removed_bytes=0,
            remaining_bytes=remaining,
        )

    def _slot_owner_is_dead(self, slot_root: Path, current_time: float) -> bool:
        """Return True if the slot's owner process is dead or the slot is older than 24h."""
        try:
            slot_mtime = slot_root.stat().st_mtime
            if current_time - slot_mtime > _FORCED_MAX_SLOT_AGE_S:
                return True
        except OSError:
            return True
        owner_pid, owner_started_at = _overlay_owner_metadata(slot_root)
        if owner_pid is None:
            return True
        return not _process_identity_matches(owner_pid, owner_started_at)

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
        _write_overlay_owner_metadata(sandbox_root)
        try:
            _mirror_workspace(workspace_root, worktree)
            _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        except Exception:
            self._ready_path(sandbox_root).unlink(missing_ok=True)
            raise
        self._write_ready_sentinel(sandbox_root)
        return True

    def _clear_sandbox_contents(self, sandbox_root: Path) -> None:
        sandbox_root.mkdir(parents=True, exist_ok=True)
        for child in sandbox_root.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    def _ready_path(self, sandbox_root: Path) -> Path:
        return sandbox_root / _READY_FILE

    def _is_ready(self, sandbox_root: Path) -> bool:
        return self._ready_path(sandbox_root).is_file()

    def _write_ready_sentinel(self, sandbox_root: Path) -> None:
        self._ready_path(sandbox_root).write_text('{"ready": true}', encoding="utf-8")

    def _all_slot_dirs(self, pool_root: Path) -> list[Path]:
        return [
            child
            for child in pool_root.iterdir()
            if child.is_dir() and child.name.startswith(_SLOT_PREFIX)
        ]

    def _path_size_bytes(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        stack: list[Path] = [path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(current / entry.name)
                        elif entry.is_file(follow_symlinks=False):
                            with suppress(OSError):
                                total += entry.stat().st_size
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
        if not children:
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
        key = str(workspace_root)
        now = time.monotonic()
        if key in self._workspace_size_cache:
            cached_size, cached_at = self._workspace_size_cache[key]
            if now - cached_at < _WORKSPACE_SIZE_CACHE_TTL_S:
                return cached_size
        size = _compute_workspace_size_bytes(workspace_root)
        self._workspace_size_cache[key] = (size, now)
        return size

    def _stage_dir_for_deletion(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        staged_path = path.with_name(
            f"{_TRASH_PREFIX}{path.name}-{os.getpid()}-{time.monotonic_ns()}"
        )
        try:
            path.replace(staged_path)
        except OSError:
            return None
        return staged_path

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
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if not path.exists():
                    return True
            except Exception:
                pass
            with suppress(Exception):
                subprocess.run(
                    ["cmd", "/c", "del", "/f", "/s", "/q", str(path)],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
        else:
            try:
                subprocess.run(
                    ["rm", "-rf", str(path)],
                    capture_output=True,
                    timeout=30,
                    check=False,
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

    def _delete_paths(self, paths: list[Path]) -> None:
        for path in paths:
            if not path.exists():
                continue
            with suppress(FileNotFoundError):
                shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "ExecCacheCleanupSummary",
    "ExecSandboxManager",
    "_compute_sandbox_limits",
    "_compute_workspace_size_bytes",
    "_current_process_identity",
    "_workspace_key",
]
