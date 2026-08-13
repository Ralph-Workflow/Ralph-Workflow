"""Regression coverage for proactive missing-credential failures (S-2)."""

from __future__ import annotations

from ralph.agents.invoke import MissingCredentialsError
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.pipeline.agent_retry_intent import AgentRetryIntent


def test_missing_credentials_error_routes_to_fast_fallover() -> None:
    """S-2: missing provider credentials skip same-agent retries."""
    error = MissingCredentialsError("opencode", "OPENAI_API_KEY not set")

    intent = resolve_retry_intent(
        error,
        phase="build",
        agent="opencode",
        session_id=None,
        inactivity_error_type=type(error),
    )

    assert error.skip_same_agent_retries is True
    assert error.agent_name == "opencode"
    assert error.env_var == "OPENAI_API_KEY"
    assert isinstance(intent, AgentRetryIntent)
    assert intent.skip_same_agent_retries is True
    assert intent.failed_agent_name == "opencode"
    assert intent.failure_reason == "MissingCredentialsError"
