"""Canonical workspace scope for the active Ralph run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
    def for_worktree(
        cls,
        worktree_path: Path,
        allowed_directories: tuple[str, ...],
    ) -> WorkspaceScope:
        allowed_roots = tuple(worktree_path / ad for ad in allowed_directories)
        return cls(root=worktree_path, allowed_roots=allowed_roots)


def _default_local_config_path(root: Path) -> Path:
    return root / CONFIG_DIR_NAME / WORKSPACE_CONFIG_NAME


def resolve_workspace_scope(start: Path | str | None = None) -> WorkspaceScope:
    """Resolve the active workspace scope.

    Prefer the current git worktree root and fall back to the provided path/cwd
    when Ralph is run outside a git repository.
    """

    candidate = Path.cwd() if start is None else Path(start)
    try:
        root = find_repo_root(candidate)
        main_root = find_main_worktree_root(candidate)
        propagated_configs: tuple[Path, ...] = ()
        if main_root != root:
            propagated_configs = (_default_local_config_path(main_root),)
        return WorkspaceScope(
            root,
            local_config_path=_default_local_config_path(root),
            propagated_config_paths=propagated_configs,
        )
    except GitOperationError:
        return WorkspaceScope(candidate)


__all__ = ["WorkspaceScope", "resolve_workspace_scope"]
