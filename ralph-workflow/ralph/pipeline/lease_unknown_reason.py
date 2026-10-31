"""Environment failures that leave the integration lease unclassified.

An :attr:`~ralph.pipeline.lease_outcome_kind.LeaseOutcomeKind.UNKNOWN`
lease outcome always carries one of these reasons, so a broken git dir or
lock-file I/O error is never reported as contention.
"""

from __future__ import annotations

from enum import Enum, auto


class LeaseUnknownReason(Enum):
    """Why the lease state could not be classified (environment failures only)."""

    GIT_DIR_UNRESOLVED = auto()  # `rev-parse --absolute-git-dir` failed or was empty
    GIT_DIR_INVALID = auto()  # `.git` directory present without a `HEAD` marker
    LOCK_IO_ERROR = auto()  # OSError opening the advisory lock file


__all__ = ["LeaseUnknownReason"]
