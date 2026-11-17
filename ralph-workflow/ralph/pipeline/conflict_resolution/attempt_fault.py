from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.classifier import FailureContext
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.unavailability_reason import AGENT_EXIT_UNAVAILABILITY_REASONS

if TYPE_CHECKING:
    from ralph.recovery.classified_failure import ClassifiedFailure

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

_REASON_TO_OUTCOME: dict[ResolutionTerminationReason, ConflictAttemptOutcome] = {
    ResolutionTerminationReason.CANDIDATE_EXITED: ConflictAttemptOutcome.NO_ACTIVITY,
    ResolutionTerminationReason.ATTEMPT_FAILED: ConflictAttemptOutcome.ATTEMPT_FAILED,
    ResolutionTerminationReason.CANDIDATE_EXHAUSTED: ConflictAttemptOutcome.ATTEMPT_FAILED,
}

_LOCAL_REASON_TO_FAULT: dict[ResolutionTerminationReason, AttemptFault] = {
    ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED: AttemptFault.AGENT_INFRASTRUCTURE_FAILURE,
    ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE: (
        AttemptFault.AGENT_INFRASTRUCTURE_FAILURE
    ),
    ResolutionTerminationReason.TOOL_SURFACE_DEAD: AttemptFault.AGENT_TOOL_DEAD,
    ResolutionTerminationReason.CANDIDATE_UNAVAILABLE: AttemptFault.AGENT_TOOL_DEAD,
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


def _fault_from_classified(classified: ClassifiedFailure) -> AttemptFault:
    if classified.reset_tool_registry:
        return AttemptFault.AGENT_TOOL_DEAD
    if classified.unavailability_reason in AGENT_EXIT_UNAVAILABILITY_REASONS:
        return AttemptFault.AGENT_EXITED
    if classified.category is FailureCategory.ENVIRONMENTAL:
        return AttemptFault.AGENT_INFRASTRUCTURE_FAILURE
    return AttemptFault.UNKNOWN


def _classify_via_shared_outcome(outcome: ConflictAttemptOutcome) -> AttemptFault:
    classified = FailureContext(
        phase=PHASE_RESOLUTION,
        agent=None,
        outcome=outcome,
    ).classified_failure
    if classified is None:
        return AttemptFault.UNKNOWN
    return _fault_from_classified(classified)


def classify_attempt_fault(
    evidence: object = None,
) -> AttemptFault:
    if isinstance(evidence, AttemptFault):
        return evidence
    if evidence is None:
        return AttemptFault.AGENT_HEALTHY
    if not isinstance(evidence, ResolutionTerminationReason):
        return AttemptFault.UNKNOWN
    outcome = _REASON_TO_OUTCOME.get(evidence)
    if outcome is not None:
        return _classify_via_shared_outcome(outcome)
    return _LOCAL_REASON_TO_FAULT.get(evidence, AttemptFault.UNKNOWN)


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
