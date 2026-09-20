from __future__ import annotations

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery


def test_repeated_same_signature_backs_off_and_reports_terminal_diagnostic() -> None:
    calls = 0
    delays: list[float] = []

    def run_attempt(
        _session_id: str | None, _capture_session_id: object
    ) -> str:
        nonlocal calls
        calls += 1
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
            sleep=delays.append,
        )

    assert calls == 3
    assert delays == [0.001]
    assert "retry cooldown exhausted" in "\n".join(exc_info.value.parsed_output)
