from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from ralph.pipeline._rung_post_condition import RungPostCondition

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.integration_observation import InProgressKind

from ralph.pipeline._rung_post_condition import RungPostCondition

__all__ = [
    "EnvironmentFailure",
    "EnvironmentProbe",
    "FileQuarantine",
    "GitPlumbing",
    "LadderOutcome",
    "LandResult",
    "NextStrategyOutcome",
    "RepointResult",
    "RungPostCondition",
]


type LadderOutcome = Literal["succeeded", "failed", "impossible", "waiting", "terminal"]


@dataclass(frozen=True, slots=True)
class NextStrategyOutcome:
    strategy: Literal["complete_in_place"]
    reason: str
    alternatives_considered: tuple[str, ...]


type LandResult = Literal[
    "landed",
    "landed-already",
    "tip-unreadable",
    "target-unreadable",
    "target-moved",
    "update-failed",
]

type RepointResult = Literal[
    "repointed",
    "worktree-missing",
    "head-moved",
    "head-unreadable",
    "tip-unreadable",
    "reset-failed",
]

type EnvironmentFailure = Literal[
    "read_only_fs",
    "disk_full",
    "safe_directory",
    "missing_gitdir",
    "remote_down",
]


@runtime_checkable
class _GitPlumbing(Protocol):
    def changed_paths(self, root: Path) -> tuple[str, ...] | None: ...

    def head_sha(self, root: Path) -> str | None: ...

    def target_sha(self, root: Path, target: str) -> str | None: ...

    def commit_dirt(self, root: Path, head_sha: str, owning_worktree: str | None) -> str | None: ...

    def ref_is_readable(self, root: Path, ref: str) -> bool: ...

    def prune_landed_backup_refs(
        self, root: Path, is_landed: Callable[[str], bool]
    ) -> tuple[str, ...]: ...

    def write_patch(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool: ...

    def archive_paths(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool: ...

    def restore_tree_to_head(self, root: Path) -> bool: ...

    def abort_operation(self, root: Path, kind: InProgressKind) -> bool: ...

    def remove_operation_state(self, root: Path, kind: InProgressKind) -> bool: ...

    def reset_hard(self, root: Path, revision: str) -> bool: ...

    def clean_untracked(self, root: Path, paths: tuple[str, ...]) -> bool: ...

    def remove_stale_locks(self, root: Path) -> tuple[str, ...]: ...

    def recreate_worktree(self, root: Path, branch: str) -> bool: ...

    def set_safe_directory(self, root: Path) -> bool: ...

    def repair_gitdir(self, root: Path) -> bool: ...

    def create_worktree(self, root: Path, path: Path, target: str) -> bool: ...

    def apply_preserved(self, root: Path, path: Path, preserved_ref: str) -> bool: ...

    def land_in_worktree(
        self, root: Path, path: Path, target: str, expected_sha: str
    ) -> LandResult: ...

    def repoint_original(
        self, root: Path, path: Path, observed_sha: str | None
    ) -> RepointResult: ...

    def remote_reachable(self, root: Path, remote: str) -> bool: ...


GitPlumbing = _GitPlumbing


@runtime_checkable
class _FileQuarantine(Protocol):
    def rename_into_quarantine(self, root: Path, quarantine_dir: Path, path: str) -> str | None: ...

    def prune_regenerable_artifacts(self, state_dir: Path) -> tuple[str, ...]: ...

    def path_is_readable(self, path: Path) -> bool: ...

    def alternative_state_dir(self, root: Path) -> Path | None: ...


FileQuarantine = _FileQuarantine


@runtime_checkable
class _EnvironmentProbe(Protocol):
    def failure_class(self, root: Path, *, remote: str) -> EnvironmentFailure | None: ...


EnvironmentProbe = _EnvironmentProbe
