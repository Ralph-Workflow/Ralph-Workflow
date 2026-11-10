"""Public result type for the rebase-resolution transport.

One rebase-resolution attempt answers three questions: did the rebase
complete, what terminal evidence did the resolver chain leave when it
did not, and what typed root cause produced that evidence. The cause is
what tells a broken configuration (``CONFIG_FAILURE``) apart from an
unresolved conflict, so it travels with the outcome instead of being
re-derived from the reason text at the far end of the chain.
"""

from __future__ import annotations

from typing import NamedTuple

from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["RebaseResolutionResult"]


class RebaseResolutionResult(NamedTuple):
    """The outcome of one rebase-resolution attempt.

    Attributes:
        succeeded: ``True`` only when the rebase completed.
        reason: The terminal resolver-chain evidence when the rebase did
            not complete; ``None`` when it did.
        failure_cause: The typed root cause of the failure.
            ``ResolutionFailureCause.NONE`` when the attempt succeeded or
            when no cause was recorded.
    """

    succeeded: bool
    reason: str | None
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
