from __future__ import annotations

from ralph.agents.invoke import AgentInvocationError
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from tests.recovery._classifier_session_helper__agentinactivitytimeouterror import (
    _AgentInactivityTimeoutError,
)


class _AgentInvocationError(Exception):
    pass


_AgentInvocationError.__name__ = "AgentInvocationError"
_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_stale_session_message_sets_reset_session_true() -> None:
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: deadbeef"
    )
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is True


def test_stale_session_attributed_to_agent() -> None:
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'claude' failed with code 1: No conversation found with session ID: 8e9806b7-8bcd"
    )
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.attributed_agent == "claude"
    assert failure.attributed_phase == "development"


def test_non_stale_session_invocation_error_reset_session_false() -> None:
    classifier = FailureClassifier()
    exc = _AgentInvocationError("Agent 'claude' failed with code 1: some other error")
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.reset_session is False


def test_inactivity_timeout_reset_session_false() -> None:
    classifier = FailureClassifier()
    exc = _AgentInactivityTimeoutError("agent idle for too long")
    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.reset_session is False


def test_environmental_error_reset_session_false() -> None:
    classifier = FailureClassifier()
    failure = classifier.classify(
        ConnectionRefusedError("connection refused"),
        phase="development",
        agent="claude",
    )

    assert failure.category == FailureCategory.ENVIRONMENTAL
    assert failure.reset_session is False


def test_documented_claude_session_limit_message_classifies_as_agent_fault() -> None:
    classifier = FailureClassifier()
    exc = _AgentInvocationError("You've hit your session limit · resets 3:45pm")

    failure = classifier.classify(exc, phase="development", agent="claude")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is False
    assert failure.attributed_agent == "claude"


def test_agent_invocation_parsed_output_session_limit_message_is_detected() -> None:
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


def test_subscription_limit_sets_is_unavailable_when_online() -> None:
    classifier = FailureClassifier()
    exc = AgentInvocationError(
        "claude",
        1,
        "",
        ["You've hit your weekly limit \u00b7 resets Mon 12:00am"],
    )

    failure = classifier.classify(
        exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.is_unavailable is True
    assert failure.reset_tool_registry is False


def test_online_timeout_with_no_output_is_treated_as_suspicious_agent_fault() -> None:
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
    classifier = FailureClassifier()
    exc = _AgentInvocationError(
        "Agent 'opencode' failed with code 1: No conversation found with session ID: abc123"
    )
    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.counts_against_budget is True
