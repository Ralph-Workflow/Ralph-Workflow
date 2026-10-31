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

__all__ = ["WorktreeConflictObserver"]


class WorktreeConflictObserver(Protocol):
    """Every read the round loop's verdict and prompt rest on.

    Each method replaces one module-level function the driver used to
    call directly. The production implementation is
    :class:`~ralph.pipeline.conflict_resolution.git_worktree_observer.GitWorktreeConflictObserver`;
    scenario tests inject a fake instead of monkeypatching driver internals.
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
