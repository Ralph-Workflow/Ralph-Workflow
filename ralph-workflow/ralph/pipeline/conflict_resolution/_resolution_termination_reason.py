"""Typed terminal reasons for a conflict-resolution attempt."""

from __future__ import annotations

from enum import StrEnum


class ResolutionTerminationReason(StrEnum):
    """Reason a resolver returned control to its conflict owner."""

    CONFLICT_INACTIVITY = "CONFLICT_INACTIVITY"
    OPERATOR_CAP_REACHED = "OPERATOR_CAP_REACHED"
    SUPERVISION_INFRASTRUCTURE_FAILURE = "SUPERVISION_INFRASTRUCTURE_FAILURE"
    TRANSPORT_LOOP_DETECTED = "TRANSPORT_LOOP_DETECTED"
    TOOL_SURFACE_DEAD = "TOOL_SURFACE_DEAD"
    REPEATED_IDENTICAL_FAILURE = "REPEATED_IDENTICAL_FAILURE"
    NO_PROGRESS_OBSERVED = "NO_PROGRESS_OBSERVED"
    OUT_OF_REACH = "OUT_OF_REACH"
    CANDIDATE_DECLINED = "CANDIDATE_DECLINED"
    EXCEPTION = "EXCEPTION"


__all__ = ["ResolutionTerminationReason"]
