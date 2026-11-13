from __future__ import annotations

from ralph.agents.invoke._broken_agent_exit_error import BrokenAgentExitError
from ralph.pipeline import retryable_failure
from ralph.pipeline.agent_retry_intent import AgentRetryIntent, agent_retry_intent_for_failure
from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.failure_classifier import FailureClassifier, _is_subscription_limit_message
from ralph.recovery.failure_details import provider_message_parts


def resolve_retry_intent(
    failure: AttemptOutcome | Exception,
    *,
    phase: str,
    agent: str | None,
    session_id: str | None,
    inactivity_error_type: type[Exception],
    exc: Exception | None = None,
) -> AgentRetryIntent | None:
    if isinstance(failure, AttemptOutcome):
        return _intent_from_outcome(
            failure,
            exc=exc,
            phase=phase,
            agent=agent,
            session_id=session_id,
        )
    return _intent_from_exception(
        failure,
        phase=phase,
        agent=agent,
        session_id=session_id,
        inactivity_error_type=inactivity_error_type,
    )


def _intent_from_outcome(
    outcome: AttemptOutcome,
    *,
    exc: Exception | None,
    phase: str,
    agent: str | None,
    session_id: str | None,
) -> AgentRetryIntent | None:
    failure_reason = type(exc).__name__ if exc is not None else outcome.reason
    if outcome.kind is AttemptOutcomeKind.AGENT_DEAD:
        return AgentRetryIntent(
            failure_reason=failure_reason,
            skip_same_agent_retries=True,
            failed_agent_name=agent,
            broken_agent_reason=exc.reason if isinstance(exc, BrokenAgentExitError) else None,
            broken_agent_returncode=exc.returncode
            if isinstance(exc, BrokenAgentExitError)
            else None,
            broken_agent_stderr=exc.stderr if isinstance(exc, BrokenAgentExitError) else "",
        )
    if outcome.kind is AttemptOutcomeKind.RUNTIME_ENDED:
        return AgentRetryIntent(
            failure_reason=failure_reason,
            skip_same_agent_retries=True,
            failed_agent_name=agent,
        )
    if not retryable_failure.retryable_from_outcome(outcome):
        return None
    reset_tool_registry = (
        FailureClassifier().classify(exc, phase=phase, agent=agent).reset_tool_registry
        if exc is not None
        else False
    )
    return agent_retry_intent_for_failure(
        failure_reason=failure_reason,
        session_id=session_id,
        reset_tool_registry=reset_tool_registry,
    )


def _intent_from_exception(
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
    } or _is_subscription_limit_message(provider_message_parts(exc)):
        return AgentRetryIntent(
            failure_reason=type(exc).__name__,
            skip_same_agent_retries=True,
            failed_agent_name=agent,
            broken_agent_reason=exc.reason if isinstance(exc, BrokenAgentExitError) else None,
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


__all__ = ("resolve_retry_intent",)
