"""Fakeable observation seam for the conflict-resolution round loop.

The driver's verdict — "resolved" or not — and every prompt it writes
rest on a small, fixed set of filesystem and git reads: listing unmerged
paths, classifying them on sight, staging the mechanical ones, scanning
for surviving conflict markers, and checking whether a path still exists
or was emptied. Scenario tests used to reach those reads by monkeypatching
attributes on the driver module; :class:`WorktreeConflictObserver` is the
public, injectable replacement. The round loop's public entry points
accept ``observer=`` and thread it to every inventoried read site.

The prompt render+write seam lives separately in
:mod:`ralph.pipeline.conflict_resolution.prompt` as
:class:`~ralph.pipeline.conflict_resolution.prompt.PromptMaterializer`,
because it consumes the observation results rather than producing them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.sight import ConflictSight

__all__ = ["GitWorktreeConflictObserver", "WorktreeConflictObserver"]


class WorktreeConflictObserver(Protocol):
    """Every read the round loop's verdict and prompt rest on.

    Each method replaces one module-level function the driver used to
    call directly. :class:`GitWorktreeConflictObserver` is the production
    implementation; scenario tests inject a fake instead of monkeypatching
    driver internals.
    """

    def unmerged_paths(self, root: Path) -> Sequence[str]:
        """Paths git still reports as unmerged under ``root``.

        A failed query reports the query-failure sentinel so a broken
        repository is never mistaken for "fully resolved".
        """
        ...

    def retained_merge_paths(self, root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
        """Durable or Git-recorded conflict paths from the active merge."""
        ...

    def classify_unmerged_conflicts(
        self, root: Path, paths: tuple[str, ...]
    ) -> dict[str, ConflictSight]:
        """Classify every unmerged path before the chain is spent."""
        ...

    def declared_decision_paths(self, kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
        """Paths whose resolution is a decision the round must see declared."""
        ...

    def stage_mechanical_conflicts(
        self, root: Path, kinds: dict[str, ConflictSight]
    ) -> tuple[str, ...]:
        """Stage identical-blob / matching-gitlink paths; return what was staged."""
        ...

    def paths_with_conflict_markers(self, root: Path, paths: tuple[str, ...]) -> Sequence[str]:
        """Paths that still carried conflict markers on the on-disk re-scan."""
        ...

    def path_exists(self, path: Path) -> bool:
        """Whether a conflicted path is still present in the worktree."""
        ...

    def is_effectively_empty(self, path: Path) -> bool:
        """Whether a file holds nothing a resolution could have meant to keep."""
        ...


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
