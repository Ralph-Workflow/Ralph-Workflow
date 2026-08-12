"""Broken agents are unavailable and reset their session."""

from __future__ import annotations

from ralph.agents.invoke import BrokenAgentExitError
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)


def test_broken_agent_is_classified_as_unavailable_with_short_backoff() -> None:
    failure = FailureClassifier().classify(
        BrokenAgentExitError("claude", reason="no_output"),
        phase="development",
        agent="claude",
        connectivity_state="offline",
    )

    assert failure.is_unavailable is True
    assert failure.reset_session is True
    assert failure.unavailability_reason == UnavailabilityReason.BROKEN_AGENT
    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[UnavailabilityReason.BROKEN_AGENT]
    assert policy.base_backoff_ms == 5_000
    assert policy.max_backoff_ms == 60_000
