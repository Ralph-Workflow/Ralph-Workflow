from __future__ import annotations

import pytest

from ralph.pipeline.conflict_resolution.attempt_fault import (
    AttemptFault,
    classify_attempt_fault,
)

_MISLEADING_SUBSTRING_PROSE = [
    "unhealthy environment",
    "agent is healthy enough",
    "I am healthy and ready",
    "healthy chicken dinner",
    "feeling healthy today",
    "the agent was healthy before it died",
]


@pytest.mark.parametrize("text", _MISLEADING_SUBSTRING_PROSE)
def test_substring_healthy_does_not_authorize_agent_healthy(text: str) -> None:
    classified = classify_attempt_fault(text)
    assert classified is not AttemptFault.AGENT_HEALTHY, (
        f"substring match leaked through: {text!r} -> {classified!r}"
    )
    assert classified is AttemptFault.AGENT_EXITED


def test_substring_healthy_does_not_authorize_count() -> None:
    assert len(_MISLEADING_SUBSTRING_PROSE) == 6


@pytest.mark.parametrize(
    "text",
    [
        "",
        "healthy",
    ],
)
def test_exact_healthy_authorizes_agent_healthy(text: str) -> None:
    assert classify_attempt_fault(text) is AttemptFault.AGENT_HEALTHY


def test_none_authorizes_agent_healthy() -> None:
    assert classify_attempt_fault(None) is AttemptFault.AGENT_HEALTHY


def test_healthy_uppercase_is_exact_match() -> None:
    assert classify_attempt_fault("HEALTHY") is AttemptFault.AGENT_HEALTHY
    assert classify_attempt_fault("Healthy") is AttemptFault.AGENT_HEALTHY


def test_healthy_with_whitespace_padded_is_stripped_then_exact_match() -> None:
    assert classify_attempt_fault("  healthy  ") is AttemptFault.AGENT_HEALTHY
