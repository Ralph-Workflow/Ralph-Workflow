from __future__ import annotations

from ralph.git.rebase._rebase_continuation_error import RebaseContinuationError


class ConflictRemainingError(RebaseContinuationError):
    pass


__all__ = ["ConflictRemainingError"]
