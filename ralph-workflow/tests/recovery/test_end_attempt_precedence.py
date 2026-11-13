from __future__ import annotations

from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.end_attempt import end_attempt


def test_valid_artifact_failure_beats_retryable_hint() -> None:
    outcome = end_attempt(
        AttemptFacts(artifact_valid=True),
        attempt_failed=True,
        retryable_reason="inactivity",
    )

    assert outcome.kind is AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE
    assert outcome.benches_agent is False
    assert outcome.retries_same_agent is False
    assert outcome.counts_toward_agent_state is False


def test_identical_peer_failures_beats_retryable_hint() -> None:
    outcome = end_attempt(
        AttemptFacts(event_kinds=("unseen_failure",)),
        attempt_failed=True,
        peer_failures_identical=True,
        retryable_reason="inactivity",
    )

    assert outcome.kind is AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE
    assert outcome.benches_agent is False
    assert outcome.retries_same_agent is False
    assert outcome.counts_toward_agent_state is False


def test_retryable_hint_still_applies_without_task_or_environment_evidence() -> None:
    outcome = end_attempt(
        AttemptFacts(),
        attempt_failed=True,
        retryable_reason="inactivity",
    )

    assert outcome.kind is AttemptOutcomeKind.RETRYABLE_FAILURE
    assert outcome.retries_same_agent is True
