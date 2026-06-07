from __future__ import annotations

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery


def test_direct_mcp_recovery_ignores_nested_tool_payload_session_ids() -> None:
    calls: list[str | None] = []

    failure = AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=[
            '{"type":"tool_result","content":{"session_id":"tool-payload"}}',
        ],
    )

    def _run_attempt(session_id: str | None) -> str:
        calls.append(session_id)
        raise failure

    try:
        run_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=1,
            reset_tool_registry=lambda: None,
        )
    except AgentInvocationError:
        pass

    assert calls == [None]
