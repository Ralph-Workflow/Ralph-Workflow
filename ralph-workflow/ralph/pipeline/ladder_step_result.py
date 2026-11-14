from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline._integration_ladder_seams import LadderOutcome, RungPostCondition
    from ralph.pipeline.integration_decision import CleanupLadderRung

__all__ = ("LadderStepResult",)


@dataclass(frozen=True)
class LadderStepResult:
    rung: CleanupLadderRung
    outcome: LadderOutcome
    reason: str
    preserved_ref: str | None = None
    preserved_sidecar: str | None = None
    quarantined_paths: tuple[str, ...] = ()
    next_rung: CleanupLadderRung | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    post_condition: RungPostCondition | None = None
    post_condition_verified: bool = False
