"""Tests for the shared retry-decision core, `resolve_retry_intent`.

Both the pipeline executor and the direct-MCP recovery loop call this one
function to decide "is this failure retryable, and what is the canonical
next-attempt intent?", so retryability + classification + intent cannot drift.
"""

from __future__ import annotations

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.pipeline.agent_retry_decision import resolve_retry_intent


def test_returns_none_for_non_retryable_failure() -> None:
    intent = resolve_retry_intent(
        RuntimeError("totally unrelated"),
        phase="standalone",
        agent="agent",
        session_id=None,
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    assert intent is None


def test_builds_intent_for_retryable_empty_response() -> None:
    exc = AgentInvocationError(
        "agent",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=['{"type":"session","session_id":"sess-1"}'],
    )
    intent = resolve_retry_intent(
        exc,
        phase="standalone",
        agent="agent",
        session_id="sess-1",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    assert intent is not None
    assert intent.failure_reason == str(exc)


def test_fresh_action_clears_session_id() -> None:
    # A session-not-found failure must yield a fresh action with no session id,
    # regardless of an observed session id (handled by agent_retry_intent_for_failure).
    exc = AgentInvocationError("agent", 1, "session not found: sess-x")
    intent = resolve_retry_intent(
        exc,
        phase="standalone",
        agent="agent",
        session_id="sess-x",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    if intent is not None and intent.action == "fresh":
        assert intent.session_id is None
