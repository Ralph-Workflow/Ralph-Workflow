"""Workspace change probe protocol and default implementation.

Enforces black-box observation of workspace modifications without coupling
recovery or execution to hidden global state or test-specific filename markers.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.workspace.protocol import Workspace

__all__ = [
    "DefaultWorkspaceChangeProbe",
    "WorkspaceChangeProbe",
    "take_workspace_snapshot",
]


def _is_ignored_workspace_path(path: str) -> bool:
    norm = path.replace("\\", "/").strip("./")
    parts = norm.split("/")
    first = parts[0] if parts else ""
    if first in {
        ".agent",
        ".git",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }:
        return True
    return any(p.endswith((".pyc", ".pyo", ".swp", ".swo", "~")) for p in parts)


def take_workspace_snapshot(workspace: Workspace) -> dict[str, str]:
    """Capture a snapshot of workspace files and their fingerprints.

    Excludes VCS internals, temporary files, build caches, and Ralph internal
    directories (.agent, .git, checkpoint files).
    """
    snapshot: dict[str, str] = {}
    file_paths: Sequence[str]
    try:
        if hasattr(workspace, "iter_files"):
            file_paths = workspace.iter_files("")
        else:
            file_paths = [f for f in workspace.list_dir(".") if workspace.is_file(f)]
    except Exception:
        return {}

    for raw_path in sorted(file_paths):
        p = str(raw_path).replace("\\", "/").lstrip("./")
        if _is_ignored_workspace_path(p):
            continue
        try:
            st = workspace.stat(p)
            if st.get("type") != "file":
                continue
            mtime = st.get("modified_unix")
            size = st.get("size_bytes", 0)
            if mtime is not None:
                snapshot[p] = f"{size}:{mtime}"
            else:
                content = workspace.read(p)
                digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
                snapshot[p] = f"{size}:{digest}"
        except Exception:
            continue
    return snapshot


@runtime_checkable
class WorkspaceChangeProbe(Protocol):
    """Protocol for detecting workspace changes across phase execution attempts."""

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        """Capture baseline workspace state before an execution attempt."""
        ...

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        """Return True if workspace changed since observe_before was called."""
        ...


class DefaultWorkspaceChangeProbe:
    """Default workspace change probe tracking per-phase baselines without global state."""

    def __init__(self) -> None:
        self._baselines: dict[str, dict[str, str]] = {}  # bounded-accumulator-ok: keyed by phase

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        """Capture baseline snapshot before an attempt."""
        self._baselines[phase] = take_workspace_snapshot(workspace)

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        """Return True if workspace changed since observe_before was called."""
        before = self._baselines.pop(phase, None)
        after = take_workspace_snapshot(workspace)
        if before is None:
            return False
        return before != after
