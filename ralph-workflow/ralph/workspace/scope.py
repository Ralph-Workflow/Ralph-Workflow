"""Canonical workspace scope for the active Ralph run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

from ralph.git.operations import GitOperationError, find_main_worktree_root, find_repo_root

CONFIG_DIR_NAME = ".agent"
WORKSPACE_CONFIG_NAME = "ralph-workflow.toml"


def _canonicalize(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True, init=False)
class WorkspaceScope:
    """Single source of truth for workspace root and future allowed roots."""

    root: Path
    allowed_roots: tuple[Path, ...]
    local_config_path: Path
    propagated_config_paths: tuple[Path, ...]

    def __init__(
        self,
        root: Path | str,
        allowed_roots: Iterable[Path | str] | None = None,
        *,
        local_config_path: Path | str | None = None,
        propagated_config_paths: Iterable[Path | str] | None = None,
    ) -> None:
        canonical_root = _canonicalize(root)
        deduped_allowed: list[Path] = [canonical_root]
        for candidate in allowed_roots or ():
            canonical_candidate = _canonicalize(candidate)
            if canonical_candidate not in deduped_allowed:
                deduped_allowed.append(canonical_candidate)
        canonical_allowed = tuple(deduped_allowed)
        canonical_local_config = _canonicalize(
            local_config_path or _default_local_config_path(canonical_root)
        )
        canonical_propagated_configs = tuple(
            _canonicalize(candidate) for candidate in propagated_config_paths or ()
        )
        object.__setattr__(self, "root", canonical_root)
        object.__setattr__(self, "allowed_roots", canonical_allowed)
        object.__setattr__(self, "local_config_path", canonical_local_config)
        object.__setattr__(self, "propagated_config_paths", canonical_propagated_configs)

    @classmethod
    def for_same_workspace_worker(
        cls,
        repo_root: Path,
        allowed_directories: tuple[str, ...],
        worker_namespace: Path,
    ) -> WorkspaceScope:
        """Build a worker-scoped view of the shared checkout.

        The root stays at ``repo_root`` (no per-worker root reassignment). Each
        allowed directory is resolved relative to ``repo_root``. The
        ``worker_namespace`` is always added so the worker can write its own
        artifacts, logs, and temporary outputs even when ``allowed_directories``
        is narrow. A ``ValueError`` is raised when any entry escapes ``repo_root``
        via ``..`` or an absolute path.

        This method bypasses the standard __init__ to avoid unconditionally
        adding ``repo_root`` to allowed_roots. Same-workspace workers must NOT
        have the repo root as an allowed root — they are restricted to only
        their declared edit areas plus their own worker namespace.

        Args:
            repo_root: Shared repository root (same for all parallel workers).
            allowed_directories: Relative subpaths the worker may edit.
            worker_namespace: Per-worker scratch directory (always allowed).

        Returns:
            WorkspaceScope with root=repo_root, allowed_roots restricted to the
            declared directories plus the worker namespace (repo root is NOT included).
        """
        canonical_root = _canonicalize(repo_root)
        canonical_ns = _canonicalize(worker_namespace)

        allowed_roots: list[Path] = []
        for ad in allowed_directories:
            if not ad:
                raise ValueError("allowed_directory must be non-empty")
            p = canonical_root / ad
            resolved = p.resolve()
            if not str(resolved).startswith(str(canonical_root)):
                raise ValueError(
                    f"allowed_directory {ad!r} escapes repo_root {canonical_root}"
                )
            allowed_roots.append(resolved)

        allowed_roots.append(canonical_ns)

        # Build the scope directly, bypassing __init__ to avoid unconditionally
        # adding canonical_root to allowed_roots. Same-workspace workers must
        # only have their specific allowed directories + worker namespace.
        scope = object.__new__(cls)
        object.__setattr__(scope, "root", canonical_root)
        object.__setattr__(scope, "allowed_roots", cast("tuple[Path, ...]", tuple(allowed_roots)))
        object.__setattr__(
            scope, "local_config_path", _default_local_config_path(canonical_root)
        )
        object.__setattr__(scope, "propagated_config_paths", ())
        return scope


def _default_local_config_path(root: Path) -> Path:
    return root / CONFIG_DIR_NAME / WORKSPACE_CONFIG_NAME


def _find_nearest_workspace_root(candidate: Path, repo_root: Path) -> Path:
    """Prefer the nearest Ralph workspace config between cwd and repo root."""
    current = candidate.resolve()
    resolved_repo_root = repo_root.resolve()
    while True:
        if _default_local_config_path(current).exists():
            return current
        if current == resolved_repo_root:
            return resolved_repo_root
        parent = current.parent
        if parent == current:
            return resolved_repo_root
        current = parent


def resolve_workspace_scope(start: Path | str | None = None) -> WorkspaceScope:
    """Resolve the active workspace scope.

    Prefer the current git worktree root and fall back to the provided path/cwd
    when Ralph is run outside a git repository.
    """

    candidate = Path.cwd() if start is None else Path(start)
    try:
        repo_root = find_repo_root(candidate)
        main_root = find_main_worktree_root(candidate)
        root = _find_nearest_workspace_root(candidate, repo_root)
        propagated_configs: tuple[Path, ...] = ()
        if main_root != repo_root:
            propagated_configs = (_default_local_config_path(main_root),)
        return WorkspaceScope(
            root,
            local_config_path=_default_local_config_path(root),
            propagated_config_paths=propagated_configs,
        )
    except GitOperationError:
        return WorkspaceScope(candidate)


__all__ = ["WorkspaceScope", "resolve_workspace_scope"]
