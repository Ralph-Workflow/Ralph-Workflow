from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )

__all__ = ["RebaseResolutionResult"]


@dataclass(frozen=True, slots=True, init=False)
class RebaseResolutionResult:
    outcome: StopResolutionOutcome
    reason: str | None
    failure_cause: ResolutionFailureCause
    terminal_reason: ResolutionTerminationReason | None

    def __init__(
        self,
        succeeded: bool,
        reason: str | None,
        failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE,
        terminal_reason: ResolutionTerminationReason | None = None,
    ) -> None:
        outcome: StopResolutionOutcome
        if succeeded:
            outcome = ResolvedOutcome()
        else:
            outcome = RetryLaterOutcome(on_change=reason or "resolution_state_changed")
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "failure_cause", failure_cause)
        object.__setattr__(self, "terminal_reason", terminal_reason)

    @property
    def succeeded(self) -> bool:
        match self.outcome:
            case ResolvedOutcome():
                return True
            case (
                WaitingOutcome()
                | RetryLaterOutcome()
                | NextStrategyOutcome()
                | ConfigFailureOutcome()
                | UnknownOutcome()
            ):
                return False
