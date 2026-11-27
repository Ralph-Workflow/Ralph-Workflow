from __future__ import annotations

from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)


def _reason(outcome: ConflictAttemptOutcome) -> UnavailabilityReason | None:
    failure = FailureClassifier().classify_outcome(
        outcome,
        phase="rebase_conflict_resolution",
        agent="resolver",
        unresolved_count=2,
    )
    return failure.unavailability_reason


def test_declaring_success_with_conflicts_left_is_not_called_silence() -> None:
    reason = _reason(ConflictAttemptOutcome.STALLED_NO_PROGRESS)

    assert reason is not UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY
    assert reason == "incomplete_resolution"


def test_the_incomplete_resolution_reason_has_a_backoff_policy() -> None:
    reason = UnavailabilityReason("incomplete_resolution")

    assert reason in DEFAULT_UNAVAILABILITY_BACKOFF_POLICY


def test_a_resolver_that_went_quiet_is_still_named_silence() -> None:
    assert _reason(ConflictAttemptOutcome.STALLED) is UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY
