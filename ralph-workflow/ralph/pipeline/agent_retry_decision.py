from __future__ import annotations

from ralph.agents.invoke._broken_agent_exit_error import BrokenAgentExitError
from ralph.pipeline import retryable_failure
from ralph.pipeline.agent_retry_intent import AgentRetryIntent, agent_retry_intent_for_failure
from ralph.recovery.failure_classifier import FailureClassifier, _is_subscription_limit_message
from ralph.recovery.failure_details import failure_message_parts


def resolve_retry_intent(
    exc: Exception,
    *,
    phase: str,
    agent: str | None,
    session_id: str | None,
    inactivity_error_type: type[Exception],
) -> AgentRetryIntent | None:
    if type(exc).__name__ in {
        "BrokenAgentExitError",
        "MissingCredentialsError",
        "PiContextExhaustedExitError",
        "PiProviderFailureExitError",
        "QuotaExhaustedError",
    } or _is_subscription_limit_message(failure_message_parts(exc)):
        broken_agent_reason = exc.reason if isinstance(exc, BrokenAgentExitError) else None
        return AgentRetryIntent(
            failure_reason=type(exc).__name__,
            skip_same_agent_retries=True,
            failed_agent_name=agent,
            broken_agent_reason=broken_agent_reason,
            broken_agent_returncode=exc.returncode
            if isinstance(exc, BrokenAgentExitError)
            else None,
            broken_agent_stderr=exc.stderr if isinstance(exc, BrokenAgentExitError) else "",
        )
    if retryable_failure.retryable_agent_failure_reason(exc, inactivity_error_type) is None:
        return None
    classified = FailureClassifier().classify(exc, phase=phase, agent=agent)
    return agent_retry_intent_for_failure(
        failure_reason=type(exc).__name__,
        session_id=session_id,
        reset_tool_registry=classified.reset_tool_registry,
    )


__all__ = ["resolve_retry_intent"]
