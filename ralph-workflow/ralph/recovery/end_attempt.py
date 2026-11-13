from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.attribution import Attribution, attribute_attempt
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    TypedSignal,
    decide_dead_agent_verdict,
)

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import AttemptFacts
    from ralph.recovery.canary_probe import ProbeOutcome

__all__ = ("end_attempt",)


def end_attempt(
    facts: AttemptFacts,
    *,
    attempt_failed: bool,
    typed_signal: TypedSignal | None = None,
    probe_outcome: ProbeOutcome | None = None,
    peer_failures_identical: bool = False,
    retryable_reason: str | None = None,
) -> AttemptOutcome:
    if not attempt_failed and facts.artifact_valid:
        return AttemptOutcome(
            kind=AttemptOutcomeKind.COMPLETED,
            issuer=None,
            verdict=None,
            reason="completed",
        )

    attribution = attribute_attempt(
        facts,
        peer_failures_identical=peer_failures_identical,
        typed_signal_observed=typed_signal is not None,
        typed_signal_provider_originated=bool(
            typed_signal is not None and typed_signal.provider_originated
        ),
    )
    verdict = decide_dead_agent_verdict(facts, attribution, probe_outcome, typed_signal)

    if verdict.verdict is DeadAgentVerdict.AGENT_DEAD:
        return AttemptOutcome(
            kind=AttemptOutcomeKind.AGENT_DEAD,
            issuer=None,
            verdict=verdict,
            reason=attribution.reason,
        )
    if attribution.attribution is Attribution.RUNTIME:
        termination = facts.termination
        if termination is None:
            raise RuntimeError("runtime attribution requires a termination record")
        return AttemptOutcome(
            kind=AttemptOutcomeKind.RUNTIME_ENDED,
            issuer=termination.issuer,
            verdict=verdict,
            reason=attribution.reason,
        )
    if retryable_reason is not None:
        return AttemptOutcome(
            kind=AttemptOutcomeKind.RETRYABLE_FAILURE,
            issuer=None,
            verdict=verdict,
            reason=retryable_reason,
        )
    if attribution.attribution is Attribution.TASK_OR_ENVIRONMENT:
        kind = (
            AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE
            if facts.artifact_valid or peer_failures_identical
            else AttemptOutcomeKind.UNKNOWN
        )
        return AttemptOutcome(
            kind=kind,
            issuer=None,
            verdict=verdict,
            reason=attribution.reason,
        )
    return AttemptOutcome(
        kind=AttemptOutcomeKind.UNKNOWN,
        issuer=None,
        verdict=verdict,
        reason=attribution.reason,
    )
