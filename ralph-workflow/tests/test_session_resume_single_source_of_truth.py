"""Regression tests for the session-resume single source of truth helpers.

These tests pin:

- ``resolve_resume_session_id`` is the ONLY place that decides WHETHER the next
  attempt continues the prior session and WHICH session id it threads. It is
  transport-independent; the per-transport resume flag SYNTAX is owned by
  ``config.session_flag`` and exercised by the command-builder tests in
  ``test_agents_invoke_4.py``.
- A simulated live 'retry after timeout' resolves to the prior session id so the
  builder can resume it (rather than dropping it and starting fresh).
- The recovery action mapping routes known failure reasons to the expected
  recovery action.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke._session_resume import (
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)


def test_resume_after_inactivity_timeout_threads_prior_session_id() -> None:
    new_sid = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="sid-1",
        recovery_action="resume",
    )
    assert new_sid == "sid-1"


def test_resume_is_transport_independent() -> None:
    """The decision does not depend on the transport; syntax lives in config."""
    for prior in ("sid-claude", "sid-opencode", "sid-nanocoder"):
        new_sid = resolve_resume_session_id(
            has_prior_session=True,
            prior_session_id=prior,
            recovery_action="resume",
        )
        assert new_sid == prior


def test_fresh_recovery_returns_none_session_id() -> None:
    new_sid = resolve_resume_session_id(
        has_prior_session=False,
        prior_session_id=None,
        recovery_action="fresh",
    )
    assert new_sid is None


def test_new_session_with_id_threads_prior_session_id() -> None:
    new_sid = resolve_resume_session_id(
        has_prior_session=True,
        prior_session_id="sid-stale",
        recovery_action="new_session_with_id",
    )
    assert new_sid == "sid-stale"


def test_resolve_rejects_unknown_recovery_action() -> None:
    with pytest.raises(ValueError):
        resolve_resume_session_id(
            has_prior_session=True,
            prior_session_id="sid-1",
            recovery_action="bogus",
        )


def test_resolve_rejects_prior_session_id_mismatch() -> None:
    with pytest.raises(ValueError):
        resolve_resume_session_id(
            has_prior_session=True,
            prior_session_id="",
            recovery_action="resume",
        )


@pytest.mark.parametrize(
    ("failure_reason", "has_prior_session", "expected_action"),
    [
        ("AgentInactivityTimeoutError", True, "resume"),
        ("OpenCodeResumableExitError", True, "resume"),
        ("No conversation found with session ID: abc", True, "fresh"),
        ("RandomOtherError", True, "fresh"),
        ("AgentInactivityTimeoutError", False, "fresh"),
        ("NoConversationFoundError", False, "fresh"),
    ],
)
def test_recovery_action_for_failure_reason(
    failure_reason: str, has_prior_session: bool, expected_action: str
) -> None:
    action = recovery_action_for_failure_reason(
        failure_reason, has_prior_session=has_prior_session
    )
    assert action == expected_action


def test_tool_registry_recovery_empty_response_uses_resume_action() -> None:
    action = recovery_action_for_failure_reason(
        "Model returned an empty response with no tool calls",
        has_prior_session=True,
        reset_tool_registry=True,
    )

    assert action == "resume"


def test_tool_registry_recovery_agent_invocation_error_still_uses_resume_action() -> None:
    action = recovery_action_for_failure_reason(
        "AgentInvocationError",
        has_prior_session=True,
        reset_tool_registry=True,
    )

    assert action == "resume"
