"""Classify Ralph-origin faults that must fail a conflict attempt, not feed the agent."""

from __future__ import annotations

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)

_TRANSPORT_LOOP = "transport_loop_detected"
_RELAY_FAULT = "SUPERVISION_INFRASTRUCTURE_FAILURE"

__all__ = [
    "INFRASTRUCTURE_TERMINATION_REASONS",
    "classify_ralph_origin_fault",
    "ralph_origin_counts_as_liveness",
]

INFRASTRUCTURE_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = frozenset(
    {
        ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        ResolutionTerminationReason.REPEATED_IDENTICAL_FAILURE,
    }
)


def classify_ralph_origin_fault(payload: str) -> ResolutionTerminationReason | None:
    """Return a typed attempt failure when ``payload`` is Ralph's own fault text."""
    text = payload.strip()
    if not text:
        return None
    lowered = text.lower()
    if _TRANSPORT_LOOP in lowered:
        return ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED
    if _RELAY_FAULT in text or "activity relay" in lowered:
        return ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    if "repeated identical" in lowered:
        return ResolutionTerminationReason.REPEATED_IDENTICAL_FAILURE
    if "tool service" in lowered or "tool surface" in lowered:
        return ResolutionTerminationReason.TOOL_SURFACE_DEAD
    return None


def ralph_origin_counts_as_liveness(payload: str) -> bool:
    """Ralph-origin error text must never reset idle or progress clocks."""
    return classify_ralph_origin_fault(payload) is None
