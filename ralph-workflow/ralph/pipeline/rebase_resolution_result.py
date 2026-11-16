from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome

__all__ = ["RebaseResolutionResult"]


@dataclass(frozen=True, slots=True)
class RebaseResolutionResult:
    outcome: StopResolutionOutcome
    reason: str | None
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    terminal_reason: ResolutionTerminationReason | None = None
