from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.sight import ConflictClassification, ConflictSight

__all__ = ["WorktreeConflictObserver"]


class WorktreeConflictObserver(Protocol):
    def unmerged_paths(self, root: Path) -> Sequence[str]: ...

    def retained_merge_paths(self, root: Path, paths: tuple[str, ...]) -> tuple[str, ...]: ...

    def classify_unmerged_conflicts(
        self, root: Path, paths: tuple[str, ...]
    ) -> ConflictClassification | dict[str, ConflictSight]: ...

    def out_of_reach_reasons(self, kinds: dict[str, ConflictSight]) -> dict[str, str]: ...

    def declared_decision_paths(self, kinds: dict[str, ConflictSight]) -> tuple[str, ...]: ...

    def stage_mechanical_conflicts(
        self, root: Path, kinds: dict[str, ConflictSight]
    ) -> tuple[str, ...]: ...

    def paths_with_conflict_markers(self, root: Path, paths: tuple[str, ...]) -> Sequence[str]: ...

    def path_exists(self, path: Path) -> bool: ...

    def is_effectively_empty(self, path: Path) -> bool: ...
