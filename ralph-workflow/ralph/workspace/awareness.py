"""Bounded workspace-change awareness shared by observation and index refresh."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

_MAX_DIRTY_PATHS = 512


class WorkspaceAwareness:
    """Coalesce relevant workspace paths for one canonical workspace root."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.absolute()
        self._dirty_paths: OrderedDict[str, None] = (
            OrderedDict()
        )  # bounded-accumulator-ok: record evicts beyond _MAX_DIRTY_PATHS
        self._mode = "watch"
        self._cause: str | None = None

    def record(self, absolute_path: str) -> None:
        """Remember one relevant path, retaining only the latest bounded set."""
        try:
            relative = Path(absolute_path).absolute().relative_to(self._workspace_root)
        except ValueError:
            return
        path = relative.as_posix()
        self._dirty_paths.pop(path, None)
        self._dirty_paths[path] = None
        while len(self._dirty_paths) > _MAX_DIRTY_PATHS:
            self._dirty_paths.popitem(last=False)

    def set_live_fallback(self, cause: str) -> None:
        """Expose a failed observer without claiming indexed knowledge is current."""
        self._mode = "live_fallback"
        self._cause = cause

    def set_watch_active(self) -> None:
        """Record successful observation recovery for the shared status surface."""
        self._mode = "watch"
        self._cause = None

    def requeue(self, paths: list[str], *, cause: str) -> None:
        """Restore unacknowledged paths after a failed persistent handoff."""
        for path in paths:
            self._dirty_paths.pop(path, None)
            self._dirty_paths[path] = None
        while len(self._dirty_paths) > _MAX_DIRTY_PATHS:
            self._dirty_paths.popitem(last=False)
        self._cause = cause

    def drain(self) -> list[str]:
        """Return the coalesced dirty set once, in deterministic arrival order."""
        paths = list(self._dirty_paths)
        self._dirty_paths.clear()
        return paths

    def snapshot(self) -> dict[str, object]:
        """Return bounded status without filesystem I/O."""
        live_fallback = self._mode == "live_fallback"
        return {
            "workspace": str(self._workspace_root),
            "mode": self._mode,
            "freshness": "live_fallback"
            if live_fallback
            else ("pending" if self._dirty_paths else "current"),
            "cause": self._cause,
            "dirty_paths_count": len(self._dirty_paths),
            "automatic_recovery": live_fallback,
            "safe_next_action": (
                "Ralph will reconcile at the next knowledge boundary."
                if live_fallback
                else "None required."
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
