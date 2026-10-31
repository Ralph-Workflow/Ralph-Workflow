"""Classify Ralph-origin faults that must fail a conflict attempt, not feed the agent."""

from __future__ import annotations

from enum import StrEnum

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)

_TRANSPORT_LOOP = "transport_loop_detected"
_RELAY_FAULT = "SUPERVISION_INFRASTRUCTURE_FAILURE"


class AttemptFault(StrEnum):
    """Typed classification of a conflict-resolution attempt fault."""

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


#: Reasons that are a failure of something OTHER than the resolver: a
#: launch that never happened, a name the registry could not produce, an
#: operator's own wall-clock cap, Ralph's infrastructure. None of them
#: means the agents spent the chain, so none may be written down as an
#: exhausted resolver -- that record says the AGENTS gave up, and the
#: next seam reads it and refuses to try a conflict none of them ever
#: got to attempt. Anything NOT listed here is treated as genuinely
#: spent: an unrecognised reason must escalate honestly rather than
#: silently buy another lap.
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
    """Return a typed attempt failure when ``payload`` is Ralph's own fault text.

    Every marker here must be a token Ralph itself EMITS, never an
    English phrase that describes one. This payload is scanned from the
    resolver's activity events, which carry the agent's own output, and
    a match permanently records that agent as a dead tool surface for
    the rest of the rebase. Prose markers made that a trap: an agent
    resolving a conflict in this very repository reads and echoes the
    words "tool surface" constantly, and the only resolver in the
    shipped one-agent chain was then killed for quoting a comment --
    after which every later round and every later stop had nobody left
    to invoke. ``transport_loop_detected`` (the MCP breaker's 503 frame)
    and ``SUPERVISION_INFRASTRUCTURE_FAILURE`` (the activity relay's
    own error text) are real strings Ralph writes; "tool service",
    "tool surface" and "repeated identical" appear nowhere in anything
    Ralph emits, so they could only ever match the agent quoting us.
    """
    text = payload.strip()
    if not text:
        return None
    lowered = text.lower()
    if _TRANSPORT_LOOP in lowered:
        return ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    if _RELAY_FAULT in text:
        return ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    return None


def _classify_text_attempt_fault(text: str) -> AttemptFault:
    try:
        return _REASON_TO_FAULT.get(ResolutionTerminationReason(text), AttemptFault.UNKNOWN)
    except ValueError:
        pass

    if classify_ralph_origin_fault(text) is not None:
        return AttemptFault.AGENT_INFRASTRUCTURE_FAILURE
    lowered = text.lower()
    if any(
        k in lowered
        for k in (
            "tool_surface_dead",
            "tool surface dead",
            "candidate_unavailable",
            "candidate unavailable",
        )
    ):
        return AttemptFault.AGENT_TOOL_DEAD
    if any(k in lowered for k in ("unknown", "attempt_failed", "attempt failed")):
        return AttemptFault.UNKNOWN
    return AttemptFault.AGENT_EXITED


def classify_attempt_fault(evidence: object = None) -> AttemptFault:
    """Classify resolution attempt failure evidence into a typed AttemptFault."""
    if isinstance(evidence, AttemptFault):
        return evidence
    if evidence is None:
        return AttemptFault.AGENT_HEALTHY
    if isinstance(evidence, ResolutionTerminationReason):
        return _REASON_TO_FAULT.get(evidence, AttemptFault.UNKNOWN)

    text = str(evidence).strip()
    if not text or text.lower() == "healthy":
        return AttemptFault.AGENT_HEALTHY

    try:
        return AttemptFault(text)
    except ValueError:
        pass

    return _classify_text_attempt_fault(text)


def attempt_fault_to_termination_reason(
    fault: AttemptFault,
    evidence: str | None = None,
) -> ResolutionTerminationReason | None:
    """Map an AttemptFault back to a ResolutionTerminationReason."""
    if fault is AttemptFault.AGENT_INFRASTRUCTURE_FAILURE and evidence is not None:
        origin = classify_ralph_origin_fault(evidence)
        if origin is not None:
            return origin
    return _FAULT_TO_REASON.get(fault, ResolutionTerminationReason.ATTEMPT_FAILED)


def ralph_origin_counts_as_liveness(payload: str) -> bool:
    """Ralph-origin error text must never reset idle or progress clocks."""
    return classify_ralph_origin_fault(payload) is None
