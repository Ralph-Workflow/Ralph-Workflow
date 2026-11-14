from __future__ import annotations

from enum import StrEnum

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)

_TRANSPORT_LOOP = "transport_loop_detected"
_RELAY_FAULT = "SUPERVISION_INFRASTRUCTURE_FAILURE"


class AttemptFault(StrEnum):
    AGENT_HEALTHY = "AGENT_HEALTHY"
    AGENT_EXITED = "AGENT_EXITED"
    AGENT_INFRASTRUCTURE_FAILURE = "AGENT_INFRASTRUCTURE_FAILURE"
    AGENT_TOOL_DEAD = "AGENT_TOOL_DEAD"
    UNKNOWN = "UNKNOWN"


__all__ = [
    "INFRASTRUCTURE_TERMINATION_REASONS",
    "RESOLVER_NOT_SPENT_TERMINATION_REASONS",
    "AttemptFault",
    "attempt_fault_to_termination_reason",
    "classify_attempt_fault",
    "classify_ralph_origin_fault",
    "ralph_origin_counts_as_liveness",
]

INFRASTRUCTURE_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = frozenset(
    {
        ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        ResolutionTerminationReason.CANDIDATE_UNAVAILABLE,
    }
)


RESOLVER_NOT_SPENT_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = (
    INFRASTRUCTURE_TERMINATION_REASONS
    | frozenset(
        {
            ResolutionTerminationReason.EXCEPTION,
            ResolutionTerminationReason.ATTEMPT_FAILED,
            ResolutionTerminationReason.OPERATOR_CAP_REACHED,
            ResolutionTerminationReason.CANDIDATE_EXITED,
            ResolutionTerminationReason.OUT_OF_REACH,
            ResolutionTerminationReason.NO_RESOLVER_CONFIGURED,
            ResolutionTerminationReason.PROMPT_UNAVAILABLE,
            ResolutionTerminationReason.CONFIG_FAILURE,
            ResolutionTerminationReason.CONTEXT_FAILURE,
        }
    )
)

_REASON_TO_FAULT: dict[ResolutionTerminationReason, AttemptFault] = {
    ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED: AttemptFault.AGENT_INFRASTRUCTURE_FAILURE,
    ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE: (
        AttemptFault.AGENT_INFRASTRUCTURE_FAILURE
    ),
    ResolutionTerminationReason.TOOL_SURFACE_DEAD: AttemptFault.AGENT_TOOL_DEAD,
    ResolutionTerminationReason.CANDIDATE_UNAVAILABLE: AttemptFault.AGENT_TOOL_DEAD,
    ResolutionTerminationReason.CANDIDATE_EXITED: AttemptFault.AGENT_EXITED,
    ResolutionTerminationReason.ATTEMPT_FAILED: AttemptFault.UNKNOWN,
    ResolutionTerminationReason.CANDIDATE_EXHAUSTED: AttemptFault.UNKNOWN,
}

_FAULT_TO_REASON: dict[AttemptFault, ResolutionTerminationReason | None] = {
    AttemptFault.AGENT_HEALTHY: None,
    AttemptFault.AGENT_EXITED: ResolutionTerminationReason.CANDIDATE_EXITED,
    AttemptFault.AGENT_INFRASTRUCTURE_FAILURE: (
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    ),
    AttemptFault.AGENT_TOOL_DEAD: ResolutionTerminationReason.TOOL_SURFACE_DEAD,
    AttemptFault.UNKNOWN: ResolutionTerminationReason.ATTEMPT_FAILED,
}


def classify_ralph_origin_fault(payload: str) -> ResolutionTerminationReason | None:
    text = payload.strip()
    if not text:
        return None
    lowered = text.lower()
    if _TRANSPORT_LOOP in lowered:
        return ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    if _RELAY_FAULT in text:
        return ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    return None


def classify_attempt_fault(
    evidence: AttemptFault | ResolutionTerminationReason | None = None,
) -> AttemptFault:
    if isinstance(evidence, AttemptFault):
        return evidence
    if evidence is None:
        return AttemptFault.AGENT_HEALTHY
    return _REASON_TO_FAULT.get(evidence, AttemptFault.UNKNOWN)


def attempt_fault_to_termination_reason(
    fault: AttemptFault,
    evidence: str | None = None,
) -> ResolutionTerminationReason | None:
    if fault is AttemptFault.AGENT_INFRASTRUCTURE_FAILURE and evidence is not None:
        origin = classify_ralph_origin_fault(evidence)
        if origin is not None:
            return origin
    return _FAULT_TO_REASON.get(fault, ResolutionTerminationReason.ATTEMPT_FAILED)


def ralph_origin_counts_as_liveness(payload: str) -> bool:
    return classify_ralph_origin_fault(payload) is None
