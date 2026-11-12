from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

from ralph.git.errors import GitOperationError
from ralph.git.operations import find_main_worktree_root, find_repo_root
from ralph.pro_support.workspace import resolve_pro_workspace

CONFIG_DIR_NAME = ".agent"
WORKTREE_SCOPE_LABEL = "worktree"
PROJECT_SCOPE_LABEL = "project"
_PRO_WORKSPACE_RESOLVER = "ralph.pro_support.workspace.resolve_pro_workspace"
WORKSPACE_CONFIG_NAME = "ralph-workflow.toml"
_WORKSPACE_AGENT_FILENAMES = (
    "ralph-workflow.toml",
    "agents.toml",
    "pipeline.toml",
    "artifacts.toml",
    "mcp.toml",
)


def _canonicalize(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True, init=False)
class WorkspaceScope:
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

    @property
    def is_linked_worktree(self) -> bool:
        return self.project_config_path.parent.parent != self.root

    @property
    def scope_label(self) -> str:
        return WORKTREE_SCOPE_LABEL if self.is_linked_worktree else PROJECT_SCOPE_LABEL

    @property
    def worktree_config_path(self) -> Path:
        return _default_local_config_path(self.root)

    @property
    def project_config_path(self) -> Path:
        if self.local_config_path != self.worktree_config_path:
            return self.local_config_path
        if self.propagated_config_paths:
            return self.propagated_config_paths[0]
        return self.worktree_config_path

    def resolve_agent_file(self, filename: str) -> Path:
        local_agent_dir = self.root / CONFIG_DIR_NAME
        local_candidate = local_agent_dir / filename
        if local_candidate.exists():
            return local_candidate

        inherited_candidate = self.local_config_path.parent / filename
        if inherited_candidate != local_candidate:
            return inherited_candidate

        if self.propagated_config_paths:
            propagated_candidate = self.propagated_config_paths[0].parent / filename
            if propagated_candidate != local_candidate:
                return propagated_candidate

        return local_candidate

    def has_any_local_agent_override(self) -> bool:
        local_agent_dir = self.root / CONFIG_DIR_NAME
        return any((local_agent_dir / filename).exists() for filename in _WORKSPACE_AGENT_FILENAMES)

    @classmethod
    def for_same_workspace_worker(
        cls,
        repo_root: Path,
        allowed_directories: tuple[str, ...],
        worker_namespace: Path,
        *,
        paths: tuple[str, ...] = (),
    ) -> WorkspaceScope:
        canonical_root = _canonicalize(repo_root)
        canonical_ns = _canonicalize(worker_namespace)

        allowed_roots: list[Path] = []
        for ad in (*allowed_directories, *paths):
            if not ad or Path(ad) == Path():
                raise ValueError("allowed_directory must be a specific non-empty path")
            relative = Path(ad)
            if relative.is_absolute() or any(
                part in {".agent", ".git", ".worktrees"} for part in relative.parts
            ):
                raise ValueError(f"allowed_directory {ad!r} includes a protected path")
            p = canonical_root / ad
            resolved = p.resolve()
            try:
                resolved.relative_to(canonical_root)
            except ValueError:
                raise ValueError(
                    f"allowed_directory {ad!r} escapes repo_root {canonical_root}"
                ) from None
            allowed_roots.append(resolved)

        allowed_roots.append(canonical_ns)

        scope = object.__new__(cls)
        object.__setattr__(scope, "root", canonical_root)
        object.__setattr__(scope, "allowed_roots", cast("tuple[Path, ...]", tuple(allowed_roots)))
        object.__setattr__(scope, "local_config_path", _default_local_config_path(canonical_root))
        object.__setattr__(scope, "propagated_config_paths", ())
        return scope


def _default_local_config_path(root: Path) -> Path:
    return root / CONFIG_DIR_NAME / WORKSPACE_CONFIG_NAME


def _find_nearest_workspace_root(candidate: Path, repo_root: Path) -> Path:
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

    if start is None:
        try:
            candidate = resolve_pro_workspace()
        except Exception:
            candidate = Path.cwd()
    else:
        candidate = Path(start)
    try:
        repo_root = find_repo_root(candidate)
        main_root = find_main_worktree_root(candidate)
        root = _find_nearest_workspace_root(candidate, repo_root)
        local_config_path = _default_local_config_path(root)
        propagated_configs: tuple[Path, ...] = ()
        if main_root != repo_root:
            inherited_config_path = _default_local_config_path(main_root)
            if not local_config_path.exists():
                local_config_path = inherited_config_path
            else:
                propagated_configs = (inherited_config_path,)
        return WorkspaceScope(
            root,
            local_config_path=local_config_path,
            propagated_config_paths=propagated_configs,
        )
    except GitOperationError:
        return WorkspaceScope(candidate)


__all__ = [
    "PROJECT_SCOPE_LABEL",
    "WORKTREE_SCOPE_LABEL",
    "WorkspaceScope",
    "resolve_workspace_scope",
]
