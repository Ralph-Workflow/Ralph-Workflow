"""Exhausted-analysis-phase-advance pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
else:
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase


@dataclass(frozen=True)
class ExhaustedAnalysisPhaseAdvanceEffect:
    """Effect to bypass an already exhausted analysis phase through PHASE_ADVANCE.

    Used when the runner is already sitting in an exhausted analysis phase. The
    runner must not invoke the analysis agent again; instead it emits
    ``PHASE_ADVANCE`` so reducer-owned success routing, loop resets, and phase
    advancement remain the single source of truth.
    """

    phase: PipelinePhase
