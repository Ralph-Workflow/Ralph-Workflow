"""NoRebaseInProgressError — raised when no rebase is active but continuation was requested."""

from __future__ import annotations

from ralph.git.rebase._rebase_continuation_error import RebaseContinuationError


class NoRebaseInProgressError(RebaseContinuationError):
    """Raised when no rebase is active but continuation was requested."""


__all__ = ["NoRebaseInProgressError"]
