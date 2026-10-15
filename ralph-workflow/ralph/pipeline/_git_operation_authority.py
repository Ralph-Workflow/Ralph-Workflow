"""Single authority for classifying which git operation a durable record owns.

The pending-integration modules keep their model fields for
checkpoint compatibility, but every decision about which operation
is in flight (and therefore which fields may be mutated or cleared)
is derived here via :func:`classify_git_operation`. No caller may
re-derive the operation kind by hand-rolled boolean combinations of
the record's flags.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord


class GitOperationKind(StrEnum):
    """The closed vocabulary of in-flight git operations."""

    RESOLVING_REBASE = "resolving_rebase"
    RESOLVING_MERGE = "resolving_merge"
    AWAITING_CONTINUE = "awaiting_continue"
    AWAITING_MERGE_COMMIT = "awaiting_merge_commit"
    NONE = "none"


def classify_git_operation(record: IntegrationRecord) -> GitOperationKind:
    """Map a durable record's operation flags to exactly one kind.

    Priority follows danger of a concurrent continuation: an armed
    continuation/commit pending flag wins over the resolver-only
    flags; the prepared tree alone (crash between snapshot and
    arming) still counts as awaiting its commit.
    """
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


__all__ = [
    "GitOperationKind",
    "classify_git_operation",
]
