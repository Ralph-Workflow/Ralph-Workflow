"""Tests for the shared retry-decision core, `resolve_retry_intent`.

Both the pipeline executor and the direct-MCP recovery loop call this one
function to decide "is this failure retryable, and what is the canonical
next-attempt intent?", so retryability + classification + intent cannot drift.
"""

from __future__ import annotations

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._open_code_resumable_exit_error import OpenCodeResumableExitError
from ralph.agents.invoke._pi_context_exhausted_exit_error import PiContextExhaustedExitError
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
    assert intent.failure_reason == type(exc).__name__
    assert intent.failure_reason == "AgentInvocationError"


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


def test_resume_action_for_agent_inactivity_timeout_with_session_resume_safe() -> None:
    """An AgentInactivityTimeoutError with a resumable prior session MUST
    resolve to action='resume' with the original session id preserved.

    Regression: prior to the fix, `resolve_retry_intent` passed
    `failure_reason=str(exc)` (the rendered exception message) into
    `agent_retry_intent_for_failure`, so `recovery_action_for_failure_reason`
    could not match the canonical 'AgentInactivityTimeoutError' class-name
    token and silently downgraded resumable inactivity kills to fresh sessions.
    """
    exc = AgentInactivityTimeoutError(
        agent_name="agent",
        timeout_seconds=30,
        opts=InactivityTimeoutOpts(
            session_resume_safe=True,
            resumable_session_id="sess-123",
        ),
    )
    intent = resolve_retry_intent(
        exc,
        phase="standalone",
        agent="agent",
        session_id="sess-123",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    assert intent is not None
    assert intent.action == "resume", (
        f"AgentInactivityTimeoutError with session_resume_safe=True and a prior"
        f" session must resolve to action='resume'; got action={intent.action!r}"
    )
    assert intent.session_id == "sess-123", (
        f"Resume intent must preserve the original session id; got"
        f" session_id={intent.session_id!r}"
    )
    assert intent.failure_reason == "AgentInactivityTimeoutError"


def test_resume_action_for_open_code_resumable_exit_error() -> None:
    """An OpenCodeResumableExitError carrying a session id MUST resolve to
    action='resume' with the original session id preserved.

    Regression: prior to the fix, `resolve_retry_intent` passed
    `failure_reason=str(exc)` so `recovery_action_for_failure_reason` could
    not match the canonical 'OpenCodeResumableExitError' class-name token
    and silently downgraded a resumable rc=0 exit to a fresh session,
    dropping the captured session id.
    """
    exc = OpenCodeResumableExitError("agent", session_id="sess-456")
    intent = resolve_retry_intent(
        exc,
        phase="standalone",
        agent="agent",
        session_id="sess-456",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    assert intent is not None
    assert intent.action == "resume", (
        f"OpenCodeResumableExitError carrying a session id must resolve to"
        f" action='resume'; got action={intent.action!r}"
    )
    assert intent.session_id == "sess-456", (
        f"Resume intent must preserve the captured session id; got"
        f" session_id={intent.session_id!r}"
    )
    assert intent.failure_reason == "OpenCodeResumableExitError"


def test_pi_context_exhaustion_skips_same_agent_retries() -> None:
    exc = PiContextExhaustedExitError("pi/zai/glm-5.2")
    intent = resolve_retry_intent(
        exc,
        phase="development",
        agent="pi/zai/glm-5.2",
        session_id="pi-session",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    assert intent is not None
    assert intent.action is None
    assert intent.session_id is None
    assert intent.skip_same_agent_retries is True
    assert intent.failure_reason == "PiContextExhaustedExitError"
