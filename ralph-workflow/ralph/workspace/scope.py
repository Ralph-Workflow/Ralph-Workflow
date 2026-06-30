"""Canonical workspace scope for the active Ralph run.

Provides ``WorkspaceScope``, the frozen dataclass that centralises all
workspace-root and allowed-directory decisions made at process startup. Every
component that needs to know where files live or which paths an agent may write
should read its values from a ``WorkspaceScope`` instance rather than calling
``Path.cwd()`` directly.

Key API:

- ``resolve_workspace_scope(start)`` - detect the active workspace from the
  filesystem. Walks upward from *start* (default: ``cwd()``) looking for a
  ``ralph-workflow.toml`` config file or a git repo root. Linked worktrees
  automatically inherit config from the main worktree unless the linked
  worktree has its own override.
- ``WorkspaceScope`` - frozen dataclass with ``root``, ``allowed_roots``,
  ``local_config_path``, and ``propagated_config_paths``. Use
  ``scope.resolve_agent_file(filename)`` to locate ``.agent/`` files with
  correct inheritance between linked and main worktrees.
- ``WorkspaceScope.for_same_workspace_worker(...)`` - builds a restricted
  scope for parallel workers that share a single checkout; the repo root is NOT
  added to allowed roots, enforcing that workers only write to their declared
  directories and their own worker namespace.

Config files searched (in order):
  ``ralph-workflow.toml``, ``agents.toml``, ``pipeline.toml``,
  ``artifacts.toml``, ``mcp.toml`` (all under ``.agent/`` in the workspace
  root).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

from ralph.git.operations import GitOperationError, find_main_worktree_root, find_repo_root
from ralph.pro_support.workspace import resolve_pro_workspace

CONFIG_DIR_NAME = ".agent"
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
    """Single source of truth for workspace root and config inheritance.

    Frozen dataclass that centralises every workspace-root and
    allowed-directory decision made at process startup. Every
    component that needs to know where files live, which paths an
    agent may write, or where local and inherited configuration
    files are read from should consume a :class:`WorkspaceScope`
    instance rather than calling :func:`pathlib.Path.cwd` directly.
    The dataclass is hashable and frozen so it can be cached,
    passed between threads, and used as a dictionary key.

    Construction canonicalises every path through :func:`_canonicalize`
    (expanduser + resolve) and deduplicates ``allowed_roots`` so
    callers can pass user-supplied paths and still rely on a stable
    canonical form.

    Attributes:
        root: Canonical absolute path to the workspace root. All
            ``.agent/``-relative paths are resolved against this
            value, and it is the canonical entry point for
            repository-relative lookups.
        allowed_roots: Tuple of canonical absolute paths that agents
            may read or write to during the run. ``root`` is always
            the first entry. Additional entries are added by the
            workspace resolver for linked worktrees, parallel
            worker namespaces, and any directory the active
            pipeline phase has been granted access to.
        local_config_path: Canonical absolute path to the
            workspace-local ``ralph-workflow.toml`` file. Defaults to
            ``<root>/.agent/ralph-workflow.toml`` when no override
            is supplied; can be overridden for tests and for
            workspaces that store configuration outside ``.agent/``.
        propagated_config_paths: Tuple of canonical absolute paths
            to inherited configuration files. The values come from
            the workspace resolver walking upward from ``root`` and
            collecting any ``.agent/ralph-workflow.toml``,
            ``agents.toml``, ``pipeline.toml``, ``artifacts.toml``,
            or ``mcp.toml`` it finds. Order is parent-first so the
            most-specific entry wins on conflict.

    Lifecycle:
        1. Construct (or receive from
           :func:`resolve_workspace_scope`) a :class:`WorkspaceScope`.
        2. Pass it to every component that needs to locate files
           (``scope.root``, ``scope.local_config_path``) or check
           path containment (``root in scope.allowed_roots``).
        3. For parallel workers, build a restricted scope with
           :meth:`for_same_workspace_worker` instead of mutating
           the original instance — the dataclass is frozen.
    """

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

    def resolve_agent_file(self, filename: str) -> Path:
        """Resolve the effective .agent file for this workspace.

        Linked worktrees inherit defaults from the main worktree unless the
        current workspace has an explicit local override for that filename.
        """
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
        """Return True when the current workspace has any explicit .agent override."""
        local_agent_dir = self.root / CONFIG_DIR_NAME
        return any((local_agent_dir / filename).exists() for filename in _WORKSPACE_AGENT_FILENAMES)

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
            try:
                resolved.relative_to(canonical_root)
            except ValueError:
                raise ValueError(
                    f"allowed_directory {ad!r} escapes repo_root {canonical_root}"
                ) from None
            allowed_roots.append(resolved)

        allowed_roots.append(canonical_ns)

        # Build the scope directly, bypassing __init__ to avoid unconditionally
        # adding canonical_root to allowed_roots. Same-workspace workers must
        # only have their specific allowed directories + worker namespace.
        scope = object.__new__(cls)
        object.__setattr__(scope, "root", canonical_root)
        object.__setattr__(scope, "allowed_roots", cast("tuple[Path, ...]", tuple(allowed_roots)))
        object.__setattr__(scope, "local_config_path", _default_local_config_path(canonical_root))
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

    The workspace root remains the active checkout, but linked worktrees inherit
    default .agent config from the main checkout unless the linked worktree has
    an explicit local override file.
    """

    if start is None:
        try:
            candidate = resolve_pro_workspace()
        except Exception:  # import-time failure of pro_support must not break scope resolution
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


__all__ = ["WorkspaceScope", "resolve_workspace_scope"]
