from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
    has_return_code_discrepancy,
)
from ralph.recovery.attribution import Attribution, AttributionVerdict
from ralph.recovery.canary_probe import ProbeOutcome

__all__ = [
    "DeadAgentVerdict",
    "PersistedVerdict",
    "TypedSignal",
    "authoritative_typed_signal_convicts_agent",
    "decide_dead_agent_verdict",
    "probe_outcome_verdict",
    "runtime_issued_termination_is_not_agent_fault",
    "task_or_environment_attribution_is_not_agent_fault",
    "verdict_benches_agent",
    "verdict_counts_toward_agent_state",
]

_REQUIRED_DEAD_AGENT_VERDICT_VALUES: Final[tuple[str, ...]] = (
    "agent_dead",
    "healthy",
    "not_agent_fault",
    "unknown",
)

_RUNTIME_ISSUERS: Final[frozenset[TerminationIssuer]] = frozenset(
    {
        TerminationIssuer.QUOTA_DETECTOR,
        TerminationIssuer.SUPERVISOR_IDLE,
        TerminationIssuer.RECOVERY_TIMEOUT,
        TerminationIssuer.OPERATOR,
    }
)


class _DeadAgentVerdict(StrEnum):
    AGENT_DEAD = "agent_dead"
    HEALTHY = "healthy"
    NOT_AGENT_FAULT = "not_agent_fault"
    UNKNOWN = "unknown"


if tuple(verdict.value for verdict in _DeadAgentVerdict) != _REQUIRED_DEAD_AGENT_VERDICT_VALUES:
    raise RuntimeError(
        "DeadAgentVerdict must expose exactly agent_dead, healthy, not_agent_fault, unknown"
    )


@dataclass(frozen=True)
class _TypedSignal:
    name: str
    provider_originated: bool = False
    agent_initiated: bool = False


@dataclass(frozen=True)
class _PersistedVerdict:
    verdict: _DeadAgentVerdict
    facts: AttemptFacts
    attribution: AttributionVerdict
    probe_outcome: ProbeOutcome | None
    termination: TerminationRecord | None
    typed_signal: _TypedSignal | None
    return_code_discrepancy: bool


DeadAgentVerdict = _DeadAgentVerdict
TypedSignal = _TypedSignal
PersistedVerdict = _PersistedVerdict


def runtime_issued_termination_is_not_agent_fault(facts: AttemptFacts) -> bool:
    termination = facts.termination
    if termination is None:
        return False
    return termination.issuer in _RUNTIME_ISSUERS


def task_or_environment_attribution_is_not_agent_fault(
    attribution: AttributionVerdict,
    probe_outcome: ProbeOutcome | None = None,
) -> bool:
    if attribution.attribution is not Attribution.TASK_OR_ENVIRONMENT:
        return False
    return probe_outcome is not ProbeOutcome.PASSED


def authoritative_typed_signal_convicts_agent(typed_signal: TypedSignal | None) -> bool:
    if typed_signal is None:
        return False
    return typed_signal.provider_originated and typed_signal.agent_initiated


def probe_outcome_verdict(probe_outcome: ProbeOutcome | None) -> DeadAgentVerdict:
    if probe_outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK:
        return DeadAgentVerdict.AGENT_DEAD
    if probe_outcome is ProbeOutcome.PASSED:
        return DeadAgentVerdict.HEALTHY
    return DeadAgentVerdict.UNKNOWN


def decide_dead_agent_verdict(
    facts: AttemptFacts,
    attribution: AttributionVerdict,
    probe_outcome: ProbeOutcome | None,
    typed_signal: TypedSignal | None = None,
) -> PersistedVerdict:
    return PersistedVerdict(
        verdict=_decide_verdict(facts, attribution, probe_outcome, typed_signal),
        facts=facts,
        attribution=attribution,
        probe_outcome=probe_outcome,
        termination=facts.termination,
        typed_signal=typed_signal,
        return_code_discrepancy=has_return_code_discrepancy(facts.termination),
    )


def _decide_verdict(
    facts: AttemptFacts,
    attribution: AttributionVerdict,
    probe_outcome: ProbeOutcome | None,
    typed_signal: TypedSignal | None,
) -> DeadAgentVerdict:
    if runtime_issued_termination_is_not_agent_fault(facts):
        return DeadAgentVerdict.NOT_AGENT_FAULT
    if task_or_environment_attribution_is_not_agent_fault(attribution, probe_outcome):
        return DeadAgentVerdict.NOT_AGENT_FAULT
    if authoritative_typed_signal_convicts_agent(typed_signal):
        return DeadAgentVerdict.AGENT_DEAD
    return probe_outcome_verdict(probe_outcome)


def verdict_benches_agent(verdict: DeadAgentVerdict) -> bool:
    return verdict is DeadAgentVerdict.AGENT_DEAD


def verdict_counts_toward_agent_state(verdict: DeadAgentVerdict) -> bool:
    return verdict is not DeadAgentVerdict.NOT_AGENT_FAULT
