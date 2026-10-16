"""S3 regression: terminal retry error must preserve the original exception type.

For non-``AgentInvocationError`` failures, ``_terminal_retry_error`` must
return the original exception with the diagnostic attached as a note
(via ``add_note``), NOT rebuild it as a bare ``RuntimeError``. This keeps
callers' ``pytest.raises`` and upstream classifiers able to key on the
real failure type.

The third test pins that the S2-untouched ``AgentInvocationError`` path
still emits the "retry cooldown exhausted" diagnostic into
``parsed_output`` (the existing contract used by
``tests/test_commit_retry_cooldown.py``).
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import (
    _terminal_retry_error,
    run_with_direct_mcp_recovery,
)


class _SomeCustomError(Exception):
    """Non-AgentInvocationError exception used to verify type preservation."""


def test_terminal_retry_error_preserves_non_agent_invocation_type() -> None:
    original = _SomeCustomError("x")

    result = _terminal_retry_error(original, consecutive_failures=5)

    assert type(result) is _SomeCustomError
    assert result is original
    notes = getattr(result, "__notes__", ())
    joined = " ".join((*result.args, *notes))
    assert "retry cooldown exhausted" in joined


def test_invoke_agent_with_recovery_returns_typed_failure_for_non_agent_invocation_error() -> None:
    attempts = 0
    registry_calls = 0

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("non-agent failure")

    def _reset_tool_registry() -> None:
        nonlocal registry_calls
        registry_calls += 1

    with pytest.raises(ValueError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=_reset_tool_registry,
        )

    assert type(exc_info.value) is ValueError
    assert registry_calls == 0
    assert attempts == 1


def test_invoke_agent_with_recovery_keeps_commit_retry_cooldown_text() -> None:
    """Pins that the S2-untouched ``AgentInvocationError`` path still emits the
    diagnostic into ``parsed_output`` (the contract used by
    ``tests/test_commit_retry_cooldown.py``).
    """

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        raise AgentInvocationError(
            "commit-agent",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=["invalid Files section"],
        )

    with pytest.raises(AgentInvocationError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=lambda: None,
            sleep=lambda _seconds: None,
        )

    assert "retry cooldown exhausted" in "\n".join(exc_info.value.parsed_output)


def test_terminal_retry_error_no_agent_switch_wording_for_typed_failure() -> None:
    """S3: non-AgentInvocationError terminal error has no agent-switch wording."""

    class _TypedError(Exception):
        pass

    original = _TypedError("typed failure")
    result = _terminal_retry_error(original, consecutive_failures=3)

    assert result is original
    # Must have the retry cooldown diagnostic
    notes = getattr(result, "__notes__", ())
    joined = " ".join((*result.args, *notes))
    assert "retry cooldown exhausted" in joined
    # Must NOT have agent-switch wording
    assert "try the next eligible agent" not in joined
