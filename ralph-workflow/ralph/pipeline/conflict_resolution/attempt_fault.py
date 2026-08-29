"""Classify Ralph-origin faults that must fail a conflict attempt, not feed the agent."""

from __future__ import annotations

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)

_TRANSPORT_LOOP = "transport_loop_detected"
_RELAY_FAULT = "SUPERVISION_INFRASTRUCTURE_FAILURE"

__all__ = [
    "INFRASTRUCTURE_TERMINATION_REASONS",
    "RESOLVER_NOT_SPENT_TERMINATION_REASONS",
    "classify_ralph_origin_fault",
    "ralph_origin_counts_as_liveness",
]

INFRASTRUCTURE_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = frozenset(
    {
        ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        ResolutionTerminationReason.REPEATED_IDENTICAL_FAILURE,
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
            ResolutionTerminationReason.OPERATOR_CAP_REACHED,
            ResolutionTerminationReason.CANDIDATE_EXITED,
            ResolutionTerminationReason.OUT_OF_REACH,
        }
    )
)


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


def ralph_origin_counts_as_liveness(payload: str) -> bool:
    """Ralph-origin error text must never reset idle or progress clocks."""
    return classify_ralph_origin_fault(payload) is None
