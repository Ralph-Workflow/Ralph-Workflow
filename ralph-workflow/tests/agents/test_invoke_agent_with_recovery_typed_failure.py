"""Behavioral regressions for direct-MCP retry-budget exhaustion.

The retry loop preserves the original typed failure while attaching a factual
attempt-budget diagnostic. It does not classify agent health or advise the
caller to select another agent.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke import AgentInvocationError, SupervisionInfrastructureError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery


class _SomeCustomError(Exception):
    """Retryable non-agent-invocation failure used to verify type preservation."""

    agent_name = "custom-agent"
    stderr = "connection refused"


def test_retry_budget_exhaustion_preserves_non_agent_invocation_type() -> None:
    failure = _SomeCustomError("x")
    attempts = 0

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(_SomeCustomError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=lambda: None,
        )

    assert attempts == 3
    assert exc_info.value is failure
    notes = getattr(exc_info.value, "__notes__", ())
    diagnostic = " ".join((*exc_info.value.args, *notes))
    assert "retry budget exhausted after 3 attempts (2 retries)" in diagnostic
    assert "cause: _SomeCustomError" in diagnostic
    assert "try the next eligible agent" not in diagnostic


def test_retry_budget_exhaustion_preserves_typed_agent_invocation_subclass() -> None:
    class _TypedAgentFailureError(AgentInvocationError):
        pass

    failure = _TypedAgentFailureError(
        "commit-agent",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=["invalid Files section"],
    )
    attempts = 0

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(_TypedAgentFailureError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=lambda: None,
        )

    assert attempts == 3
    assert type(exc_info.value) is _TypedAgentFailureError
    assert exc_info.value is failure
    diagnostic = "\n".join(exc_info.value.parsed_output)
    assert "retry budget exhausted after 3 attempts (2 retries)" in diagnostic
    assert "cause: _TypedAgentFailureError" in diagnostic
    assert "identical failure signatures" not in diagnostic
    assert "try the next eligible agent" not in diagnostic


def test_non_retryable_failure_surfaces_without_retry() -> None:
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


def test_supervision_infrastructure_failure_surfaces_without_retry_or_agent_verdict() -> None:
    failure = SupervisionInfrastructureError("resolver", "relay dead")
    attempts = 0
    registry_calls = 0

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        nonlocal attempts
        attempts += 1
        raise failure

    def _reset_tool_registry() -> None:
        nonlocal registry_calls
        registry_calls += 1

    with pytest.raises(SupervisionInfrastructureError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=2,
            reset_tool_registry=_reset_tool_registry,
        )

    assert exc_info.value is failure
    assert registry_calls == 0
    assert attempts == 1
    assert "try the next eligible agent" not in "\n".join(exc_info.value.parsed_output)
