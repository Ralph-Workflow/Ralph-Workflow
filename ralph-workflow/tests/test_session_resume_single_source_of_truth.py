"""Regression tests for the session-resume single source of truth helper.

These tests pin:

- ``resolve_session_resume_flag`` is the ONLY place that knows Claude
  Code's ``--resume`` vs ``--session-id`` flag semantics.
- A simulated live 'retry after timeout' feeds
  a canonical retry intent with ``session_id='sid-1'`` and
  the helper returns ``['--resume', 'sid-1']`` (NOT
  ``['--session-id', 'sid-1']``).
- The recovery action mapping routes known failure reasons to the
  expected recovery action.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke._session_resume import (
    recovery_action_for_failure_reason,
    resolve_session_resume_flag,
)
from ralph.config.enums import AgentTransport


def test_resume_after_inactivity_timeout_uses_resume_flag() -> None:
    args, new_sid = resolve_session_resume_flag(
        AgentTransport.CLAUDE_INTERACTIVE,
        has_prior_session=True,
        prior_session_id="sid-1",
        recovery_action="resume",
    )
    assert args == ["--resume", "sid-1"]
    assert new_sid == "sid-1"


def test_resume_after_inactivity_timeout_claude_transport_uses_resume_flag() -> None:
    args, new_sid = resolve_session_resume_flag(
        AgentTransport.CLAUDE,
        has_prior_session=True,
        prior_session_id="sid-claude",
        recovery_action="resume",
    )
    assert args == ["--resume", "sid-claude"]
    assert new_sid == "sid-claude"


def test_resume_after_inactivity_timeout_opencode_uses_session_flag() -> None:
    args, new_sid = resolve_session_resume_flag(
        AgentTransport.OPENCODE,
        has_prior_session=True,
        prior_session_id="sid-opencode",
        recovery_action="resume",
    )
    assert args == ["--session", "sid-opencode"]
    assert new_sid == "sid-opencode"


def test_resume_after_inactivity_timeout_nanocoder_has_no_special_resume_flag() -> None:
    args, new_sid = resolve_session_resume_flag(
        AgentTransport.NANOCODER,
        has_prior_session=True,
        prior_session_id="sid-nanocoder",
        recovery_action="resume",
    )
    assert args == ["--session-id", "sid-nanocoder"]
    assert new_sid == "sid-nanocoder"


def test_fresh_recovery_returns_no_args_and_none_session_id() -> None:
    args, new_sid = resolve_session_resume_flag(
        AgentTransport.CLAUDE_INTERACTIVE,
        has_prior_session=False,
        prior_session_id=None,
        recovery_action="fresh",
    )
    assert args == []
    assert new_sid is None


def test_new_session_with_id_uses_session_id_flag() -> None:
    args, new_sid = resolve_session_resume_flag(
        AgentTransport.CLAUDE_INTERACTIVE,
        has_prior_session=True,
        prior_session_id="sid-stale",
        recovery_action="new_session_with_id",
    )
    assert args == ["--session-id", "sid-stale"]
    assert new_sid == "sid-stale"


def test_resolve_rejects_unknown_recovery_action() -> None:
    with pytest.raises(ValueError):
        resolve_session_resume_flag(
            AgentTransport.CLAUDE_INTERACTIVE,
            has_prior_session=True,
            prior_session_id="sid-1",
            recovery_action="bogus",
        )


def test_resolve_rejects_prior_session_id_mismatch() -> None:
    with pytest.raises(ValueError):
        resolve_session_resume_flag(
            AgentTransport.CLAUDE_INTERACTIVE,
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


def test_resolve_session_resume_flag_uses_only_resume_for_claude_interactive() -> None:
    """The pre-fix code had a divergent `--session-id` elif branch in
    _build_claude_interactive_command. After the fix, the helper is the
    only decision point. Verify Claude interactive ALWAYS uses --resume
    for the recovery_action='resume' case (never --session-id)."""
    for action in ("resume",):
        args, _ = resolve_session_resume_flag(
            AgentTransport.CLAUDE_INTERACTIVE,
            has_prior_session=True,
            prior_session_id="sid-x",
            recovery_action=action,
        )
        assert args[0] == "--resume", f"expected --resume, got {args[0]}"


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
