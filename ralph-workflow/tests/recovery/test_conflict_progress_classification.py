from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.failure_classifier import FailureClassifier

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_progress_remaining_is_non_penalising_and_attempt_failed_charges_budget": (
        "UNRESOLVED_REMAINING is the justified progress case; ATTEMPT_FAILED must charge "
        "the shared budget so it cannot escape cooldown as neither unavailable nor charged"
    ),
}


def test_conflict_progress_is_finalized_by_the_shared_attempt_path() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))

    outcome = controller.finalize_attempt(
        PHASE_RESOLUTION,
        "primary",
        facts=AttemptFacts(artifact_valid=False, workspace_changed=True),
        attempt_failed=True,
    )

    assert outcome.kind is AttemptOutcomeKind.UNKNOWN
    assert controller.last_attempt_outcome is outcome


def test_valid_conflict_result_is_shared_completion() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))

    outcome = controller.finalize_attempt(
        PHASE_RESOLUTION,
        "primary",
        facts=AttemptFacts(artifact_valid=True, workspace_changed=True),
        attempt_failed=False,
    )

    assert outcome.kind is AttemptOutcomeKind.COMPLETED


def test_progress_remaining_is_non_penalising_and_attempt_failed_charges_budget() -> None:
    classifier = FailureClassifier()
    progress = classifier.classify_outcome(
        ConflictAttemptOutcome.UNRESOLVED_REMAINING,
        phase=PHASE_RESOLUTION,
        agent="primary",
        unresolved_count=2,
    )
    failed = classifier.classify_outcome(
        ConflictAttemptOutcome.ATTEMPT_FAILED,
        phase=PHASE_RESOLUTION,
        agent="primary",
    )

    assert (progress.counts_against_budget, progress.is_unavailable) == (False, False)
    assert (failed.counts_against_budget, failed.is_unavailable) == (True, False)
