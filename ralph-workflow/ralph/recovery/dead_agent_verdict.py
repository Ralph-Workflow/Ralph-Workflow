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
from ralph.recovery.attribution import Attribution, AttributionVerdict, liveness_observed
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
    "verdict_record",
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
        TerminationIssuer.POST_EXIT_WATCHDOG,
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
    if termination.issuer is TerminationIssuer.QUOTA_DETECTOR and not liveness_observed(facts):
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


def _facts_record(facts: AttemptFacts) -> dict[str, object]:
    return {
        "event_kinds": list(facts.event_kinds),
        "tool_calls": list(facts.tool_calls),
        "workspace_changed_by_tool": facts.workspace_changed_by_tool,
        "artifact_valid": facts.artifact_valid,
        "workspace_changed": facts.workspace_changed,
        "elapsed_to_terminal_ms": facts.elapsed_to_terminal_ms,
        "exit_status": facts.exit_status,
        "liveness_observed": facts.liveness_observed,
        "subagent_activity_observed": facts.subagent_activity_observed,
    }


def _attribution_record(attribution: AttributionVerdict) -> dict[str, object]:
    return {
        "attribution": str(attribution.attribution),
        "runtime_cause": str(attribution.runtime_cause)
        if attribution.runtime_cause is not None
        else None,
        "cleared": attribution.cleared,
        "counts_toward_agent_state": attribution.counts_toward_agent_state,
        "reason": attribution.reason,
    }


def _termination_record(termination: TerminationRecord | None) -> dict[str, object] | None:
    if termination is None:
        return None
    return {
        "issuer": str(termination.issuer),
        "observed_returncode": termination.observed_returncode,
        "reported_returncode": termination.reported_returncode,
        "liveness_observed": termination.liveness_observed,
        "return_code_discrepancy": termination.return_code_discrepancy,
    }


def _typed_signal_record(signal: TypedSignal | None) -> dict[str, object] | None:
    if signal is None:
        return None
    return {
        "name": signal.name,
        "provider_originated": signal.provider_originated,
        "agent_initiated": signal.agent_initiated,
    }


def verdict_record(phase: str, agent: str, verdict: PersistedVerdict) -> dict[str, object]:
    return {
        "phase": phase,
        "agent": agent,
        "verdict": str(verdict.verdict),
        "facts": _facts_record(verdict.facts),
        "attribution": _attribution_record(verdict.attribution),
        "probe_outcome": str(verdict.probe_outcome) if verdict.probe_outcome is not None else None,
        "termination": _termination_record(verdict.termination),
        "typed_signal": _typed_signal_record(verdict.typed_signal),
        "return_code_discrepancy": verdict.return_code_discrepancy,
    }
