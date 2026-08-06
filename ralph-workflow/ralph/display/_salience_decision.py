"""One role's per-frame allocator outcome (PLAN.md G-9).

Split from :mod:`ralph.display._salience` so that module keeps a single
top-level class (``SalienceAllocator``) per the repo's one-class-per-file
structure policy (``ralph.testing.audit_repo_structure``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display._frequency_tier import FrequencyTier


@dataclass(frozen=True)
class AllocationDecision:
    """G-9: one role's allocator outcome for one frame, inspectable as
    data -- callers assert on this, never on allocator internals (F-4).

    ``frame_index`` (PLAN.md S-1) is the ``SalienceAllocator.frame_index``
    value of the ``allocate_frame`` call that produced this decision, so a
    caller holding a flat, chronologically-ordered sequence of decisions
    (see ``ParallelDisplay.salience_decisions``) can regroup them by the
    real frame that decided them -- the batch of concurrently-pending bids
    one ``allocate_frame`` call arbitrated between -- without threading a
    separate index alongside the decision stream.
    """

    role: str
    tier: FrequencyTier
    lit: bool
    reason: str
    frame_index: int


__all__ = ["AllocationDecision"]
