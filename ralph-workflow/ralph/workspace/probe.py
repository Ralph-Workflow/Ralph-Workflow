"""Workspace change probe protocol and default implementation.

Enforces black-box observation of workspace modifications without coupling
recovery or execution to hidden global state or test-specific filename markers.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.workspace.awareness import _awareness_by_workspace, _awareness_lock

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.workspace.protocol import Workspace

__all__ = [
    "DEFAULT_MAX_SNAPSHOT_FILES",
    "DefaultWorkspaceChangeProbe",
    "WorkspaceChangeProbe",
    "take_workspace_snapshot",
]

DEFAULT_MAX_SNAPSHOT_FILES: int = 1000

_IGNORED_ROOT_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".agent",
        ".git",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
_IGNORED_FILE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".swp", ".swo", "~")


def _normalize_workspace_path(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    while p.startswith("/"):
        p = p[1:]
    return p


def _is_ignored_workspace_path(path: str) -> bool:
    norm = _normalize_workspace_path(path)
    parts = norm.split("/")
    first = parts[0] if parts else ""
    if first in _IGNORED_ROOT_DIRECTORIES:
        return True
    if any(part in _IGNORED_ROOT_DIRECTORIES for part in parts[:-1]):
        return True
    return any(p.endswith(_IGNORED_FILE_SUFFIXES) for p in parts)


@runtime_checkable
class _HasVersionCallable(Protocol):
    def version(self) -> object: ...


@runtime_checkable
class _HasVersionProp(Protocol):
    @property
    def version(self) -> object: ...


def _extract_workspace_version(workspace: Workspace) -> str | None:
    if isinstance(workspace, _HasVersionCallable):
        with suppress(Exception):
            val = workspace.version()
            if val is not None:
                return str(val)
    if isinstance(workspace, _HasVersionProp):
        with suppress(Exception):
            val = workspace.version
            if val is not None:
                return str(val)
    return None


@runtime_checkable
class _HasPeekDirtyPaths(Protocol):
    def peek_dirty_paths(self) -> Iterable[object]: ...


@runtime_checkable
class _HasDirtyPathsCallable(Protocol):
    def dirty_paths(self) -> Iterable[object]: ...


@runtime_checkable
class _HasDirtyPathsAttr(Protocol):
    @property
    def dirty_paths(self) -> Iterable[object]: ...


@runtime_checkable
class _HasInternalDirtyPaths(Protocol):
    @property
    def _dirty_paths(self) -> Iterable[object]: ...


def _extract_from_candidate(candidate: object) -> list[str] | None:
    if isinstance(candidate, _HasPeekDirtyPaths):
        with suppress(Exception):
            return [str(p) for p in candidate.peek_dirty_paths()]
    if isinstance(candidate, _HasDirtyPathsCallable):
        with suppress(Exception):
            return [str(p) for p in candidate.dirty_paths()]
    if isinstance(candidate, _HasDirtyPathsAttr):
        with suppress(Exception):
            return [str(p) for p in candidate.dirty_paths]
    if isinstance(candidate, _HasInternalDirtyPaths):
        with suppress(Exception):
            return [str(p) for p in candidate._dirty_paths]
    return None


@runtime_checkable
class _HasExploreIndex(Protocol):
    @property
    def explore_index(self) -> object: ...


@runtime_checkable
class _HasStore(Protocol):
    @property
    def store(self) -> object: ...


@runtime_checkable
class _HasAwareness(Protocol):
    @property
    def awareness(self) -> object: ...


@runtime_checkable
class _HasRoot(Protocol):
    @property
    def root(self) -> object: ...


def _extract_tracked_dirty_paths(workspace: Workspace) -> list[str] | None:
    res = _extract_from_candidate(workspace)
    if res is not None:
        return res

    if isinstance(workspace, _HasExploreIndex):
        exp = workspace.explore_index
        if isinstance(exp, _HasStore):
            res = _extract_from_candidate(exp.store)
            if res is not None:
                return res
        res = _extract_from_candidate(exp)
        if res is not None:
            return res

    if isinstance(workspace, _HasAwareness):
        res = _extract_from_candidate(workspace.awareness)
        if res is not None:
            return res

    if isinstance(workspace, _HasRoot):
        root = workspace.root
        if isinstance(root, (Path, str)):
            with suppress(Exception):
                key = str(Path(root).absolute())
                with _awareness_lock:
                    active = _awareness_by_workspace.get(key)
                if active is not None:
                    return _extract_from_candidate(active)

    return None


@runtime_checkable
class _HasIterFilesBounded(Protocol):
    def iter_files_bounded(self, base: str, *, limit: int) -> Iterable[object]: ...


def _collect_file_paths(workspace: Workspace, limit: int) -> list[str]:
    if isinstance(workspace, _HasIterFilesBounded):
        with suppress(Exception):
            return sorted(str(p) for p in workspace.iter_files_bounded("", limit=limit))
    try:
        result: list[str] = []
        for item in workspace.iter_files(""):
            norm = _normalize_workspace_path(str(item))
            if _is_ignored_workspace_path(norm):
                continue
            result.append(norm)
            if len(result) >= limit:
                break
        return sorted(result)
    except Exception:
        pass
    try:
        result = []
        for item in workspace.list_dir("."):
            norm = _normalize_workspace_path(str(item))
            if _is_ignored_workspace_path(norm):
                continue
            if workspace.is_file(norm):
                result.append(norm)
                if len(result) >= limit:
                    break
        return sorted(result)
    except Exception:
        return []


def _fingerprint_file(workspace: Workspace, path: str) -> str | None:
    try:
        st = workspace.stat(path)
        if st.get("type") != "file":
            return None
        mtime = st.get("modified_unix")
        size = st.get("size_bytes", 0)
        if mtime is not None:
            return f"{size}:{mtime}"
        content = workspace.read(path)
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        return f"{size}:{digest}"
    except Exception:
        return None


def take_workspace_snapshot(
    workspace: Workspace,
    *,
    max_files: int = DEFAULT_MAX_SNAPSHOT_FILES,
) -> dict[str, str]:
    """Capture a snapshot of workspace files and their fingerprints.

    Excludes VCS internals, temporary files, build caches, and Ralph internal
    directories (.agent, .git, checkpoint files).
    """
    snapshot: dict[str, str] = {}
    ver = _extract_workspace_version(workspace)
    if ver is not None:
        snapshot["__workspace_version__"] = ver

    tracked_paths = _extract_tracked_dirty_paths(workspace)
    if tracked_paths is not None:
        count = 0
        for raw_path in sorted(tracked_paths):
            p = _normalize_workspace_path(str(raw_path))
            if _is_ignored_workspace_path(p):
                continue
            fp = _fingerprint_file(workspace, p)
            snapshot[p] = fp if fp is not None else "__deleted__"
            count += 1
            if count >= max_files:
                break
        return snapshot

    file_paths = _collect_file_paths(workspace, limit=max_files)
    for p in file_paths:
        fp = _fingerprint_file(workspace, p)
        if fp is not None:
            snapshot[p] = fp
            if len(snapshot) >= max_files:
                break
    return snapshot


@runtime_checkable
class _WorkspaceChangeProbe(Protocol):
    """Protocol for detecting workspace changes across phase execution attempts."""

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        """Capture baseline workspace state before an execution attempt."""
        ...

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        """Return True if workspace changed since observe_before was called."""
        ...


WorkspaceChangeProbe = _WorkspaceChangeProbe


class DefaultWorkspaceChangeProbe:
    """Default workspace change probe tracking per-phase baselines without global state."""

    def __init__(self, *, max_files: int = DEFAULT_MAX_SNAPSHOT_FILES) -> None:
        self._baselines: dict[str, dict[str, str]] = {}  # bounded-accumulator-ok: keyed by phase
        self._version_baselines: dict[
            str, str | None
        ] = {}  # bounded-accumulator-ok: keyed by phase
        self._max_files = max_files

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        """Capture baseline snapshot before an attempt."""
        ver = _extract_workspace_version(workspace)
        if ver is not None:
            self._version_baselines[phase] = ver
        self._baselines[phase] = take_workspace_snapshot(workspace, max_files=self._max_files)

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        """Return True if workspace changed since observe_before was called."""
        before_ver = self._version_baselines.pop(phase, None)
        if before_ver is not None:
            current_ver = _extract_workspace_version(workspace)
            if current_ver is not None and current_ver != before_ver:
                self._baselines.pop(phase, None)
                return True
        before = self._baselines.pop(phase, None)
        after = take_workspace_snapshot(workspace, max_files=self._max_files)
        if before is None:
            return False
        return before != after
