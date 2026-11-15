from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import (
    SESSION_NOT_FOUND_SUBSTRINGS,
    FailureClassifier,
    is_missing_artifact_message,
)

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import AttemptFacts

__all__ = [
    "SESSION_NOT_FOUND_SUBSTRINGS",
    "ClassifiedFailure",
    "ConflictAttemptOutcome",
    "FailureCategory",
    "FailureClassifier",
    "FailureContext",
    "is_missing_artifact_message",
    "is_retryable_without_budget",
]


@dataclass(frozen=True)
class FailureContext:
    phase: str
    agent: str | None = None
    retry_in_session: bool = False
    classified_failure: ClassifiedFailure | None = None
    facts: AttemptFacts | None = None
    outcome: ConflictAttemptOutcome | str | None = None
    unresolved_count: int = 0
    raw_failure: BaseException | str | None = None

    def __post_init__(self) -> None:
        if (
            self.classified_failure is None
            and self.outcome is not None
            and self.outcome not in (ConflictAttemptOutcome.RAW, "raw")
        ):
            object.__setattr__(
                self,
                "classified_failure",
                FailureClassifier().classify_outcome(
                    self.outcome,
                    phase=self.phase,
                    agent=self.agent,
                    unresolved_count=self.unresolved_count,
                    raw_failure=self.raw_failure,
                ),
            )


def is_retryable_without_budget(failure: ClassifiedFailure) -> bool:
    return not failure.counts_against_budget
