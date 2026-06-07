from __future__ import annotations

from contextlib import suppress

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery
from ralph.agents.invoke._session import extract_session_id, extract_transport_session_id


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

    with suppress(AgentInvocationError):
        run_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=1,
            reset_tool_registry=lambda: None,
        )

    assert calls == [None]


def test_session_extractors_accept_completion_marker_session_id() -> None:
    line = "Task declared complete: session_id=abc123, summary=done, timestamp=1"

    assert extract_transport_session_id((line,)) == "abc123"
    assert extract_session_id((line,)) == "abc123"


def test_transport_session_extractor_ignores_generic_plain_text_session_assignment() -> None:
    line = "assistant said session_id=fake-visible"

    assert extract_transport_session_id((line,)) is None
