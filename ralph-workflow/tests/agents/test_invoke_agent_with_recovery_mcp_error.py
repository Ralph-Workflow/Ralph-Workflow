"""S2 regression: McpServerError must propagate from recovery loops without retry.

The recovery loops must treat ``McpServerError`` as an infrastructure
failure (like ``SupervisionInfrastructureError``) and re-raise it
immediately, so a broken MCP server never masquerades as an agent
failure and never triggers ``reset_tool_registry`` as a recovery step.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import (
    iter_with_direct_mcp_recovery,
    run_with_direct_mcp_recovery,
)
from ralph.mcp.server._mcp_server_error import McpServerError


def test_mcp_server_error_propagates_after_one_attempt_no_registry_call() -> None:
    attempts = 0
    registry_calls = 0

    def run_attempt(_session_id: str | None, _capture: object) -> str:
        nonlocal attempts
        attempts += 1
        raise McpServerError("mcp server failed", restart_count=3)

    def _reset_tool_registry() -> None:
        nonlocal registry_calls
        registry_calls += 1

    with pytest.raises(McpServerError):
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_tool_registry,
        )

    assert attempts == 1
    assert registry_calls == 0


def test_iter_mcp_server_error_propagates_after_one_attempt_no_registry_call() -> None:
    attempts = 0
    registry_calls = 0

    def _run_attempt(_session_id: str | None) -> Iterable[str]:
        nonlocal attempts
        attempts += 1
        yield "stream-line-1"
        yield "stream-line-2"
        raise McpServerError("mcp server failed", restart_count=3)

    def _reset_tool_registry() -> None:
        nonlocal registry_calls
        registry_calls += 1

    with pytest.raises(McpServerError):
        list(
            iter_with_direct_mcp_recovery(
                _run_attempt,
                max_retries=5,
                reset_tool_registry=_reset_tool_registry,
            )
        )

    assert attempts == 1
    assert registry_calls == 0


def _reset_raises_mcp_server_error(calls: list[int]) -> None:
    def _reset_tool_registry() -> None:
        calls.append(1)
        raise McpServerError(
            "budget exhausted",
            restart_count=4,
            last_stage="binding_tools",
            elapsed_s=12.5,
        )

    return _reset_tool_registry


def test_run_reset_callback_mcp_error_preserves_identity() -> None:
    attempts = 0
    reset_calls: list[int] = []

    def run_attempt(_session_id: str | None, capture: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            capture("sess-run")
            raise AgentInvocationError(
                "claude",
                1,
                "Model returned an empty response with no tool calls",
                parsed_output=['{"type":"tool_result","content":"ok"}'],
            )
        return "should-not-reach"

    with pytest.raises(McpServerError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_raises_mcp_server_error(reset_calls),
            sleep=lambda _seconds: None,
        )

    error = exc_info.value
    assert type(error) is McpServerError
    assert error.restart_count == 4
    assert error.last_stage == "binding_tools"
    assert error.elapsed_s == 12.5
    assert attempts == 1
    assert reset_calls == [1]


def test_iter_reset_callback_mcp_error_preserves_identity() -> None:
    attempts = 0
    reset_calls: list[int] = []

    def _run_attempt(_session_id: str | None) -> Iterable[str]:
        nonlocal attempts
        attempts += 1
        yield '{"type":"session","session_id":"sess-iter"}'
        raise AgentInvocationError(
            "claude",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=['{"type":"tool_result","content":"ok"}'],
        )

    def _consume() -> Iterable[str]:
        yield from iter_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_raises_mcp_server_error(reset_calls),
            sleep=lambda _seconds: None,
        )

    with pytest.raises(McpServerError) as exc_info:
        list(_consume())

    error = exc_info.value
    assert type(error) is McpServerError
    assert error.restart_count == 4
    assert error.last_stage == "binding_tools"
    assert error.elapsed_s == 12.5
    assert attempts == 1
    assert reset_calls == [1]


def test_run_reset_callback_generic_error_still_converts_to_invocation_error() -> None:
    """Non-McpServerError reset-callback failures keep the converted shape."""
    reset_calls: list[int] = []

    def run_attempt(_session_id: str | None, capture: object) -> str:
        capture("sess-run")
        raise AgentInvocationError(
            "claude",
            1,
            "Model returned an empty response with no tool calls",
            parsed_output=['{"type":"tool_result","content":"ok"}'],
        )

    def _reset_tool_registry() -> None:
        reset_calls.append(1)
        raise RuntimeError("bridge gone")

    with pytest.raises(AgentInvocationError) as exc_info:
        run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=5,
            reset_tool_registry=_reset_tool_registry,
            sleep=lambda _seconds: None,
        )

    error = exc_info.value
    assert type(error) is AgentInvocationError
    assert error.failure_origin == "mcp_operation"
    assert error.issuer == "direct_mcp_recovery.reset_tool_registry"
    assert reset_calls == [1]
