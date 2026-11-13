from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.integration_decision import CleanupLadderRung
    from ralph.pipeline.ladder_effect_kind import LadderEffectKind

__all__ = ("LadderEffect",)


@dataclass(frozen=True, slots=True)
class LadderEffect:
    kind: LadderEffectKind
    rung: CleanupLadderRung | None = None
