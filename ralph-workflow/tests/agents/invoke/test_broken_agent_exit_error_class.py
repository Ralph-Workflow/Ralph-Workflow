"""Tests for the typed broken-agent failure signal."""

from __future__ import annotations

import pytest

from ralph.agents.invoke import AgentInvocationError, BrokenAgentExitError


def test_broken_agent_exit_error_skips_same_agent_retries_and_exposes_reason() -> None:
    error = BrokenAgentExitError("claude", reason="prompt_echo")

    assert isinstance(error, AgentInvocationError)
    assert error.skip_same_agent_retries is True
    assert error.reason == "prompt_echo"
    assert "credentials" in str(error).casefold()
    assert "prompt" in str(error).casefold()


def test_broken_agent_exit_error_retains_returncode_and_stderr() -> None:
    error = BrokenAgentExitError(
        "pi/omnirouter/cx/gpt-5.6-terra-medium",
        reason="no_output",
        returncode=17,
        stderr="provider authentication failed",
    )

    assert error.returncode == 17
    assert error.stderr == "provider authentication failed"
    assert "code 17" in str(error)
    assert "provider authentication failed" in str(error)


def test_broken_agent_exit_error_rejects_unknown_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        BrokenAgentExitError("claude", reason="unexpected")
