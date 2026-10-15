"""Tests for conflict-resolution AttemptFault typing and classification."""

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


def test_attempt_fault_enum_values() -> None:
    """Verify that all AttemptFault values exist."""
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
    """Round-trip: classify_attempt_fault(fault) is fault."""
    assert classify_attempt_fault(fault) is fault
    assert classify_attempt_fault(fault.value) is fault
    assert classify_attempt_fault(fault.name) is fault


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
    """Round-trip from AttemptFault to ResolutionTerminationReason and back."""
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
    """Test that typed classification matches existing ResolutionTerminationReason."""
    assert classify_attempt_fault(reason) is expected_fault
    assert classify_attempt_fault(reason.value) is expected_fault


def test_classify_attempt_fault_from_payload_strings() -> None:
    """Classify attempt faults from evidence strings."""
    assert (
        classify_attempt_fault("transport_loop_detected")
        is AttemptFault.AGENT_INFRASTRUCTURE_FAILURE
    )
    assert (
        classify_attempt_fault("SUPERVISION_INFRASTRUCTURE_FAILURE")
        is AttemptFault.AGENT_INFRASTRUCTURE_FAILURE
    )
    assert (
        classify_attempt_fault("candidate produced no activity before it exited")
        is AttemptFault.AGENT_EXITED
    )
    assert classify_attempt_fault("candidate_unavailable") is AttemptFault.AGENT_TOOL_DEAD
    assert classify_attempt_fault("tool_surface_dead") is AttemptFault.AGENT_TOOL_DEAD
    assert classify_attempt_fault("attempt failed") is AttemptFault.UNKNOWN
    assert classify_attempt_fault(None) is AttemptFault.AGENT_HEALTHY
    assert classify_attempt_fault("") is AttemptFault.AGENT_HEALTHY
    assert classify_attempt_fault("healthy") is AttemptFault.AGENT_HEALTHY


def test_attempt_fault_to_termination_reason_with_specific_evidence() -> None:
    """Specific Ralph origin evidence maps to the exact infrastructure reason."""
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
