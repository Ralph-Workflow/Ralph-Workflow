"""S2 regression: McpServerError must propagate from recovery loops without retry.

The recovery loops must treat ``McpServerError`` as an infrastructure
failure (like ``SupervisionInfrastructureError``) and re-raise it
immediately, so a broken MCP server never masquerades as an agent
failure and never triggers ``reset_tool_registry`` as a recovery step.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

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
