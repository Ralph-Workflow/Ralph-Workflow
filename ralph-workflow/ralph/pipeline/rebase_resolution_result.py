from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.resolution_outcome import ResolvedOutcome, RetryLaterOutcome

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome

__all__ = ["RebaseResolutionResult"]

_RESULT_LEN = 3


@dataclass(frozen=True, slots=True, init=False)
class RebaseResolutionResult:
    outcome: StopResolutionOutcome
    reason: str | None
    failure_cause: ResolutionFailureCause
    terminal_reason: ResolutionTerminationReason | None

    def __init__(
        self,
        outcome_or_succeeded: StopResolutionOutcome | bool = False,
        reason: str | None = None,
        failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE,
        terminal_reason: ResolutionTerminationReason | None = None,
        *,
        outcome: StopResolutionOutcome | None = None,
        succeeded: bool | None = None,
    ) -> None:
        eff_outcome: StopResolutionOutcome
        if outcome is not None:
            eff_outcome = outcome
        elif succeeded is not None:
            eff_outcome = (
                ResolvedOutcome()
                if succeeded
                else RetryLaterOutcome(on_change=reason or "resolution_state_changed")
            )
        elif isinstance(outcome_or_succeeded, bool):
            eff_outcome = (
                ResolvedOutcome()
                if outcome_or_succeeded
                else RetryLaterOutcome(on_change=reason or "resolution_state_changed")
            )
        else:
            eff_outcome = outcome_or_succeeded

        object.__setattr__(self, "outcome", eff_outcome)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "failure_cause", failure_cause)
        object.__setattr__(self, "terminal_reason", terminal_reason)

    def __len__(self) -> int:
        return _RESULT_LEN

    def __iter__(self) -> Iterator[bool | str | None | ResolutionFailureCause]:
        yield bool(self.outcome)
        yield self.reason
        yield self.failure_cause

    def __getitem__(self, index: int) -> object:
        return (bool(self.outcome), self.reason, self.failure_cause)[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple) and len(other) == _RESULT_LEN:
            return other == (bool(self.outcome), self.reason, self.failure_cause)
        if isinstance(other, RebaseResolutionResult):
            return (
                self.outcome,
                self.reason,
                self.failure_cause,
                self.terminal_reason,
            ) == (
                other.outcome,
                other.reason,
                other.failure_cause,
                other.terminal_reason,
            )
        return False

    def __hash__(self) -> int:
        return hash((self.outcome, self.reason, self.failure_cause, self.terminal_reason))
