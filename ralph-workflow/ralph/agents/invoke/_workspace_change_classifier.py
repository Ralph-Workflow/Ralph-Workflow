from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from ralph.agents.idle_watchdog._workspace_change_kind import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeKind,
)

__all__ = [
    "ARTIFACT_PARENT_DIRS",
    "CACHE_FILENAME_GLOBS",
    "CACHE_PARENT_DIRS",
    "DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS",
    "ENGINE_ROOT_RELATIVE_CACHE_PATHS",
    "LOG_SUFFIXES",
    "SOURCE_EXTENSIONS",
    "WORKER_ENGINE_CACHE_DIRS",
    "WORKER_MANIFEST_BASENAME",
    "WorkspaceChangeClassifier",
    "WorkspaceChangeKind",
    "_is_agent_internal_state_db_path",
    "_normalize_workspace_change_weights",
]


_CACHED_VALUES: frozenset[str] = frozenset(member.value for member in WorkspaceChangeKind)
_ALLOWED_WEIGHTS: frozenset[float] = frozenset({0.0, 1.0})

CACHE_PARENT_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        ".agent/tmp",
        ".agent/raw",
        ".agent/ralph-explore",
        ".agent/receipts",
        ".agent/prompt_history",
        ".agent/artifact-formats",
        ".agent/logs",
    }
)

ENGINE_ROOT_RELATIVE_CACHE_PATHS: frozenset[str] = frozenset(
    {
        "checkpoint.json",
        ".agents/mcp_config.json",
        ".cursor/mcp.json",
        ".kimi-code/mcp.json",
    }
)

WORKER_ENGINE_CACHE_DIRS: frozenset[str] = frozenset({"tmp", "logs", "handoffs"})
WORKER_MANIFEST_BASENAME: str = "worker-manifest.json"

CACHE_FILENAME_GLOBS: tuple[str, ...] = ("completion_seen_*.json",)

_AGENT_INTERNAL_STATE_DB_BASENAMES: frozenset[str] = frozenset(
    {
        "state.db",
        "state.db-wal",
        "state.db-shm",
    }
)

ARTIFACT_PARENT_DIRS: frozenset[str] = frozenset({".agent/artifacts"})

LOG_SUFFIXES: frozenset[str] = frozenset({".log", ".tmp", ".bak", ".swp", "~", ".pyc", ".pyo"})

SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".pyw",
        ".rs",
        ".go",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".swift",
        ".m",
        ".mm",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cs",
        ".rb",
        ".php",
        ".scala",
        ".clj",
        ".cljs",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".lua",
        ".r",
        ".R",
        ".jl",
        ".dart",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".vue",
        ".svelte",
        ".astro",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".xml",
        ".md",
        ".rst",
        ".txt",
        ".ini",
        ".cfg",
        ".conf",
        ".env",
        ".tf",
        ".proto",
    }
)


@dataclass(frozen=True)
class WorkspaceChangeClassifier:
    weights: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
    )

    def __post_init__(self) -> None:
        for key, value in self.weights.items():
            if key not in _CACHED_VALUES:
                msg = (
                    f"WorkspaceChangeClassifier.weights[{key!r}] is not a valid"
                    f" WorkspaceChangeKind value; allowed: {sorted(_CACHED_VALUES)}"
                )
                raise ValueError(msg)
            if value not in _ALLOWED_WEIGHTS:
                msg = (
                    f"WorkspaceChangeClassifier.weights[{key!r}]={value!r}"
                    f" is not a binary weight; allowed: {{0.0, 1.0}}"
                )
                raise ValueError(msg)

    def classify(
        self,
        src_path: str,
        *,
        workspace_root: PurePosixPath | None = None,
    ) -> tuple[WorkspaceChangeKind, float]:
        posix = PurePosixPath(src_path.replace("\\", "/"))
        parts = posix.parts
        basename = posix.name

        rules: tuple[tuple[bool, WorkspaceChangeKind], ...] = (
            (
                _matches_parent_walk(parts, CACHE_PARENT_DIRS),
                WorkspaceChangeKind.CACHE,
            ),
            (
                _matches_filename_glob(basename, CACHE_FILENAME_GLOBS),
                WorkspaceChangeKind.CACHE,
            ),
            (
                _is_agent_top_level_path(parts),
                WorkspaceChangeKind.CACHE,
            ),
            (
                _worker_engine_kind(parts) is WorkspaceChangeKind.CACHE,
                WorkspaceChangeKind.CACHE,
            ),
            (
                _is_engine_root_relative_path(posix, workspace_root),
                WorkspaceChangeKind.CACHE,
            ),
            (
                _matches_parent_walk(parts, ARTIFACT_PARENT_DIRS)
                or _worker_engine_kind(parts) is WorkspaceChangeKind.ARTIFACT,
                WorkspaceChangeKind.ARTIFACT,
            ),
            (
                _matches_suffix(basename, LOG_SUFFIXES),
                WorkspaceChangeKind.LOG,
            ),
            (
                _matches_suffix(basename, SOURCE_EXTENSIONS),
                WorkspaceChangeKind.SOURCE,
            ),
        )

        for predicate, kind in rules:
            if predicate:
                return kind, self._weight(kind)

        return WorkspaceChangeKind.OTHER, self._weight(WorkspaceChangeKind.OTHER)

    def _weight(self, kind: WorkspaceChangeKind) -> float:
        return self.weights.get(kind.value, 0.0)


def _normalize_workspace_change_weights(
    partial: Mapping[str, float] | None,
) -> dict[str, float]:
    merged: dict[str, float] = dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
    if partial is not None:
        merged.update(partial)
    return merged


def _is_agent_internal_state_db_path(
    path_parts: tuple[str, ...],
    basename: str,
) -> bool:
    if basename not in _AGENT_INTERNAL_STATE_DB_BASENAMES:
        return False
    return _is_agent_top_level_path(path_parts)


def _worker_engine_kind(path_parts: tuple[str, ...]) -> WorkspaceChangeKind | None:
    for index in range(len(path_parts) - 3):
        if path_parts[index] != ".agent" or path_parts[index + 1] != "workers":
            continue
        remainder = path_parts[index + 3 :]
        if len(remainder) == 1 and remainder[0] == WORKER_MANIFEST_BASENAME:
            return WorkspaceChangeKind.CACHE
        if len(remainder) >= _PARENT_DIR_LOOKUP_DEPTH:
            if remainder[0] in WORKER_ENGINE_CACHE_DIRS:
                return WorkspaceChangeKind.CACHE
            if remainder[0] == "artifacts":
                return WorkspaceChangeKind.ARTIFACT
        return None
    return None


def _is_engine_root_relative_path(
    posix: PurePosixPath, workspace_root: PurePosixPath | None
) -> bool:
    if workspace_root is None:
        return False
    try:
        relative = posix.relative_to(workspace_root)
    except ValueError:
        return False
    return relative.as_posix() in ENGINE_ROOT_RELATIVE_CACHE_PATHS


def _is_agent_top_level_path(path_parts: tuple[str, ...]) -> bool:
    if len(path_parts) < _PARENT_DIR_LOOKUP_DEPTH:
        return False
    return path_parts[-_PARENT_DIR_LOOKUP_DEPTH] == ".agent"


_PARENT_DIR_LOOKUP_DEPTH: int = 2


def _matches_parent_walk(
    path_parts: tuple[str, ...],
    parent_set: frozenset[str],
) -> bool:
    for window_size in range(1, len(path_parts) + 1):
        for start in range(len(path_parts) - window_size + 1):
            candidate = "/".join(path_parts[start : start + window_size])
            if candidate in parent_set:
                return True
    return False


def _matches_filename_glob(basename: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(basename, glob) for glob in globs)


def _matches_suffix(basename: str, suffixes: frozenset[str]) -> bool:
    return any(basename.endswith(suffix) for suffix in suffixes)
