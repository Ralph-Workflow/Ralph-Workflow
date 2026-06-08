"""Shared retry-decision core for failed agent invocations.

There is exactly ONE place where both the pipeline executor
(`_invoke_agent_with_recovery`) and the direct-MCP recovery loop
(`run_with_direct_mcp_recovery`) decide whether a failed attempt is retryable
and what the canonical next-attempt intent is. Routing both callers through
`resolve_retry_intent` makes the retry semantics impossible to drift apart.

drift-audit: This module owns the recovery-decision pipeline seam — the
ONLY `FailureClassifier(` site in `ralph/pipeline/`. The 8-file allowlist
is INVARIANT (8 files; 5 actual sites). When extending the recovery
decision surface, do NOT add a 6th `FailureClassifier(` site here — the
seam is already a single owner. New callers MUST route through
`should_reset_tool_registry(...)` (ralph/recovery/failure_classifier.py)
for classification, and through `resolve_retry_intent(...)` (this
module) for the canonical next-attempt intent. PA-003 procedure: pin
counts are invariant — do not raise them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.agent_retry_intent import agent_retry_intent_for_failure
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.recovery.failure_classifier import FailureClassifier

if TYPE_CHECKING:
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent


def resolve_retry_intent(
    exc: Exception,
    *,
    phase: str,
    agent: str | None,
    session_id: str | None,
    inactivity_error_type: type[Exception],
) -> AgentRetryIntent | None:
    """Return the canonical retry intent for a failed attempt, or None.

    None means the failure is not retryable. Otherwise the returned intent is the
    single source of truth for the next attempt's session action and
    tool-registry reset. ``session_id`` is the caller-resolved observed session id
    (the intent clears it when the failure semantics demand a fresh session).
    """
    if retryable_agent_failure_reason(exc, inactivity_error_type) is None:
        return None
    classified = FailureClassifier().classify(exc, phase=phase, agent=agent)
    return agent_retry_intent_for_failure(
        failure_reason=str(exc),
        session_id=session_id,
        reset_tool_registry=classified.reset_tool_registry,
    )


__all__ = ["resolve_retry_intent"]
