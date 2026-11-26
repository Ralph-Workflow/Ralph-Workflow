from __future__ import annotations

import hashlib
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.git.ignored_paths import gitignored_paths
from ralph.workspace.awareness import _awareness_by_workspace, _awareness_lock

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.workspace.protocol import Workspace

__all__ = [
    "DEFAULT_MAX_SNAPSHOT_FILES",
    "ENGINE_ROOT_RELATIVE_CACHE_PATHS",
    "DefaultWorkspaceChangeProbe",
    "WorkspaceChangeProbe",
    "take_workspace_snapshot",
]

DEFAULT_MAX_SNAPSHOT_FILES: int = 1000

ENGINE_ROOT_RELATIVE_CACHE_PATHS: frozenset[str] = frozenset(
    {
        "checkpoint.json",
        ".agents/mcp_config.json",
        ".cursor/mcp.json",
        ".kimi-code/mcp.json",
    }
)
_DELETED_FINGERPRINT = "__deleted__"
_VERSION_KEY = "__workspace_version__"

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
    if norm in ENGINE_ROOT_RELATIVE_CACHE_PATHS:
        return True
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


@runtime_checkable
class _HasWalkFiles(Protocol):
    def walk_files(self, base: str) -> Iterable[object]: ...


_MAX_SCANNED_ENTRIES_PER_SNAPSHOT_FILE = 50


def _first_unignored(items: Iterable[object], limit: int) -> list[str]:
    result: list[str] = []
    scan_cap = max(limit, 1) * _MAX_SCANNED_ENTRIES_PER_SNAPSHOT_FILE
    for scanned, item in enumerate(items, start=1):
        norm = _normalize_workspace_path(str(item))
        if not _is_ignored_workspace_path(norm):
            result.append(norm)
            if len(result) >= limit:
                break
        if scanned >= scan_cap:
            break
    return sorted(result)


def _collect_file_paths(workspace: Workspace, limit: int) -> list[str]:
    if isinstance(workspace, _HasWalkFiles):
        with suppress(OSError, RuntimeError, ValueError, AttributeError):
            return _first_unignored(workspace.walk_files(""), limit)
    if isinstance(workspace, _HasIterFilesBounded):
        with suppress(Exception):
            return sorted(str(p) for p in workspace.iter_files_bounded("", limit=limit))
    with suppress(OSError, RuntimeError, ValueError, AttributeError):
        return _first_unignored(workspace.iter_files(""), limit)
    try:
        result: list[str] = []
        for item in workspace.list_dir("."):
            norm = _normalize_workspace_path(str(item))
            if _is_ignored_workspace_path(norm):
                continue
            if workspace.is_file(norm):
                result.append(norm)
                if len(result) >= limit:
                    break
        return sorted(result)
    except (OSError, RuntimeError, ValueError, AttributeError):
        return []


def _fingerprint_file(workspace: Workspace, path: str) -> str | None:
    try:
        st = workspace.stat(path)
    except (OSError, RuntimeError, ValueError, AttributeError):
        return None
    if st.get("type") != "file":
        return None
    mtime = st.get("modified_unix")
    size = st.get("size_bytes", 0)
    if mtime is not None:
        return f"{size}:{mtime}"
    try:
        content = workspace.read(path)
    except (OSError, RuntimeError, ValueError, AttributeError):
        return None
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    return f"{size}:{digest}"


def take_workspace_snapshot(
    workspace: Workspace,
    *,
    max_files: int = DEFAULT_MAX_SNAPSHOT_FILES,
) -> dict[str, str]:
    tracked_paths = _extract_tracked_dirty_paths(workspace)
    if tracked_paths is not None:
        snapshot = _versioned(workspace)
        snapshot.update(_tracked_fingerprints(workspace, tracked_paths, max_files=max_files))
        return snapshot
    return _walk_snapshot(workspace, max_files=max_files)


def _versioned(workspace: Workspace) -> dict[str, str]:
    ver = _extract_workspace_version(workspace)
    return {} if ver is None else {_VERSION_KEY: ver}


def _git_ignored(workspace: Workspace, paths: list[str]) -> frozenset[str]:
    if not isinstance(workspace, _HasRoot):
        return frozenset()
    root = workspace.root
    if not isinstance(root, (Path, str)):
        return frozenset()
    return gitignored_paths(root, paths)


def _walk_snapshot(workspace: Workspace, *, max_files: int) -> dict[str, str]:
    snapshot = _versioned(workspace)
    count = 0
    paths = _collect_file_paths(workspace, limit=max_files)
    ignored = _git_ignored(workspace, paths)
    for p in paths:
        if _is_ignored_workspace_path(p) or p in ignored:
            continue
        fp = _fingerprint_file(workspace, p)
        if fp is not None:
            snapshot[p] = fp
            count += 1
            if count >= max_files:
                break
    return snapshot


def _tracked_fingerprints(
    workspace: Workspace,
    tracked_paths: Iterable[object],
    *,
    max_files: int,
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for raw_path in sorted({_normalize_workspace_path(str(p)) for p in tracked_paths}):
        if _is_ignored_workspace_path(raw_path):
            continue
        fp = _fingerprint_file(workspace, raw_path)
        fingerprints[raw_path] = fp if fp is not None else _DELETED_FINGERPRINT
        if len(fingerprints) >= max_files:
            break
    return fingerprints


def _tracked_paths_changed(
    workspace: Workspace,
    baseline: dict[str, str],
    *,
    max_files: int,
) -> bool:
    current = _extract_tracked_dirty_paths(workspace) or []
    after = _tracked_fingerprints(workspace, [*baseline, *current], max_files=max_files)
    return any(
        fingerprint != baseline.get(path, _DELETED_FINGERPRINT)
        for path, fingerprint in after.items()
    )


@runtime_checkable
class _WorkspaceChangeProbe(Protocol):
    def observe_before(self, phase: str, workspace: Workspace) -> None: ...

    def has_changed(self, phase: str, workspace: Workspace) -> bool: ...


WorkspaceChangeProbe = _WorkspaceChangeProbe


class DefaultWorkspaceChangeProbe:
    def __init__(self, *, max_files: int = DEFAULT_MAX_SNAPSHOT_FILES) -> None:
        self._baselines: dict[str, dict[str, str]] = {}
        self._tracked_baselines: dict[str, dict[str, str]] = {}
        self._version_baselines: dict[str, str | None] = {}
        self._max_files = max_files

    def observe_before(self, phase: str, workspace: Workspace) -> None:
        ver = _extract_workspace_version(workspace)
        if ver is not None:
            self._version_baselines[phase] = ver
        self._baselines[phase] = _walk_snapshot(workspace, max_files=self._max_files)
        tracked = _extract_tracked_dirty_paths(workspace)
        if tracked is None:
            self._tracked_baselines.pop(phase, None)
        else:
            self._tracked_baselines[phase] = _tracked_fingerprints(
                workspace, tracked, max_files=self._max_files
            )

    def has_changed(self, phase: str, workspace: Workspace) -> bool:
        before_ver = self._version_baselines.pop(phase, None)
        if before_ver is not None:
            current_ver = _extract_workspace_version(workspace)
            if current_ver is not None and current_ver != before_ver:
                self._baselines.pop(phase, None)
                self._tracked_baselines.pop(phase, None)
                return True
        before = self._baselines.pop(phase, None)
        tracked_before = self._tracked_baselines.pop(phase, {})
        if before is None:
            return False
        if before != _walk_snapshot(workspace, max_files=self._max_files):
            return True
        return _tracked_paths_changed(workspace, tracked_before, max_files=self._max_files)
