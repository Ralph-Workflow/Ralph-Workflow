"""Closed set of outcomes for the exclusive integration worktree lease.

:func:`ralph.pipeline.auto_integrate_transaction.integration_transaction`
classifies every lease attempt into exactly one of these kinds, carried on
:class:`~ralph.pipeline.auto_integrate_transaction.LeaseOutcome`.
"""

from __future__ import annotations

from enum import Enum, auto


class LeaseOutcomeKind(Enum):
    """Closed set of lease outcomes.

    ``ACQUIRED`` — lease held; caller owns the worktree for the block.
    ``HELD_BY_LIVE_OWNER`` — another live process holds the advisory lock
    (real contention; bounded retry applies, and only here).
    ``UNKNOWN`` — an environment failure prevented classification; the
    caller must never treat this as "busy" and must never dispatch an
    agent for it: back off via the injectable clock, re-observe, and let
    the existing fallback ladder handle persistence past the bound.
    """

    ACQUIRED = auto()
    HELD_BY_LIVE_OWNER = auto()
    UNKNOWN = auto()


__all__ = ["LeaseOutcomeKind"]
