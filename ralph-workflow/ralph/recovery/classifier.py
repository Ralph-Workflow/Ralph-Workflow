"""Failure classification: categorize exceptions for intelligent attribution."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import (
    FailureClassifier,
    SESSION_NOT_FOUND_SUBSTRINGS,
    is_missing_artifact_message,
)

__all__ = [
    "ClassifiedFailure",
    "FailureCategory",
    "FailureClassifier",
    "FailureContext",
    "SESSION_NOT_FOUND_SUBSTRINGS",
    "is_missing_artifact_message",
    "is_retryable_without_budget",
]


@dataclass(frozen=True)
class FailureContext:
    """Context for a failure event passed to RecoveryController.handle."""

    FailureCategory = FailureCategory
    ClassifiedFailure = ClassifiedFailure
    FailureClassifier = FailureClassifier

    phase: str
    agent: str | None = None
    retry_in_session: bool = False
    classified_failure: ClassifiedFailure | None = None


def is_retryable_without_budget(failure: ClassifiedFailure) -> bool:
    """Return True if this failure should retry without debiting the agent budget.

    Environmental, artifact-validation, and ambiguous failures retry without
    counting. Agent and user_config failures consume budget.
    """
    return not failure.counts_against_budget
