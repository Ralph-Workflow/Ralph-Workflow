from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )

__all__ = ["RebaseResolutionResult"]

_RESULT_LEN = 3


def _is_outcome_success(outcome: object) -> bool:
    return isinstance(outcome, ResolvedOutcome) or outcome is True


@dataclass(frozen=True, slots=True, init=False)
class RebaseResolutionResult:
    outcome: StopResolutionOutcome
    reason: str | None
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    terminal_reason: ResolutionTerminationReason | None = None
    _raw_succeeded: bool | None = None

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
        raw_succ: bool | None = None
        if outcome is not None:
            eff_outcome = outcome
            if succeeded is not None:
                raw_succ = succeeded
        elif succeeded is not None:
            raw_succ = succeeded
            eff_outcome = (
                ResolvedOutcome()
                if succeeded
                else RetryLaterOutcome(on_change=reason or "resolution_state_changed")
            )
        elif isinstance(outcome_or_succeeded, bool):
            raw_succ = outcome_or_succeeded
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
        object.__setattr__(self, "_raw_succeeded", raw_succ)

    def _succeeded(self) -> bool:
        if self._raw_succeeded is not None:
            return self._raw_succeeded
        return bool(self.outcome)

    def __len__(self) -> int:
        return _RESULT_LEN

    @property
    def succeeded(self) -> bool:
        return _is_outcome_success(self.outcome)

    def __iter__(self) -> Iterator[object]:
        return iter((self._succeeded(), self.reason, self.failure_cause))

    def __getitem__(self, index: int | slice[int | None, int | None, int | None]) -> object:
        items = (self._succeeded(), self.reason, self.failure_cause)
        return items[index]

    def __bool__(self) -> bool:
        return self._succeeded()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple) and len(other) == _RESULT_LEN:
            return other == (_is_outcome_success(self.outcome), self.reason, self.failure_cause)
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
