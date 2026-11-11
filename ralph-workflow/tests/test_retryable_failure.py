from __future__ import annotations

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason


def test_plain_empty_model_response_without_tool_activity_is_retryable() -> None:
    exc = AgentInvocationError(
        "nanocoder",
        1,
        "[plain] MCP server connected: ralph",
        parsed_output=[
            "nanocoder raw: Let me analyze this diff ...",
            "Model returned an empty response with no tool calls",
        ],
    )

    reason = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError)

    assert reason is not None


def test_post_tool_result_empty_response_keeps_specific_reason() -> None:
    exc = AgentInvocationError(
        "claude",
        1,
        "agent exited",
        parsed_output=[
            '{"type":"tool_use","name":"read_file"}',
            "empty response",
        ],
    )

    reason = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError)

    assert reason == "a post-tool-result continuation failure"


def test_bare_fetch_failed_is_a_transient_connectivity_failure() -> None:
    exc = AgentInvocationError(
        "pi",
        1,
        "TypeError: fetch failed",
        parsed_output=["pi/minimax/MiniMax-M3 fetch failed"],
    )

    reason = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError)

    assert reason == "a transient connectivity failure"


def test_mcp_tool_call_deadline_is_not_labelled_as_connectivity() -> None:
    exc = AgentInvocationError(
        "codex",
        1,
        "tool call error: tool call failed for `ralph/git_status`\n\n"
        "Caused by:\n    timed out awaiting tools/call after 75s",
    )

    reason = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError)

    assert reason == "a Ralph MCP tool call that timed out"


def test_unrelated_failure_is_not_retryable() -> None:
    exc = AgentInvocationError(
        "claude",
        1,
        "some unrelated fatal error",
        parsed_output=["nothing retryable here"],
    )

    reason = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError)

    assert reason is None
