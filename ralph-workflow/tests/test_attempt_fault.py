from __future__ import annotations

import pytest

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    AttemptFault,
    attempt_fault_to_termination_reason,
    classify_attempt_fault,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.classifier import FailureContext
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import UnavailabilityReason


def test_attempt_fault_enum_values() -> None:
    expected = {
        "AGENT_HEALTHY",
        "AGENT_EXITED",
        "AGENT_INFRASTRUCTURE_FAILURE",
        "AGENT_TOOL_DEAD",
        "UNKNOWN",
    }
    assert {fault.value for fault in AttemptFault} == expected


@pytest.mark.parametrize(
    "fault",
    [
        AttemptFault.AGENT_HEALTHY,
        AttemptFault.AGENT_EXITED,
        AttemptFault.AGENT_INFRASTRUCTURE_FAILURE,
        AttemptFault.AGENT_TOOL_DEAD,
        AttemptFault.UNKNOWN,
    ],
)
def test_classify_attempt_fault_round_trip_enum(fault: AttemptFault) -> None:
    assert classify_attempt_fault(fault) is fault


@pytest.mark.parametrize(
    ("fault", "expected_reason"),
    [
        (AttemptFault.AGENT_HEALTHY, None),
        (AttemptFault.AGENT_EXITED, ResolutionTerminationReason.CANDIDATE_EXITED),
        (
            AttemptFault.AGENT_INFRASTRUCTURE_FAILURE,
            ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ),
        (AttemptFault.AGENT_TOOL_DEAD, ResolutionTerminationReason.TOOL_SURFACE_DEAD),
        (AttemptFault.UNKNOWN, ResolutionTerminationReason.ATTEMPT_FAILED),
    ],
)
def test_attempt_fault_termination_reason_round_trip(
    fault: AttemptFault,
    expected_reason: ResolutionTerminationReason | None,
) -> None:
    reason = attempt_fault_to_termination_reason(fault)
    assert reason == expected_reason
    classified = classify_attempt_fault(reason)
    assert classified is fault


@pytest.mark.parametrize(
    ("reason", "expected_fault"),
    [
        (
            ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
            AttemptFault.AGENT_INFRASTRUCTURE_FAILURE,
        ),
        (
            ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
            AttemptFault.AGENT_INFRASTRUCTURE_FAILURE,
        ),
        (
            ResolutionTerminationReason.TOOL_SURFACE_DEAD,
            AttemptFault.AGENT_TOOL_DEAD,
        ),
        (
            ResolutionTerminationReason.CANDIDATE_UNAVAILABLE,
            AttemptFault.AGENT_TOOL_DEAD,
        ),
        (
            ResolutionTerminationReason.CANDIDATE_EXITED,
            AttemptFault.AGENT_EXITED,
        ),
        (
            ResolutionTerminationReason.ATTEMPT_FAILED,
            AttemptFault.UNKNOWN,
        ),
    ],
)
def test_classify_attempt_fault_matches_existing_termination_reasons(
    reason: ResolutionTerminationReason,
    expected_fault: AttemptFault,
) -> None:
    assert classify_attempt_fault(reason) is expected_fault


def test_classify_attempt_fault_defaults_without_typed_evidence() -> None:
    assert classify_attempt_fault(None) is AttemptFault.AGENT_HEALTHY


def test_candidate_exited_matches_shared_no_activity_classification() -> None:
    shared = FailureContext(
        phase=PHASE_RESOLUTION,
        agent="resolver",
        outcome=ConflictAttemptOutcome.NO_ACTIVITY,
    ).classified_failure
    assert shared is not None
    assert shared.unavailability_reason is UnavailabilityReason.NO_OUTPUT_AT_START
    assert (
        classify_attempt_fault(ResolutionTerminationReason.CANDIDATE_EXITED)
        is AttemptFault.AGENT_EXITED
    )


def test_candidate_exited_classifies_through_failure_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[ConflictAttemptOutcome | str] = []
    real = FailureClassifier.classify_outcome

    def _spy(
        self: FailureClassifier,
        outcome: ConflictAttemptOutcome | str,
        **kwargs: object,
    ) -> object:
        outcomes.append(outcome)
        return real(self, outcome, **kwargs)

    monkeypatch.setattr(FailureClassifier, "classify_outcome", _spy)

    assert (
        classify_attempt_fault(ResolutionTerminationReason.CANDIDATE_EXITED)
        is AttemptFault.AGENT_EXITED
    )
    assert outcomes == [ConflictAttemptOutcome.NO_ACTIVITY]


def test_attempt_failed_classifies_through_failure_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[ConflictAttemptOutcome | str] = []
    real = FailureClassifier.classify_outcome

    def _spy(
        self: FailureClassifier,
        outcome: ConflictAttemptOutcome | str,
        **kwargs: object,
    ) -> object:
        outcomes.append(outcome)
        return real(self, outcome, **kwargs)

    monkeypatch.setattr(FailureClassifier, "classify_outcome", _spy)

    assert (
        classify_attempt_fault(ResolutionTerminationReason.ATTEMPT_FAILED)
        is AttemptFault.UNKNOWN
    )
    assert outcomes == [ConflictAttemptOutcome.ATTEMPT_FAILED]


def test_attempt_fault_to_termination_reason_with_specific_evidence() -> None:
    assert (
        attempt_fault_to_termination_reason(
            AttemptFault.AGENT_INFRASTRUCTURE_FAILURE, "transport_loop_detected"
        )
        == ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    )
    assert (
        attempt_fault_to_termination_reason(
            AttemptFault.AGENT_INFRASTRUCTURE_FAILURE, "SUPERVISION_INFRASTRUCTURE_FAILURE"
        )
        == ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    )
