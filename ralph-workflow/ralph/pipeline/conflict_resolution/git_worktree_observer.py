"""Production observer for the conflict-resolution round loop.

:class:`GitWorktreeConflictObserver` implements
:class:`~ralph.pipeline.conflict_resolution.worktree_observer.WorktreeConflictObserver`
against the real worktree and git index. The driver uses it whenever no
``observer=`` is injected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.sight import ConflictSight

__all__ = ["GitWorktreeConflictObserver"]


class GitWorktreeConflictObserver:
    """Default observer delegating to the existing module-level functions.

    Every git/sight delegation resolves its target through the ``driver``
    module namespace at CALL TIME, not import time. The legacy
    monkeypatch-based tests patch attributes such as
    ``driver.unmerged_paths`` and ``driver.paths_with_conflict_markers``
    on that module; binding the functions at import time here would hide
    those patches behind this adapter and break the suites that rely on
    them. The content/existence checks are not part of that patch surface
    and are implemented directly.
    """

    def unmerged_paths(self, root: Path) -> Sequence[str]:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        return _driver_mod.unmerged_paths(root)

    def retained_merge_paths(self, root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        return _driver_mod.retained_merge_paths(root, paths)

    def classify_unmerged_conflicts(
        self, root: Path, paths: tuple[str, ...]
    ) -> dict[str, ConflictSight]:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        return _driver_mod.classify_unmerged_conflicts(root, paths)

    def declared_decision_paths(self, kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        return _driver_mod.declared_decision_paths(kinds)

    def stage_mechanical_conflicts(
        self, root: Path, kinds: dict[str, ConflictSight]
    ) -> tuple[str, ...]:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        return _driver_mod.stage_mechanical_conflicts(root, kinds)

    def paths_with_conflict_markers(self, root: Path, paths: tuple[str, ...]) -> Sequence[str]:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        return _driver_mod.paths_with_conflict_markers(root, paths)

    def path_exists(self, path: Path) -> bool:
        return path.exists()

    def is_effectively_empty(self, path: Path) -> bool:
        from ralph.pipeline.conflict_resolution.round_verdict import is_effectively_empty

        return is_effectively_empty(path)
