"""RecoveryAction — decision enum guiding error recovery in a rebase."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from ralph.git.rebase._rebase_kind import RebaseKind

if TYPE_CHECKING:
    from ralph.git.rebase.rebase_kinds import RebaseErrorKind


class RecoveryAction(Enum):
    """Decision returned by ``decide`` to guide error recovery in a rebase."""

    Continue = "continue"
    Retry = "retry"
    Abort = "abort"
    Skip = "skip"

    @staticmethod
    def decide(error_kind: RebaseErrorKind, error_count: int, max_attempts: int) -> RecoveryAction:
        if error_count >= max_attempts:
            return RecoveryAction.Abort
        kind = error_kind.kind
        if kind == RebaseKind.CONTENT_CONFLICT:
            return RecoveryAction.Continue
        if kind in {
            RebaseKind.CONCURRENT_OPERATION,
            RebaseKind.PATCH_APPLICATION_FAILED,
            RebaseKind.AUTOSTASH_FAILED,
            RebaseKind.COMMIT_CREATION_FAILED,
            RebaseKind.REFERENCE_UPDATE_FAILED,
        }:
            return RecoveryAction.Retry
        if kind == RebaseKind.EMPTY_COMMIT:
            return RecoveryAction.Skip
        return RecoveryAction.Abort


__all__ = ["RecoveryAction"]
