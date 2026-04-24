"""Black-box tests: stale-session failures are classified with reset_session=True."""

from __future__ import annotations

from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.recovery.classifier import FailureCategory, FailureClassifier


class _AgentInvocationError(Exception):
    """Simulates AgentInvocationError via class name."""


_AgentInvocationError.__name__ = "AgentInvocationError"


class _AgentInactivityTimeoutError(_AgentInvocationError):
    """Simulates AgentInactivityTimeoutError via class name."""


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_stale_session_message_sets_reset_session_true() -> None:
    """AgentInvocationError with stale-session substring sets reset_session=True."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef"
    )
    failure = classifier.classify(exc, phase=PHASE_DEVELOPMENT, agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is True


def test_stale_session_attributed_to_agent() -> None:
    """Stale-session failure is attributed to the agent, not environmental."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: 8e9806b7-8bcd"
    )
    failure = classifier.classify(exc, phase=PHASE_DEVELOPMENT, agent="claude")

    assert failure.attributed_agent == "claude"
    assert failure.attributed_phase == PHASE_DEVELOPMENT


def test_non_stale_session_invocation_error_reset_session_false() -> None:
    """AgentInvocationError without the stale-session substring keeps reset_session=False."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError("Agent 'claude' failed with code 1: some other error")
    failure = classifier.classify(exc, phase=PHASE_DEVELOPMENT, agent="claude")

    assert failure.reset_session is False


def test_inactivity_timeout_reset_session_false() -> None:
    """AgentInactivityTimeoutError is unaffected by stale-session logic."""
    classifier = FailureClassifier()
    exc = _AgentInactivityTimeoutError("agent idle for too long")
    failure = classifier.classify(exc, phase=PHASE_DEVELOPMENT, agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.reset_session is False


def test_environmental_error_reset_session_false() -> None:
    """Environmental failures never set reset_session."""
    classifier = FailureClassifier()
    failure = classifier.classify(
        ConnectionRefusedError("connection refused"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.reset_session is False


def test_stale_session_counts_against_budget() -> None:
    """Stale-session failures consume agent retry budget."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'opencode' failed with code 1: No conversation found with session ID: abc123"
    )
    failure = classifier.classify(exc, phase=PHASE_DEVELOPMENT, agent="opencode")

    assert failure.counts_against_budget is True
