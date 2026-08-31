"""EmptyReplayError -- raised when skipping an emptied replay would drop it."""

from __future__ import annotations

from ralph.git.rebase._rebase_continuation_error import RebaseContinuationError


class EmptyReplayError(RebaseContinuationError):
    """The replayed commit became empty, and skipping it would drop it."""


__all__ = ["EmptyReplayError"]
