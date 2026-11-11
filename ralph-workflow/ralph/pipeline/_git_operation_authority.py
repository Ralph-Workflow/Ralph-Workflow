
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord


class GitOperationKind(StrEnum):

    RESOLVING_REBASE = "resolving_rebase"
    RESOLVING_MERGE = "resolving_merge"
    AWAITING_CONTINUE = "awaiting_continue"
    AWAITING_MERGE_COMMIT = "awaiting_merge_commit"
    NONE = "none"


def classify_git_operation(record: IntegrationRecord) -> GitOperationKind:
    if record.rebase_continue_pending:
        return GitOperationKind.AWAITING_CONTINUE
    if record.merge_commit_pending:
        return GitOperationKind.AWAITING_MERGE_COMMIT
    if record.resolving_rebase:
        return GitOperationKind.RESOLVING_REBASE
    if record.resolving_merge:
        return GitOperationKind.RESOLVING_MERGE
    if record.merge_commit_tree is not None:
        return GitOperationKind.AWAITING_MERGE_COMMIT
    return GitOperationKind.NONE


def is_in_active_resolution_state(record: IntegrationRecord) -> bool:
    kind = classify_git_operation(record)
    return kind in (GitOperationKind.RESOLVING_MERGE, GitOperationKind.RESOLVING_REBASE)


__all__ = [
    "GitOperationKind",
    "classify_git_operation",
    "is_in_active_resolution_state",
]
