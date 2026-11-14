from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.operation_snapshot import (
    OperationSnapshot,
    OperationState,
    observe_operation_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "conflict_resolution_warranted",
    "retained_round_fingerprint",
]


def conflict_resolution_warranted(root: Path) -> bool:
    try:
        snapshot = observe_operation_snapshot(root)
    except Exception:
        return True
    return bool(
        snapshot.operation_state is OperationState.REBASE_PAUSED
        or not snapshot.unmerged_query_ok
        or snapshot.unmerged_paths
        or snapshot.staged_conflict_marker_paths
    )


def retained_round_fingerprint(root: Path) -> str:
    snapshot = observe_operation_snapshot(root)
    return "|".join(
        (_head_identity(snapshot), _stop_identity(snapshot), _unmerged_identity(snapshot))
    )


def _head_identity(snapshot: OperationSnapshot) -> str:
    return snapshot.head_sha or "unreadable"


def _stop_identity(snapshot: OperationSnapshot) -> str:
    if snapshot.operation_state is OperationState.MERGE_PAUSED:
        return snapshot.merge_head or "merge-in-progress"
    if snapshot.operation_state is OperationState.REBASE_PAUSED:
        return snapshot.rebase_head or "rebase-in-progress"
    if snapshot.operation_state is OperationState.UNKNOWN:
        return "unreadable"
    return "none"


def _unmerged_identity(snapshot: OperationSnapshot) -> str:
    if not snapshot.unmerged_query_ok:
        return "unreadable"
    return ",".join(sorted(snapshot.unmerged_paths))
