from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.operation_snapshot import (
    OperationState,
    observe_operation_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "conflict_resolution_warranted",
]


def conflict_resolution_warranted(root: Path) -> bool:
    try:
        snapshot = observe_operation_snapshot(root)
    except (OSError, RuntimeError, ValueError, KeyError):
        return True
    return bool(
        snapshot.operation_state is OperationState.REBASE_PAUSED
        or not snapshot.unmerged_query_ok
        or snapshot.unmerged_paths
        or snapshot.staged_conflict_marker_paths
    )
