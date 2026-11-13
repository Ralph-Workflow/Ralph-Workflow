from __future__ import annotations

from ralph.agents.invoke import AgentInactivityTimeoutError, AgentInvocationError
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord

_UNSEEN_QUOTA_WORDING = "provider halted the attempt: reason zq-17 (unrecognized)"


def _quota_stopped_attempt() -> AgentInvocationError:
    exc = AgentInvocationError("claude", 0, _UNSEEN_QUOTA_WORDING)
    exc.facts = AttemptFacts(
        termination=TerminationRecord.create(TerminationIssuer.QUOTA_DETECTOR)
    )
    return exc


def test_unseen_wording_with_typed_facts_matches_the_old_text_path_intent() -> None:
    fact_intent = resolve_retry_intent(
        _quota_stopped_attempt(),
        phase="development",
        agent="claude",
        session_id="sess-1",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    text_intent = resolve_retry_intent(
        AgentInvocationError("claude", 1, "You've hit your session limit"),
        phase="development",
        agent="claude",
        session_id="sess-1",
        inactivity_error_type=AgentInactivityTimeoutError,
    )

    assert fact_intent is not None
    assert text_intent is not None
    assert fact_intent.skip_same_agent_retries is True
    assert fact_intent.skip_same_agent_retries == text_intent.skip_same_agent_retries
    assert fact_intent.action == text_intent.action
    assert fact_intent.failed_agent_name == text_intent.failed_agent_name


def test_unseen_wording_with_typed_facts_is_not_retryable() -> None:
    reason = retryable_agent_failure_reason(
        _quota_stopped_attempt(), AgentInactivityTimeoutError
    )

    assert reason is None


def test_unseen_wording_without_facts_keeps_the_prior_route_behavior() -> None:
    exc = AgentInvocationError("claude", 0, _UNSEEN_QUOTA_WORDING)

    assert retryable_agent_failure_reason(exc, AgentInactivityTimeoutError) is None
    intent = resolve_retry_intent(
        exc,
        phase="development",
        agent="claude",
        session_id="sess-1",
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    assert intent is None


def test_non_quota_typed_facts_do_not_suppress_retryable_reasons() -> None:
    exc = AgentInvocationError("claude", 1, "session not found: sess-9")
    exc.facts = AttemptFacts(
        termination=TerminationRecord.create(TerminationIssuer.AGENT_EXIT)
    )

    intent = resolve_retry_intent(
        exc,
        phase="development",
        agent="claude",
        session_id="sess-9",
        inactivity_error_type=AgentInactivityTimeoutError,
    )

    assert intent is not None
    assert intent.skip_same_agent_retries is False
