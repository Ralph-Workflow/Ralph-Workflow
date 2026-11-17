from __future__ import annotations

from ralph.pipeline.conflict_resolution.attempt_fault import (
    AttemptFault,
    classify_attempt_fault,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_classify_attempt_fault_rejects_untyped_prose_evidence": (
        "the sealed-outcome migration removed text evidence from classify_attempt_fault; "
        "7360f33bf restored the pre-migration prose-matching tests, which contradict "
        "tests/test_sealed_outcome_migration.py, so the typed-only contract is restored "
        "without the banned inline type-ignore suppressions"
    ),
    "test_typed_healthy_round_trips": (
        "restored with the typed-only contract that the sealed-outcome migration pins"
    ),
    "test_substring_healthy_does_not_authorize_agent_healthy": (
        "removed: it required prose classification, which the sealed-outcome migration retired"
    ),
    "test_substring_healthy_does_not_authorize_count": (
        "removed with the retired prose-classification parametrisation it counted"
    ),
    "test_exact_healthy_authorizes_agent_healthy": (
        "removed: text evidence is rejected as UNKNOWN under the sealed-outcome contract"
    ),
    "test_healthy_uppercase_is_exact_match": (
        "removed: text evidence is rejected as UNKNOWN under the sealed-outcome contract"
    ),
    "test_healthy_with_whitespace_padded_is_stripped_then_exact_match": (
        "removed: text evidence is rejected as UNKNOWN under the sealed-outcome contract"
    ),
}


def test_none_authorizes_agent_healthy() -> None:
    assert classify_attempt_fault(None) is AttemptFault.AGENT_HEALTHY


def test_typed_healthy_round_trips() -> None:
    assert classify_attempt_fault(AttemptFault.AGENT_HEALTHY) is AttemptFault.AGENT_HEALTHY


def test_classify_attempt_fault_rejects_untyped_prose_evidence() -> None:
    assert classify_attempt_fault("healthy") is AttemptFault.UNKNOWN
    assert classify_attempt_fault("unhealthy environment") is AttemptFault.UNKNOWN
    assert classify_attempt_fault("agent is healthy enough") is AttemptFault.UNKNOWN
