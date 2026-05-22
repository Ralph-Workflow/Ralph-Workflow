"""Tests: OpenCode stale-session substrings classify as AGENT + reset_session=True."""

from __future__ import annotations

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.recovery.classifier import FailureCategory, FailureClassifier


class _AgentInvocationError(Exception):
    """Simulates AgentInvocationError via class name matching."""


_AgentInvocationError.__name__ = "AgentInvocationError"


_CLASSIFIER = FailureClassifier()


@pytest.mark.parametrize(
    "message",
    [
        "Session not found: abc123",
        "Unknown session: deadbeef",
        "session does not exist",
        "Agent 'opencode' failed with code 1: Session not found: xyz",
        "Agent 'opencode' failed with code 1: Unknown session: 8e9806b7",
    ],
)
def test_opencode_stale_session_message_sets_reset_session_true(message: str) -> None:
    """OpenCode stale-session substrings trigger reset_session=True and AGENT category."""
    exc = _AgentInvocationError(message)
    failure = _CLASSIFIER.classify(exc, phase="development", agent="opencode")

    assert failure.reset_session is True, (
        f"Expected reset_session=True for message {message!r}, got False"
    )
    assert failure.category == FailureCategory.AGENT, (
        f"Expected AGENT category for message {message!r}, got {failure.category}"
    )
    assert failure.counts_against_budget is True


def test_opencode_stale_session_attributed_to_agent() -> None:
    """OpenCode stale-session failure is attributed to the agent."""
    exc = _AgentInvocationError("Session not found: abc123")
    failure = _CLASSIFIER.classify(exc, phase="development", agent="opencode")

    assert failure.attributed_agent == "opencode"
    assert failure.attributed_phase == "development"


def test_unrelated_opencode_error_does_not_trigger_reset_session() -> None:
    """Non-stale-session OpenCode errors keep reset_session=False."""
    exc = _AgentInvocationError("Agent 'opencode' failed with code 1: some other error")
    failure = _CLASSIFIER.classify(exc, phase="development", agent="opencode")

    assert failure.reset_session is False


def test_stale_session_detection_is_case_insensitive() -> None:
    """Lowercase stale-session payloads still trigger reset_session=True."""
    exc = _AgentInvocationError("agent 'opencode' failed: session not found: lower-case")
    failure = _CLASSIFIER.classify(exc, phase="development", agent="opencode")

    assert failure.reset_session is True
    assert failure.category == FailureCategory.AGENT


def test_stale_session_detection_reads_agent_invocation_parsed_output() -> None:
    """Classifier inspects parsed_output, not just the top-level exception string."""
    exc = AgentInvocationError(
        "opencode",
        1,
        "Unexpected server error",
        [
            '{"type":"error","error":{"message":"session not found: recovered-from-output"}}'
        ],
    )

    failure = _CLASSIFIER.classify(exc, phase="development", agent="opencode")

    assert failure.reset_session is True
    assert failure.category == FailureCategory.AGENT
