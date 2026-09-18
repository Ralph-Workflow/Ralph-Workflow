"""Cursor authentication failures are user configuration errors."""

from __future__ import annotations

import pytest

from ralph.agents.invoke import BrokenAgentExitError
from ralph.recovery.failure_classifier import FailureCategory, FailureClassifier


@pytest.mark.parametrize(
    "message",
    (
        "Cursor authentication required",
        "Please run 'agent login' before continuing",
        "CURSOR_API_KEY is missing",
    ),
)
def test_cursor_auth_message_only_is_user_config(message: str) -> None:
    failure = FailureClassifier().classify(
        message,
        phase="development",
        agent="cursor",
    )

    assert failure.category == FailureCategory.USER_CONFIG
    assert failure.counts_against_budget is False
    assert failure.reset_session is False


@pytest.mark.parametrize(
    "stderr",
    (
        "Cursor authentication required",
        "Please run 'agent login' before continuing",
        "CURSOR_API_KEY is missing",
    ),
)
def test_cursor_auth_broken_agent_exit_is_user_config(stderr: str) -> None:
    failure = FailureClassifier().classify(
        BrokenAgentExitError("cursor", reason="no_output", stderr=stderr),
        phase="development",
        agent="cursor",
    )

    assert failure.category == FailureCategory.USER_CONFIG
    assert failure.counts_against_budget is False
    assert failure.reset_session is False


def test_generic_broken_agent_exit_remains_agent_failure() -> None:
    failure = FailureClassifier().classify(
        BrokenAgentExitError("cursor", reason="no_output", stderr="process exited"),
        phase="development",
        agent="cursor",
    )

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is True
