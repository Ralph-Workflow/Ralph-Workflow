"""Exhausted-analysis bypass result model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
    from ralph.pipeline.exhausted_analysis_skip import ExhaustedAnalysisSkip
    from ralph.pipeline.state import PipelineState


@dataclass(frozen=True)
class ExhaustedAnalysisBypassResult:
    """Resolved exhausted-analysis bypass outcome for a phase handoff."""

    state: PipelineState
    target_phase: PipelinePhase
    skipped: tuple[ExhaustedAnalysisSkip, ...] = ()


__all__ = ["ExhaustedAnalysisBypassResult"]
