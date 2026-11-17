from __future__ import annotations

from ralph.pipeline.conflict_resolution.attempt_fault import (
    AttemptFault,
    classify_attempt_fault,
)


def test_none_authorizes_agent_healthy() -> None:
    assert classify_attempt_fault(None) is AttemptFault.AGENT_HEALTHY


def test_typed_healthy_round_trips() -> None:
    assert classify_attempt_fault(AttemptFault.AGENT_HEALTHY) is AttemptFault.AGENT_HEALTHY


def test_classify_attempt_fault_rejects_untyped_prose_evidence() -> None:
    assert classify_attempt_fault("healthy") is AttemptFault.UNKNOWN  # type: ignore[arg-type]
    assert classify_attempt_fault("unhealthy environment") is AttemptFault.UNKNOWN  # type: ignore[arg-type]
    assert classify_attempt_fault("agent is healthy enough") is AttemptFault.UNKNOWN  # type: ignore[arg-type]
