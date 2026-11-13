from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.integration_decision import CleanupLadderRung
    from ralph.pipeline.ladder_effect import LadderEffect
    from ralph.pipeline.ladder_state import LadderState

__all__ = ("LadderTransition",)


@dataclass(frozen=True, slots=True)
class LadderTransition:
    state: LadderState
    terminal: bool
    reason: str
    next_rung: CleanupLadderRung | None
    effects: tuple[LadderEffect, ...]
