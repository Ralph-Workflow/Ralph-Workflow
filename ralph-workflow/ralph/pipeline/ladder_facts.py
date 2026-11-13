from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline._integration_ladder_seams import LadderOutcome
    from ralph.pipeline.integration_decision import CleanupLadderRung
    from ralph.pipeline.landed_verdict import LandedVerdict

__all__ = ("LadderFacts",)


@dataclass(frozen=True, slots=True)
class LadderFacts:
    rung: CleanupLadderRung | None
    outcome: LadderOutcome | None
    reason: str
    landed: LandedVerdict
    enabled_rungs: frozenset[CleanupLadderRung]
    terminal_for_attempt: bool
    clock_now: float
