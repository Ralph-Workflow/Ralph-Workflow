"""Black-box tests: stale-session failures are classified with reset_session=True."""

from __future__ import annotations

from ralph.recovery.classifier import FailureCategory, FailureClassifier
from tests.recovery.test_classifier_session_helper__agentinactivitytimeouterror import (
    _AgentInactivityTimeoutError,
)


class _AgentInvocationError(Exception):
    """Simulates AgentInvocationError via class name."""


_AgentInvocationError.__name__ = "AgentInvocationError"
_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_stale_session_message_sets_reset_session_true() -> None:
    """AgentInvocationError with stale-session substring sets reset_session=True."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef"
    )
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is True


def test_stale_session_attributed_to_agent() -> None:
    """Stale-session failure is attributed to the agent, not environmental."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: 8e9806b7-8bcd"
    )
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.attributed_agent == "claude"
    assert failure.attributed_phase == "development"


def test_non_stale_session_invocation_error_reset_session_false() -> None:
    """AgentInvocationError without the stale-session substring keeps reset_session=False."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError("Agent 'claude' failed with code 1: some other error")
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.reset_session is False


def test_inactivity_timeout_reset_session_false() -> None:
    """AgentInactivityTimeoutError is unaffected by stale-session logic."""
    classifier = FailureClassifier()
    exc = _AgentInactivityTimeoutError("agent idle for too long")
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.reset_session is False


def test_environmental_error_reset_session_false() -> None:
    """Environmental failures never set reset_session."""
    classifier = FailureClassifier()
    failure = classifier.classify(
        ConnectionRefusedError("connection refused"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.reset_session is False


def test_documented_claude_session_limit_message_classifies_as_agent_fault() -> None:
    """Official Claude Code session-limit text should classify as an agent-attributed fault."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError("You've hit your session limit · resets 3:45pm")

    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is False
    assert failure.attributed_agent == "claude"


def test_agent_invocation_parsed_output_session_limit_message_is_detected() -> None:
    """Classifier should inspect parsed output for documented Claude Code limit messages."""
    from ralph.agents.invoke import AgentInvocationError

    classifier = FailureClassifier()
    exc = AgentInvocationError(
        "claude",
        1,
        "",
        ["You've hit your weekly limit · resets Mon 12:00am"],
    )

    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_online_timeout_with_no_output_is_treated_as_suspicious_agent_fault() -> None:
    """When connectivity is known online, no-output timeout should fall over as agent fault."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError("Agent timed out with no output while connectivity remained online")

    failure = classifier.classify(
        exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_unknown_connectivity_timeout_with_no_output_stays_non_agent_specific() -> None:
    """Without online connectivity evidence, suspicious timeout text stays non-agent-specific."""
    classifier = FailureClassifier()
    failure = classifier.classify(
        "Agent timed out with no output",
        phase="development",
        agent="claude",
        connectivity_state="unknown",
    )

    assert failure.category == FailureCategory.AMBIGUOUS
    assert failure.counts_against_budget is False


def test_stale_session_counts_against_budget() -> None:
    """Stale-session failures consume agent retry budget."""
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'opencode' failed with code 1: No conversation found with session ID: abc123"
    )
    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.counts_against_budget is True
