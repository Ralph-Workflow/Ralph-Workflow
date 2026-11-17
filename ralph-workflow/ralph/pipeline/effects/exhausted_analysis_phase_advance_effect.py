from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase


@dataclass(frozen=True)
class ExhaustedAnalysisPhaseAdvanceEffect:
    phase: PipelinePhase
