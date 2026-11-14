from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


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
