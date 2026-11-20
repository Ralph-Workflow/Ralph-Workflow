from __future__ import annotations

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)

__all__ = [
    "BUDGET_UNCHARGED_TERMINATION_REASONS",
    "INFRASTRUCTURE_TERMINATION_REASONS",
]

INFRASTRUCTURE_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = frozenset(
    {
        ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        ResolutionTerminationReason.CANDIDATE_UNAVAILABLE,
    }
)


BUDGET_UNCHARGED_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = (
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
