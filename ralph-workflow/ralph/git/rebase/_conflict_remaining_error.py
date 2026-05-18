"""ConflictRemainingError — raised when conflicts remain while attempting to continue."""

from __future__ import annotations

from ralph.git.rebase._rebase_continuation_error import RebaseContinuationError


class ConflictRemainingError(RebaseContinuationError):
    """Raised when conflicts remain while attempting to continue."""


__all__ = ["ConflictRemainingError"]
