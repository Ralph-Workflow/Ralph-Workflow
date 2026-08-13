"""Bounded workspace-change awareness shared by observation and index refresh."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

_MAX_DIRTY_PATHS = 512
_FRESHNESS_STATES = frozenset(
    {"current", "pending", "partial", "stale", "unavailable", "live_fallback"}
)


class WorkspaceAwareness:
    """Coalesce relevant workspace paths for one canonical workspace root."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.absolute()
        self._dirty_paths: OrderedDict[str, None] = (
            OrderedDict()
        )  # bounded-accumulator-ok: record evicts beyond _MAX_DIRTY_PATHS
        self._mode = "watch"
        self._cause: str | None = None
        self._freshness_override: str | None = None
        self._lock = threading.Lock()

    def record(self, absolute_path: str) -> None:
        """Remember one relevant path, retaining only the latest bounded set."""
        try:
            relative = Path(absolute_path).absolute().relative_to(self._workspace_root)
        except ValueError:
            return
        path = relative.as_posix()
        with self._lock:
            self._dirty_paths.pop(path, None)
            self._dirty_paths[path] = None
            while len(self._dirty_paths) > _MAX_DIRTY_PATHS:
                self._dirty_paths.popitem(last=False)

    def set_live_fallback(self, cause: str) -> None:
        """Expose a failed observer without claiming indexed knowledge is current."""
        with self._lock:
            self._mode = "live_fallback"
            self._cause = cause
            self._freshness_override = None

    def set_watch_active(self) -> None:
        """Record successful observation recovery for the shared status surface."""
        with self._lock:
            self._mode = "watch"
            self._cause = None
            self._freshness_override = None

    def set_freshness(self, freshness: str, *, cause: str | None = None) -> None:
        """Expose a non-current knowledge state without filesystem work."""
        if freshness not in _FRESHNESS_STATES - {"live_fallback"}:
            raise ValueError(f"unsupported workspace freshness: {freshness}")
        with self._lock:
            self._freshness_override = None if freshness == "current" else freshness
            self._cause = cause

    def requeue(self, paths: list[str], *, cause: str) -> None:
        """Restore unacknowledged paths after a failed persistent handoff."""
        with self._lock:
            for path in paths:
                self._dirty_paths.pop(path, None)
                self._dirty_paths[path] = None
            while len(self._dirty_paths) > _MAX_DIRTY_PATHS:
                self._dirty_paths.popitem(last=False)
            self._cause = cause
            self._freshness_override = "stale"

    def record_relative(self, relative_path: str) -> None:
        """Remember one workspace-relative path from the shared owner sidecar.

        The path is coalesced with locally observed changes and marks the
        knowledge state non-current until drained into the persisted dirty
        queue (S-2: a non-owner must durably claim owner-published changes
        before claiming current freshness).
        """
        path = relative_path.strip("/")
        if not path or path.startswith(".."):
            return
        with self._lock:
            self._dirty_paths.pop(path, None)
            self._dirty_paths[path] = None
            while len(self._dirty_paths) > _MAX_DIRTY_PATHS:
                self._dirty_paths.popitem(last=False)
            if self._freshness_override is None:
                self._freshness_override = "pending"

    def drain(self) -> list[str]:
        """Return the coalesced dirty set once, in deterministic arrival order."""
        with self._lock:
            paths = list(self._dirty_paths)
            self._dirty_paths.clear()
            return paths

    def snapshot(self) -> dict[str, object]:
        """Return bounded status without filesystem I/O."""
        with self._lock:
            live_fallback = self._mode == "live_fallback"
            freshness = (
                "live_fallback"
                if live_fallback
                else (self._freshness_override or ("pending" if self._dirty_paths else "current"))
            )
            return {
                "workspace": str(self._workspace_root),
                "mode": self._mode,
                "freshness": freshness,
                "cause": self._cause,
                "dirty_paths_count": len(self._dirty_paths),
                "automatic_recovery": live_fallback,
                "safe_next_action": (
                    "Ralph will reconcile at the next knowledge boundary."
                    if live_fallback
                    else ("Run a bounded refresh before relying on indexed knowledge."
                          if freshness != "current" else "None required.")
                ),
            }


_awareness_by_workspace: dict[
    str, WorkspaceAwareness
] = {}  # bounded-accumulator-ok: release_workspace removes final lease state
_awareness_lock = threading.Lock()


def awareness_for_workspace(workspace_root: Path) -> WorkspaceAwareness:
    """Return the canonical process-local awareness owner for ``workspace_root``."""
    key = str(workspace_root.absolute())
    with _awareness_lock:
        awareness = _awareness_by_workspace.get(key)
        if awareness is None:
            awareness = WorkspaceAwareness(workspace_root)
            _awareness_by_workspace[key] = awareness
        return awareness


def release_workspace_awareness(workspace_root: Path) -> None:
    """Release derived state once its final watcher lease closes."""
    with _awareness_lock:
        _awareness_by_workspace.pop(str(workspace_root.absolute()), None)


__all__ = ["WorkspaceAwareness", "awareness_for_workspace", "release_workspace_awareness"]
