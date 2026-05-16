"""Exhausted-analysis skip details."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase


@dataclass(frozen=True)
class ExhaustedAnalysisSkip:
    """Details for a single exhausted analysis phase that was bypassed."""

    phase: PipelinePhase
    target_phase: PipelinePhase
    iteration_field: str
    iteration_value: int
    max_iterations: int


__all__ = ["ExhaustedAnalysisSkip"]
