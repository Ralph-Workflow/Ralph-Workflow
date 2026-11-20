from __future__ import annotations

from ralph.pipeline.conflict_resolution import attempt_fault
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    BUDGET_UNCHARGED_TERMINATION_REASONS,
    INFRASTRUCTURE_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.classifier import FailureContext
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.unavailability_reason import UnavailabilityReason

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_attempt_fault_module_publishes_no_failure_vocabulary": (
        "the resolver's second failure vocabulary was deleted with the shared agent path; the "
        "module now publishes the resolver's outcome-reason sets only and the test pins that "
        "no failure classifier survives there"
    ),
    "test_infrastructure_reasons_are_budget_uncharged": (
        "replaces the deleted AttemptFault round trip with the surviving outcome-vocabulary "
        "invariant: every infrastructure reason charges no conflict budget"
    ),
    "test_no_activity_outcome_keeps_the_shared_no_output_at_start_reason": (
        "the shared ClassifiedFailure is consumed as-is; the test pins the shared answer the "
        "resolver records for a candidate that produced no activity"
    ),
    "test_unresolved_remaining_outcome_keeps_the_shared_no_unavailability_reason": (
        "the shared ClassifiedFailure is consumed as-is; the test pins the shared answer for a "
        "reported success with conflicted paths left unresolved"
    ),
    "test_attempt_failed_outcome_keeps_the_shared_no_unavailability_reason": (
        "the shared ClassifiedFailure is consumed as-is; the test pins the shared answer for a "
        "failed attempt"
    ),
}


def test_attempt_fault_module_publishes_no_failure_vocabulary() -> None:
    assert set(attempt_fault.__all__) == {
        "BUDGET_UNCHARGED_TERMINATION_REASONS",
        "INFRASTRUCTURE_TERMINATION_REASONS",
    }


def test_infrastructure_reasons_are_budget_uncharged() -> None:
    assert INFRASTRUCTURE_TERMINATION_REASONS <= BUDGET_UNCHARGED_TERMINATION_REASONS
    assert (
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
        in INFRASTRUCTURE_TERMINATION_REASONS
    )


def test_no_activity_outcome_keeps_the_shared_no_output_at_start_reason() -> None:
    classified = FailureContext(
        phase=PHASE_RESOLUTION,
        agent="resolver",
        outcome=ConflictAttemptOutcome.NO_ACTIVITY,
    ).classified_failure

    assert classified is not None
    assert classified.unavailability_reason is UnavailabilityReason.NO_OUTPUT_AT_START


def test_unresolved_remaining_outcome_keeps_the_shared_no_unavailability_reason() -> None:
    classified = FailureContext(
        phase=PHASE_RESOLUTION,
        agent="resolver",
        outcome=ConflictAttemptOutcome.UNRESOLVED_REMAINING,
        unresolved_count=2,
    ).classified_failure

    assert classified is not None
    assert classified.unavailability_reason is None


def test_attempt_failed_outcome_keeps_the_shared_no_unavailability_reason() -> None:
    classified = FailureContext(
        phase=PHASE_RESOLUTION,
        agent="resolver",
        outcome=ConflictAttemptOutcome.ATTEMPT_FAILED,
    ).classified_failure

    assert classified is not None
    assert classified.unavailability_reason is None
