"""Regression tests for retryable agent-failure classification.

The cross-caller drift: a model that connects, reasons, and returns an empty
turn with NO tool call (e.g. nanocoder/MiniMax-M3 over-reasoning a commit
message and never calling ``ralph_submit_artifact``) is retried by the pipeline
path (the classifier treats it as recoverable), but the direct-MCP recovery path
used by `ralph --generate-commit` did NOT retry it because the empty-response
signal was gated on prior tool activity. These tests pin that BOTH a
post-tool-result empty response AND a plain empty response (no prior tool
activity) are retryable, so the two callers agree.
"""

from __future__ import annotations

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason


def test_plain_empty_model_response_without_tool_activity_is_retryable() -> None:
    """A model that returns empty with no prior tool call must be retryable."""
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
    """An empty response AFTER tool activity keeps its specific reason."""
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


def test_unrelated_failure_is_not_retryable() -> None:
    """A failure with none of the retryable signals returns None."""
    exc = AgentInvocationError(
        "claude",
        1,
        "some unrelated fatal error",
        parsed_output=["nothing retryable here"],
    )

    reason = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError)

    assert reason is None
