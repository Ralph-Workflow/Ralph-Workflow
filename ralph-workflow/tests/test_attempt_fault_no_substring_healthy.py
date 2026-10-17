"""Tests for attempt-fault prose boundary: 'healthy' must be an exact match.

Trust boundary: prose evidence is only consulted for an EXACT MATCH against the
closed vocabulary. Arbitrary agent output containing "healthy" as a substring
(e.g. "unhealthy environment", "agent is healthy enough") does NOT authorize
``AttemptFault.AGENT_HEALTHY``; the classifier must fall through to the
substring scans below (or to ``AGENT_EXITED`` for plain prose that matches
none of the typed-vocabulary identifiers).
"""

from __future__ import annotations

import pytest

from ralph.pipeline.conflict_resolution.attempt_fault import (
    AttemptFault,
    classify_attempt_fault,
)

# Misleading prose that contains "healthy" as a substring and previously
# authorized AGENT_HEALTHY under the buggy substring scan. Under exact match
# none of these should authorize AGENT_HEALTHY; they fall through to
# AGENT_EXITED (plain prose, no typed-vocabulary match).
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
    """Prose containing 'healthy' as a substring must NOT classify as AGENT_HEALTHY."""
    classified = classify_attempt_fault(text)
    assert classified is not AttemptFault.AGENT_HEALTHY, (
        f"substring match leaked through: {text!r} -> {classified!r}"
    )
    # Plain prose that is not a typed-vocabulary identifier falls through to
    # AGENT_EXITED (the function's documented default for unrecognised text).
    assert classified is AttemptFault.AGENT_EXITED


def test_substring_healthy_does_not_authorize_count() -> None:
    """Sanity check: the misleading-prose parametrized list has 6 entries."""
    assert len(_MISLEADING_SUBSTRING_PROSE) == 6


@pytest.mark.parametrize(
    "text",
    [
        "",
        "healthy",
    ],
)
def test_exact_healthy_authorizes_agent_healthy(text: str) -> None:
    """Empty string and the exact token 'healthy' (case-insensitive) authorize AGENT_HEALTHY."""
    assert classify_attempt_fault(text) is AttemptFault.AGENT_HEALTHY


def test_none_authorizes_agent_healthy() -> None:
    """None is the healthy-absence signal and must authorize AGENT_HEALTHY."""
    assert classify_attempt_fault(None) is AttemptFault.AGENT_HEALTHY


def test_healthy_uppercase_is_exact_match() -> None:
    """Case-insensitive exact match: 'HEALTHY' (and mixed case) authorize AGENT_HEALTHY."""
    assert classify_attempt_fault("HEALTHY") is AttemptFault.AGENT_HEALTHY
    assert classify_attempt_fault("Healthy") is AttemptFault.AGENT_HEALTHY


def test_healthy_with_whitespace_padded_is_stripped_then_exact_match() -> None:
    """Leading/trailing whitespace is stripped; the bare token 'healthy' authorizes AGENT_HEALTHY."""
    assert classify_attempt_fault("  healthy  ") is AttemptFault.AGENT_HEALTHY
